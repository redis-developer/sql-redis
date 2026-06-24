"""SQL to Redis command translator."""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field

from sql_redis.analyzer import AnalyzedQuery, Analyzer
from sql_redis.parser import (
    SQL_TO_REDIS_DATE_FUNCTIONS,
    BoolGroup,
    BoolLeaf,
    Condition,
    GeoDistanceCondition,
    ParsedQuery,
    SQLParser,
    parse_date_to_timestamp,
)
from sql_redis.query_builder import QueryBuilder
from sql_redis.schema import AsyncSchemaRegistry, SchemaRegistry


def _fmt_num(value: float) -> str:
    """Format a numeric FT.HYBRID parameter without trailing float noise."""
    return f"{value:g}"


@dataclass
class TranslatedQuery:
    """Result of translating SQL to Redis."""

    command: str  # FT.SEARCH, FT.AGGREGATE, or FT.HYBRID
    index: str
    query_string: str
    args: list[str] = field(default_factory=list)
    params: dict[str, object] = field(default_factory=dict)  # Named parameters
    score_alias: str | None = None  # Alias for score column when WITHSCORES is used
    # FT.HYBRID has no single top-level query string (its SEARCH/VSIM legs live
    # in args), so it is rendered without the quoted query_string slot.
    is_hybrid: bool = False

    def to_command_list(self) -> list[str]:
        """Return as a list suitable for redis.execute_command()."""
        if self.is_hybrid:
            return [self.command, self.index, *self.args]
        return [self.command, self.index, self.query_string, *self.args]

    def to_command_string(self) -> str:
        """Return as a human-readable command string."""
        if self.is_hybrid:
            # Quote multi-word tokens (the SEARCH query and filter expressions)
            # for readability; execution uses to_command_list (raw tokens).
            rendered = [self.command, self.index]
            rendered.extend(f'"{tok}"' if " " in tok else tok for tok in self.args)
            return " ".join(rendered)
        parts = [self.command, self.index, f'"{self.query_string}"']
        parts.extend(self.args)
        return " ".join(parts)


class Translator:
    """Translates SQL queries to Redis FT.SEARCH/FT.AGGREGATE commands."""

    def __init__(self, schema_registry: SchemaRegistry | AsyncSchemaRegistry) -> None:
        """Initialize translator with schema registry.

        Args:
            schema_registry: Registry containing index schemas. Can be either
                sync (SchemaRegistry) or async (AsyncSchemaRegistry) - only
                the sync get_schema() method is used.
        """
        self._schema_registry = schema_registry
        self._parser = SQLParser()
        self._query_builder = QueryBuilder()

    def parse(self, sql: str) -> ParsedQuery:
        """Parse a SQL SELECT into a ParsedQuery AST.

        Useful when callers need the parsed result before translation
        (e.g., to extract the index name for async schema loading).

        Args:
            sql: SQL SELECT statement.

        Returns:
            ParsedQuery with extracted index, fields, conditions, etc.
        """
        return self._parser.parse(sql)

    def translate(self, sql: str) -> TranslatedQuery:
        """Translate a SQL SELECT into a Redis search command.

        Args:
            sql: SQL SELECT statement.

        Returns:
            TranslatedQuery with command details.

        Raises:
            ValueError: If SQL is invalid or references unknown index/fields.
        """
        parsed = self._parser.parse(sql)
        return self.translate_parsed(parsed)

    def translate_parsed(self, parsed: ParsedQuery) -> TranslatedQuery:
        """Translate a pre-parsed query into a Redis search command.

        This avoids re-parsing SQL when the caller has already parsed it
        (e.g., AsyncExecutor extracts the index name before translation).

        Args:
            parsed: A ParsedQuery from SQLParser.parse().

        Returns:
            TranslatedQuery with command details.

        Raises:
            ValueError: If the index or a field is unknown.
        """
        # Get schema and analyze — raise early for missing indexes
        schema = self._schema_registry.get_schema(parsed.index)
        if not schema:
            raise ValueError(f"Unknown index: {parsed.index}")
        schemas = {parsed.index: schema}
        analyzer = Analyzer(schemas)
        analyzed = analyzer.analyze(parsed)

        # Build query
        return self._build_command(analyzed)

    def _build_command(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
        """Build the Redis command from analyzed query."""
        parsed = analyzed.parsed

        # FT.HYBRID fusion is a dedicated command path with its own
        # SEARCH/VSIM/COMBINE layout, distinct from FT.SEARCH/FT.AGGREGATE.
        if analyzed.hybrid_search is not None:
            return self._build_hybrid(analyzed)

        # Validate: geo_distance cannot be combined with OR
        # Geo filters are applied as top-level command args (GEOFILTER/FILTER) and
        # are not part of the boolean expression. Combining with OR would change
        # semantics (e.g., `A OR geo_distance(...)` would become `(A) AND geo_filter`).
        # ``has_or_in_where`` is set by the parser whenever an OR appears in
        # WHERE, even when the boolean tree collapses (e.g., the OR's other
        # branch was a geo_distance predicate that produced no tree leaf).
        if parsed.geo_conditions and parsed.has_or_in_where:
            raise ValueError(
                "Geo distance predicates cannot be combined with OR; "
                "they are applied as top-level filters and would change query "
                "semantics. Rewrite the query to avoid OR with geo_distance."
            )

        # Check if any geo conditions require FT.AGGREGATE (>, >=, BETWEEN)
        geo_requires_aggregate = any(
            geo.operator in (">", ">=", "BETWEEN") for geo in parsed.geo_conditions
        )

        # Check for date function conditions in WHERE (require FT.AGGREGATE)
        has_date_func_conditions = any(
            self._is_date_function_condition(c) for c in parsed.conditions
        )

        # Validate: date function predicates cannot be combined with OR
        # Date filters are applied via FILTER clauses (ANDed with query).
        # Combining with OR would change semantics. Walk the tree to reject
        # mixing at any depth (e.g., `A AND (YEAR(x) = 2024 OR B)`), not just
        # when OR is the root operator.
        if has_date_func_conditions and self._tree_has_date_in_or(
            parsed.condition_tree
        ):
            raise ValueError(
                "Date function predicates cannot be combined with OR; "
                "they are applied as top-level filters and would change query "
                "semantics. Rewrite the query to avoid OR with date functions."
            )

        # Determine if we need FT.AGGREGATE
        # Multi-key ORDER BY also requires FT.AGGREGATE: FT.SEARCH SORTBY
        # accepts a single key, while FT.AGGREGATE SORTBY accepts multiple.
        # Routing automatically prevents the silent drop of trailing keys
        # that used to happen on the FT.SEARCH path.
        use_aggregate = (
            len(analyzed.aggregations) > 0
            or len(analyzed.groupby_fields) > 0
            or len(analyzed.computed_fields) > 0
            or len(parsed.geo_distance_selects) > 0  # geo_distance() in SELECT
            or geo_requires_aggregate  # geo_distance with >, >=, BETWEEN
            or len(analyzed.date_functions) > 0
            or has_date_func_conditions
            or len(parsed.filters) > 0  # exists() in HAVING → FILTER
            or len(parsed.orderby_fields) > 1  # multi-key ORDER BY
        )

        # Build query string from conditions
        query_string = self._build_query_string(analyzed)

        if use_aggregate:
            if parsed.scoring is not None:
                raise ValueError(
                    "score() is not supported with FT.AGGREGATE queries. "
                    "WITHSCORES / SCORER are FT.SEARCH-only features. "
                    "Remove score() or avoid GROUP BY / aggregation functions "
                    "in the same query."
                )
            return self._build_aggregate(analyzed, query_string)
        else:
            return self._build_search(analyzed, query_string)

    def _build_query_string(self, analyzed: AnalyzedQuery) -> str:
        """Build the RediSearch query string from conditions.

        Walks the boolean tree built by the parser so that mixed AND/OR
        expressions like ``A AND (B OR C)`` keep their original grouping
        instead of collapsing onto a single boolean operator.
        """
        parsed = analyzed.parsed

        # Render the boolean tree (with proper RediSearch parenthesization)
        # if one was built by the parser. Date-function leaves are skipped —
        # they are emitted as FILTER args by the FT.AGGREGATE path.
        if parsed.condition_tree is not None:
            combined = self._render_bool_tree(parsed.condition_tree, analyzed) or ""
        else:
            combined = ""

        if not combined and not analyzed.vector_search:
            return "*"

        # Handle vector search with prefilter
        if analyzed.vector_search:
            vs = analyzed.vector_search
            # Vector search uses KNN syntax
            if analyzed.has_prefilter and combined:
                # Prefilter: (filter)=>[KNN k @field $vec]
                return f"({combined})=>[KNN {vs.k} @{vs.field} $vector AS {vs.alias}]"
            else:
                # Pure KNN: *=>[KNN k @field $vec]
                return f"*=>[KNN {vs.k} @{vs.field} $vector AS {vs.alias}]"

        return combined

    def _render_bool_tree(self, node, analyzed: AnalyzedQuery) -> str | None:
        """Recursively render a BoolLeaf/BoolGroup tree to a query string.

        Date-function leaves are dropped (handled via FILTER in FT.AGGREGATE).
        OR groups are wrapped in parentheses so that, when nested inside an
        AND group, RediSearch's higher AND precedence does not silently
        re-associate the expression. Returns None for an empty tree (e.g.,
        a group that contained only date-function leaves).
        """
        if isinstance(node, BoolLeaf):
            condition = node.condition
            if self._is_date_function_condition(condition):
                return None
            field_type = analyzed.get_field_type(condition.field)
            return self._build_condition(condition, field_type)
        if isinstance(node, BoolGroup):
            rendered = [
                r
                for r in (self._render_bool_tree(c, analyzed) for c in node.children)
                if r
            ]
            if not rendered:
                return None
            if len(rendered) == 1:
                return rendered[0]
            if node.operator == "OR":
                return "(" + "|".join(rendered) + ")"
            # AND: space-joined; RediSearch gives AND higher precedence than OR
            # so child OR groups (already wrapped in parens above) keep grouping.
            return " ".join(rendered)
        return None

    def _tree_has_date_in_or(self, node, in_or: bool = False) -> bool:
        """Return True if any date-function leaf is reachable through an OR.

        Walks the boolean tree and returns True as soon as a date-function
        condition is found beneath an OR ancestor — used to reject
        ``A OR YEAR(x) = 2024`` and similar mixes that the FT.AGGREGATE
        FILTER path cannot represent.
        """
        if isinstance(node, BoolLeaf):
            return in_or and self._is_date_function_condition(node.condition)
        if isinstance(node, BoolGroup):
            now_in_or = in_or or node.operator == "OR"
            return any(self._tree_has_date_in_or(c, now_in_or) for c in node.children)
        return False

    def _build_condition(self, condition: Condition, field_type: str | None) -> str:
        """Build a single condition string based on field type."""
        # Short-circuit for IS NULL / IS NOT NULL → ismissing()
        if condition.operator in ("IS_NULL", "IS_NOT_NULL"):
            warnings.warn(
                f"IS NULL / IS NOT NULL on field '{condition.field}' requires "
                "Redis 7.4+ (RediSearch 2.10+) with INDEXMISSING declared on "
                "the field. Older versions will return a server error.",
                stacklevel=4,
            )
            return self._query_builder.build_missing_condition(
                condition.field, is_missing=(condition.operator == "IS_NULL")
            )

        # Reject text-only operators on non-TEXT fields — fuzzy() and fulltext()
        # only make sense for TEXT fields; silently falling through to TAG/NUMERIC
        # would produce incorrect queries.
        if condition.operator in ("FUZZY", "FULLTEXT", "LIKE") and field_type != "TEXT":
            op_display = (
                "LIKE"
                if condition.operator == "LIKE"
                else f"{condition.operator.lower()}()"
            )
            raise ValueError(
                f"{op_display} can only be used on TEXT fields, "
                f"but '{condition.field}' is {field_type or 'unknown'}."
            )

        # Resolve negation using XOR so that double negation cancels out.
        # e.g. NOT (field != 'x') → negated=True, op='!=' → is_negated=False.
        operator = condition.operator
        is_negated = condition.negated ^ (operator == "!=")
        # Normalize = / != to match the resolved negation state so every
        # downstream builder sees a consistent (operator, negated) pair.
        if operator in ("=", "!="):
            operator = "!=" if is_negated else "="

        if field_type == "TEXT":
            return self._query_builder.build_text_condition(
                condition.field,
                operator,
                str(condition.value),
                is_negated,
                fuzzy_level=condition.fuzzy_level,
                slop=condition.slop,
                inorder=condition.inorder,
            )
        elif field_type == "TAG":
            # BETWEEN is meaningless for TAG values; RediSearch tags have no
            # ordering, so 'a' <= status <= 'z' has no defined semantics.
            # Previously the parser fell through and the builder emitted
            # @status:{\('a'\, 'z'\)} (invalid). Surface the limitation.
            if operator == "BETWEEN":
                raise ValueError(
                    f"BETWEEN is not supported on TAG fields ('{condition.field}'); "
                    "TAG values are unordered. Use IN (...) for a set match."
                )
            # Keep list value for IN clauses, convert scalar to string
            value = (
                condition.value
                if isinstance(condition.value, list)
                else str(condition.value)
            )
            return self._query_builder.build_tag_condition(
                condition.field,
                operator,
                value,
                negated=is_negated,
            )
        elif field_type == "NUMERIC":
            # IN (...) on a NUMERIC field was previously handed a list value
            # to build_numeric_condition, which then tried float([1,2,3]) and
            # crashed. RediSearch has no native IN for NUMERIC; expand to a
            # union of equality ranges (negated → AND of NOT-equals).
            if operator == "IN":
                if not isinstance(condition.value, list) or not condition.value:
                    raise ValueError(
                        f"IN on NUMERIC field '{condition.field}' requires a "
                        "non-empty list of values."
                    )
                parts: list[str] = []
                for item in condition.value:
                    item_num = self._convert_to_numeric(item)
                    parts.append(
                        self._query_builder.build_numeric_condition(
                            condition.field, "=", item_num, negated=is_negated
                        )
                    )
                if len(parts) == 1:
                    return parts[0]
                # NOT IN (...) → AND of negated equalities (De Morgan)
                # IN (...) → OR of equalities
                joiner = " " if is_negated else "|"
                joined = joiner.join(parts)
                return f"({joined})"

            # Cast value to expected type for numeric conditions
            numeric_value: int | float | tuple[int | float, int | float]
            if isinstance(condition.value, tuple):
                # Handle tuple values (e.g., BETWEEN) - try date conversion for each
                low, high = condition.value
                low_val = self._convert_to_numeric(low)
                high_val = self._convert_to_numeric(high)
                numeric_value = (low_val, high_val)
            elif isinstance(condition.value, bool):
                raise ValueError(
                    f"Boolean value {condition.value!r} is not valid in a "
                    "numeric context. Use 1/0 instead of true/false for "
                    "numeric fields."
                )
            elif isinstance(condition.value, (int, float)):
                numeric_value = condition.value
            else:
                # Try date string conversion for NUMERIC fields
                numeric_value = self._convert_to_numeric(condition.value)
            return self._query_builder.build_numeric_condition(
                condition.field,
                operator,
                numeric_value,
                negated=is_negated,
            )
        else:
            # GEO, VECTOR, and unknown field types - default to text search
            return self._query_builder.build_text_condition(
                condition.field,
                operator,
                str(condition.value),
                condition.negated,
            )

    def _convert_to_numeric(self, value: object) -> int | float:
        """Convert a value to numeric, trying date string conversion if needed.

        Args:
            value: The value to convert. Can be int, float, or string (possibly a date).

        Returns:
            Numeric value (int or float).

        Raises:
            ValueError: If conversion fails.
        """
        if isinstance(value, bool):
            raise ValueError(
                f"Boolean value {value!r} is not valid in a numeric context. "
                "Use 1/0 instead of true/false for numeric fields."
            )
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            # Try date string to timestamp conversion first
            timestamp = parse_date_to_timestamp(value)
            if timestamp is not None:
                return timestamp
            # Fall back to float conversion
            return float(value)
        return float(value)  # type: ignore[arg-type]

    def _build_search(
        self, analyzed: AnalyzedQuery, query_string: str
    ) -> TranslatedQuery:
        """Build FT.SEARCH command."""
        parsed = analyzed.parsed
        args: list[str] = []
        params: dict[str, object] = {}

        # Handle vector search parameters
        if analyzed.vector_search:
            args.extend(["PARAMS", "2", "vector", "$vector"])
            params["vector"] = None  # Placeholder for vector bytes

        # GEOFILTER clause for geo_distance conditions (only < and <= operators)
        for geo_cond in parsed.geo_conditions:
            if geo_cond.operator in ("<", "<="):
                args.extend(self._build_geo_filter_args(geo_cond))

        # RETURN clause - include vector distance alias if present
        return_fields = list(parsed.fields) if parsed.fields else []
        if analyzed.vector_search and analyzed.vector_search.alias:
            # Add vector distance alias to return fields (like VectorQuery with return_score=True)
            if analyzed.vector_search.alias not in return_fields:
                return_fields.append(analyzed.vector_search.alias)

        # When score() is the only SELECT expression, parsed.fields is empty.
        # We still need a RETURN clause to avoid leaking full document payloads.
        # Score itself is delivered via WITHSCORES (not RETURN), but we must
        # emit RETURN 0 so Redis returns no document attributes beyond the score.
        score_only_select = parsed.scoring is not None and not return_fields

        if score_only_select:
            # RETURN 0 — suppress all document fields, score comes via WITHSCORES
            args.extend(["RETURN", "0"])
        elif return_fields and return_fields != ["*"]:
            args.append("RETURN")
            args.append(str(len(return_fields)))
            args.extend(return_fields)

        # SORTBY — skip if the ORDER BY field is a score() alias, because
        # WITHSCORES already returns results in relevance order and the alias
        # is not a sortable indexed field.
        # Multi-key ORDER BY is routed to FT.AGGREGATE upstream (see
        # use_aggregate in _build_command), so by the time we reach this
        # branch parsed.orderby_fields has at most one entry.
        score_alias_name = parsed.scoring.alias if parsed.scoring else None
        if parsed.orderby_fields:
            field_name, direction = parsed.orderby_fields[0]
            if field_name == score_alias_name:
                # score() alias — not a real field; RediSearch sorts by
                # relevance by default when no SORTBY is specified.
                if direction == "ASC":
                    raise ValueError(
                        f"ORDER BY {field_name} ASC is not supported: "
                        "RediSearch returns results in descending relevance "
                        "order by default and does not support ascending "
                        "score sorting via FT.SEARCH."
                    )
                # DESC is the default — omit SORTBY entirely
            else:
                args.extend(["SORTBY", field_name, direction])

        # LIMIT
        if parsed.limit is not None:
            offset = parsed.offset or 0
            args.extend(["LIMIT", str(offset), str(parsed.limit)])

        # Scoring — WITHSCORES and SCORER
        if parsed.scoring is not None:
            args.append("WITHSCORES")
            if parsed.scoring.scorer:
                args.extend(["SCORER", parsed.scoring.scorer])

        # DIALECT 2 — unconditionally appended as the last arguments
        args.extend(["DIALECT", "2"])

        return TranslatedQuery(
            command="FT.SEARCH",
            index=parsed.index,
            query_string=query_string,
            args=args,
            params=params,
            score_alias=(parsed.scoring.alias if parsed.scoring is not None else None),
        )

    def _build_hybrid(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
        """Build an FT.HYBRID command from a hybrid_vector_search() query.

        Layout: ``FT.HYBRID index SEARCH "<q>" [SCORER s] VSIM @field $vector
        [FILTER n <expr>] (KNN|RANGE ...) COMBINE (RRF|LINEAR ...) [LOAD ...]
        [LIMIT ...] PARAMS 2 vector $vector DIALECT 2``. The WHERE clause is
        applied to both legs: folded into the SEARCH query and emitted as the
        VSIM FILTER so the candidate sets agree.
        """
        parsed = analyzed.parsed
        hybrid = analyzed.hybrid_search
        assert hybrid is not None  # guaranteed by the caller's dispatch check
        spec = hybrid.spec
        args: list[str] = []

        # WHERE clause becomes the shared per-leg filter (reuses the standard
        # query-string builder; vector_search is unset on the hybrid path).
        filter_expr = self._build_query_string(analyzed)
        has_filter = bool(filter_expr) and filter_expr != "*"

        # SEARCH leg: tokenized text query, with the filter folded in.
        text_query = self._query_builder.build_text_condition(
            spec.text_field, "FULLTEXT", spec.text_query
        )
        search_query = f"({text_query}) ({filter_expr})" if has_filter else text_query
        args.extend(["SEARCH", search_query])
        if spec.text_scorer:
            args.extend(["SCORER", spec.text_scorer])

        # VSIM leg: vector field + param placeholder, then the KNN/RANGE method
        # clause, then the optional per-leg filter (the method must precede
        # FILTER in the VSIM grammar).
        args.extend(["VSIM", f"@{spec.vector_field}", "$vector"])
        if spec.vector_method == "RANGE":
            assert spec.radius is not None  # parser requires radius for RANGE
            method_tokens = ["RADIUS", _fmt_num(spec.radius)]
            if spec.epsilon is not None:
                method_tokens.extend(["EPSILON", _fmt_num(spec.epsilon)])
            args.extend(["RANGE", str(len(method_tokens)), *method_tokens])
        else:
            method_tokens = ["K", str(hybrid.k)]
            if spec.ef_runtime is not None:
                method_tokens.extend(["EF_RUNTIME", str(spec.ef_runtime)])
            args.extend(["KNN", str(len(method_tokens)), *method_tokens])
        if has_filter:
            args.extend(["FILTER", "1", filter_expr])

        # COMBINE: RRF (default) or LINEAR. The combined score is yielded under
        # the SELECT alias so it comes back as a column.
        combine_tokens: list[str] = []
        if spec.combine_method == "LINEAR":
            if spec.linear_alpha is not None:
                combine_tokens.extend(
                    [
                        "ALPHA",
                        _fmt_num(spec.linear_alpha),
                        "BETA",
                        _fmt_num(1 - spec.linear_alpha),
                    ]
                )
            if spec.rrf_window is not None:
                combine_tokens.extend(["WINDOW", str(spec.rrf_window)])
            combine_tokens.extend(["YIELD_SCORE_AS", spec.alias])
            args.extend(
                ["COMBINE", "LINEAR", str(len(combine_tokens)), *combine_tokens]
            )
        else:
            if spec.rrf_constant is not None:
                combine_tokens.extend(["CONSTANT", str(spec.rrf_constant)])
            if spec.rrf_window is not None:
                combine_tokens.extend(["WINDOW", str(spec.rrf_window)])
            combine_tokens.extend(["YIELD_SCORE_AS", spec.alias])
            args.extend(["COMBINE", "RRF", str(len(combine_tokens)), *combine_tokens])

        # LOAD: the SELECT columns (field names require an @ prefix).
        load_fields = [f for f in parsed.fields if f != "*"]
        if load_fields:
            args.extend(
                ["LOAD", str(len(load_fields)), *(f"@{f}" for f in load_fields)]
            )

        # LIMIT (final row cut; KNN K is set separately above).
        if parsed.limit is not None:
            args.extend(["LIMIT", str(parsed.offset or 0), str(parsed.limit)])

        # PARAMS placeholder — the executor injects the vector bytes for "vector".
        # Note: FT.HYBRID rejects an explicit DIALECT argument (the server uses
        # its configured search-default-dialect), so none is appended here.
        args.extend(["PARAMS", "2", "vector", "$vector"])

        return TranslatedQuery(
            command="FT.HYBRID",
            index=parsed.index,
            query_string="",
            args=args,
            params={"vector": None},
            score_alias=spec.alias,
            is_hybrid=True,
        )

    def _build_geo_filter_args(self, geo_cond: GeoDistanceCondition) -> list[str]:
        """Build GEOFILTER args from a GeoDistanceCondition."""
        return [
            "GEOFILTER",
            geo_cond.field,
            str(geo_cond.lon),
            str(geo_cond.lat),
            str(geo_cond.radius),
            geo_cond.unit,
        ]

    def _build_aggregate(
        self, analyzed: AnalyzedQuery, query_string: str
    ) -> TranslatedQuery:
        """Build FT.AGGREGATE command."""
        parsed = analyzed.parsed
        args: list[str] = []

        # Identify geo conditions that need FILTER in AGGREGATE path
        # All geo conditions need FILTER when using FT.AGGREGATE (including <, <=)
        geo_filter_conditions = list(parsed.geo_conditions)

        # LOAD fields if needed
        # SELECT * in aggregate mode → LOAD * (all document attributes)
        load_all = "*" in (parsed.fields or [])

        load_fields: set[str] = set()
        if not load_all:
            for agg in analyzed.aggregations:
                if agg.field:
                    load_fields.add(agg.field)
            for field_name in analyzed.groupby_fields:
                load_fields.add(field_name)
            # Load geo fields used in geo_distance() SELECT expressions
            for geo_select in parsed.geo_distance_selects:
                load_fields.add(geo_select.field)
            # Load geo fields used in geo_distance() WHERE with >, >=, BETWEEN
            for geo_cond in geo_filter_conditions:
                load_fields.add(geo_cond.field)
            # Load source fields for date functions in SELECT
            for date_func in analyzed.date_functions:
                load_fields.add(date_func.field)
            # Load source fields for date function conditions in WHERE
            for condition in parsed.conditions:
                if self._is_date_function_condition(condition):
                    load_fields.add(condition.field)
            # Load explicit SELECT fields for FT.AGGREGATE
            for field_name in parsed.fields:
                # Skip computed fields (they have aliases from geo_distance)
                if field_name not in [gs.alias for gs in parsed.geo_distance_selects]:
                    load_fields.add(field_name)
            # Load fields referenced in exists() filters (HAVING)
            for filter_expr in parsed.filters:
                self._extract_exists_fields(filter_expr, load_fields)
            # Load fields referenced in exists() computed fields (SELECT)
            for computed in analyzed.computed_fields:
                self._extract_exists_fields(computed.expression, load_fields)
            # Load ORDER BY fields so multi-key SORTBY works on non-SORTABLE
            # columns. (SORTABLE fields are already in scope; loading them
            # again is harmless.) Skip computed/derived aliases.
            computed_aliases = {cf.alias for cf in analyzed.computed_fields}
            computed_aliases.update(df.alias for df in analyzed.date_functions)
            computed_aliases.update(gs.alias for gs in parsed.geo_distance_selects)
            for field_name, _ in parsed.orderby_fields:
                if (
                    field_name in analyzed.field_types
                    and field_name not in computed_aliases
                ):
                    load_fields.add(field_name)

        if load_all:
            args.extend(["LOAD", "*"])
        elif load_fields:
            args.append("LOAD")
            args.append(str(len(load_fields)))
            # Redis expects property names prefixed with '@' in LOAD
            args.extend(
                f"@{field}" if not field.startswith("@") else field
                for field in sorted(load_fields)
            )

        # APPLY for computed fields
        for computed in analyzed.computed_fields:
            # Prefix field references with @ for Redis
            expression = self._prefix_fields_in_expression(
                computed.expression, analyzed.field_types
            )
            args.extend(["APPLY", expression, "AS", computed.alias])

        # APPLY for geo_distance() in SELECT
        for geo_select in parsed.geo_distance_selects:
            expr, alias = self._query_builder.build_geo_distance_apply(
                geo_select.field,
                geo_select.lon,
                geo_select.lat,
                geo_select.alias,
                geo_select.unit,
            )
            args.extend(["APPLY", expr, "AS", alias])

        # APPLY and FILTER for geo_distance() with >, >=, BETWEEN operators
        for i, geo_cond in enumerate(geo_filter_conditions):
            # Create a unique alias for this geo distance calculation
            geo_alias = f"__geo_dist_{i}"
            # APPLY geodistance() to calculate distance
            geo_expr = f"geodistance(@{geo_cond.field}, {geo_cond.lon}, {geo_cond.lat})"
            args.extend(["APPLY", geo_expr, "AS", geo_alias])
            # FILTER based on operator
            filter_expr = self._build_geo_filter_expression(geo_cond, geo_alias)
            args.extend(["FILTER", filter_expr])

        # APPLY for date functions (YEAR, MONTH, DAY, etc.) from SELECT
        for date_func in analyzed.date_functions:
            redis_func = SQL_TO_REDIS_DATE_FUNCTIONS.get(date_func.function)
            if redis_func:
                if date_func.function == "DATE_FORMAT" and date_func.format_string:
                    # DATE_FORMAT(field, format) -> timefmt(@field, format)
                    # Escape backslashes and quotes in format string
                    escaped_fmt = date_func.format_string.replace("\\", "\\\\").replace(
                        '"', '\\"'
                    )
                    expression = f'{redis_func}(@{date_func.field}, "{escaped_fmt}")'
                else:
                    # Simple date extraction: YEAR(field) -> year(@field)
                    expression = f"{redis_func}(@{date_func.field})"
                args.extend(["APPLY", expression, "AS", date_func.alias])

        # APPLY for date function conditions in WHERE (need to compute before FILTER)
        date_func_conditions = [
            c for c in parsed.conditions if self._is_date_function_condition(c)
        ]

        # Validate: negated date function conditions are not supported
        for condition in date_func_conditions:
            if condition.negated:
                raise ValueError(
                    "Negated date function conditions (NOT YEAR(...), etc.) "
                    "are not supported"
                )

        # Track which date functions we've already computed with canonical alias.
        # Only skip if SELECT used the canonical alias (e.g., year_created_at).
        # If SELECT used a custom alias (e.g., YEAR(created_at) AS year),
        # we still need to compute the canonical alias for FILTER.
        computed_canonical_aliases = {
            (df.function, df.field)
            for df in analyzed.date_functions
            if df.alias == f"{df.function.lower()}_{df.field}"
        }
        for condition in date_func_conditions:
            parts = condition.operator.rsplit("_", 1)
            func_name = parts[0]
            redis_func = SQL_TO_REDIS_DATE_FUNCTIONS.get(func_name)
            if (
                redis_func
                and (func_name, condition.field) not in computed_canonical_aliases
            ):
                expression = f"{redis_func}(@{condition.field})"
                alias = f"{func_name.lower()}_{condition.field}"
                args.extend(["APPLY", expression, "AS", alias])
                computed_canonical_aliases.add((func_name, condition.field))

        # FILTER for date function conditions
        for condition in date_func_conditions:
            filter_expr = self._build_date_function_filter(condition)
            args.extend(["FILTER", filter_expr])

        # GROUPBY
        if analyzed.groupby_fields:
            args.append("GROUPBY")
            args.append(str(len(analyzed.groupby_fields)))
            args.extend(f"@{field}" for field in analyzed.groupby_fields)

            # REDUCE for aggregations
            for agg in analyzed.aggregations:
                args.append("REDUCE")
                args.append(agg.function.upper())
                # COUNT always takes 0 arguments in Redis
                if agg.function.upper() == "COUNT":
                    args.append("0")
                elif agg.field:
                    # Calculate nargs: 1 for field + number of extra args
                    nargs = 1 + len(agg.extra_args)
                    args.append(str(nargs))
                    args.append(f"@{agg.field}")
                    args.extend(agg.extra_args)
                else:
                    args.append("0")
                if agg.alias:
                    args.extend(["AS", agg.alias])
        elif analyzed.is_global_aggregation:
            # Global aggregation - no GROUPBY
            args.extend(["GROUPBY", "0"])
            for agg in analyzed.aggregations:
                args.append("REDUCE")
                args.append(agg.function.upper())
                # COUNT always takes 0 arguments in Redis
                if agg.function.upper() == "COUNT":
                    args.append("0")
                elif agg.field:
                    # Calculate nargs: 1 for field + number of extra args
                    nargs = 1 + len(agg.extra_args)
                    args.append(str(nargs))
                    args.append(f"@{agg.field}")
                    args.extend(agg.extra_args)
                else:
                    args.append("0")
                # Always provide an alias
                alias = agg.alias or agg.function.lower()
                args.extend(["AS", alias])

        # FILTER for exists() from HAVING clause (post-aggregation)
        for filter_expr in parsed.filters:
            prefixed = self._prefix_fields_in_expression(
                filter_expr, analyzed.field_types
            )
            args.extend(["FILTER", prefixed])

        # SORTBY
        if parsed.orderby_fields:
            args.append("SORTBY")
            args.append(str(len(parsed.orderby_fields) * 2))
            for field_name, direction in parsed.orderby_fields:
                args.extend([f"@{field_name}", direction])

        # LIMIT
        if parsed.limit is not None:
            offset = parsed.offset or 0
            args.extend(["LIMIT", str(offset), str(parsed.limit)])

        # DIALECT 2 — unconditionally appended as the last arguments
        args.extend(["DIALECT", "2"])

        return TranslatedQuery(
            command="FT.AGGREGATE",
            index=parsed.index,
            query_string=query_string,
            args=args,
        )

    def _build_geo_filter_expression(
        self, geo_cond: GeoDistanceCondition, alias: str
    ) -> str:
        """Build FILTER expression for geo distance comparison.

        Args:
            geo_cond: The geo distance condition with operator and radius.
            alias: The alias for the calculated distance field.

        Returns:
            Filter expression string for Redis FILTER clause.
        """
        if geo_cond.operator == "BETWEEN":
            # For BETWEEN, radius is a tuple (low, high)
            if isinstance(geo_cond.radius, tuple) and len(geo_cond.radius) == 2:
                low_m = self._convert_to_meters(geo_cond.radius[0], geo_cond.unit)
                high_m = self._convert_to_meters(geo_cond.radius[1], geo_cond.unit)
                return f"@{alias} >= {low_m} && @{alias} <= {high_m}"
            else:
                # Internal inconsistency: BETWEEN requires (low, high) tuple
                raise ValueError(
                    f"Invalid geo radius for BETWEEN operator: {geo_cond.radius!r}"
                )

        # Convert radius to meters if needed (geodistance() returns meters)
        # At this point, radius should be a float (BETWEEN case handled above)
        if isinstance(geo_cond.radius, tuple):
            # Internal inconsistency: tuple radius outside BETWEEN context
            raise ValueError(
                f"Unexpected tuple geo radius outside BETWEEN: {geo_cond.radius!r}"
            )
        radius_m = self._convert_to_meters(geo_cond.radius, geo_cond.unit)

        if geo_cond.operator == ">":
            return f"@{alias} > {radius_m}"
        elif geo_cond.operator == ">=":
            return f"@{alias} >= {radius_m}"
        elif geo_cond.operator == "<":
            return f"@{alias} < {radius_m}"
        elif geo_cond.operator == "<=":
            return f"@{alias} <= {radius_m}"
        else:
            # Unknown operator - shouldn't happen
            raise ValueError(f"Unsupported geo operator: {geo_cond.operator}")

    def _convert_to_meters(self, value: float, unit: str) -> float:
        """Convert a distance value to meters.

        Args:
            value: The distance value.
            unit: The unit (m, km, mi, ft).

        Returns:
            Distance in meters.

        Raises:
            ValueError: If the unit is not supported.
        """
        # Normalize unit to lowercase
        normalized_unit = unit.lower()
        conversions = {
            "m": 1.0,
            "km": 1000.0,
            "mi": 1609.344,
            "ft": 0.3048,
        }
        if normalized_unit not in conversions:
            raise ValueError(
                f"Unsupported geo distance unit: {unit!r}. "
                "Supported units are 'm', 'km', 'mi', 'ft'."
            )
        return value * conversions[normalized_unit]

    @staticmethod
    def _extract_exists_fields(expression: str, load_fields: set[str]) -> None:
        """Extract field names from exists() calls and add to load_fields."""
        for match in re.finditer(r"exists\((\w+)\)", expression, re.IGNORECASE):
            load_fields.add(match.group(1))

    def _prefix_fields_in_expression(
        self, expression: str, schema: dict[str, str]
    ) -> str:
        """Prefix field names with @ in an expression for Redis APPLY."""
        result = expression
        for field_name in schema:
            # Match field name as a whole word, not already prefixed with @
            pattern = rf"(?<!@)\b{re.escape(field_name)}\b"
            result = re.sub(pattern, f"@{field_name}", result)
        return result

    def _is_date_function_condition(self, condition) -> bool:
        """Check if a condition involves a date function.

        Date function conditions have operators like YEAR_=, MONTH_>, etc.
        Note: DATE_FORMAT is excluded - it's rejected at parse time because
        the format string can't be represented in the Condition model.
        """
        date_prefixes = (
            "YEAR_",
            "MONTH_",
            "DAY_",
            "DAYOFWEEK_",
            "DAYOFYEAR_",
            "HOUR_",
            "MINUTE_",
        )
        return condition.operator.startswith(date_prefixes)

    def _build_date_function_filter(self, condition) -> str:
        """Build a FILTER expression for a date function condition.

        For example: YEAR(created_at) = 2024 -> @year_created_at == 2024
        """
        # Parse operator: "YEAR_=" -> func="YEAR", op="="
        parts = condition.operator.rsplit("_", 1)
        func_name = parts[0]
        op = parts[1] if len(parts) > 1 else "="

        # Build the alias used in APPLY
        alias = f"{func_name.lower()}_{condition.field}"

        # Map SQL operators to Redis FILTER operators
        op_map = {"=": "==", "!=": "!=", ">": ">", ">=": ">=", "<": "<", "<=": "<="}
        redis_op = op_map.get(op, "==")

        # Normalize value for FILTER expression (quote strings, pass numbers as-is)
        normalized_value = self._normalize_filter_value(condition.value)

        return f"@{alias} {redis_op} {normalized_value}"

    def _normalize_filter_value(self, value: object) -> str:
        """Normalize a value for use in FILTER expressions.

        Redis FILTER expressions require string values to be quoted.

        Args:
            value: The value to normalize.

        Returns:
            String representation suitable for FILTER expression.
        """
        if isinstance(value, (int, float)):
            return str(value)
        # Quote string values for FILTER, escaping backslashes and double quotes
        str_value = str(value)
        escaped_value = str_value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped_value}"'
