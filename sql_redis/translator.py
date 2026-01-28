"""SQL to Redis command translator."""

from __future__ import annotations

from dataclasses import dataclass, field

from sql_redis.analyzer import AnalyzedQuery, Analyzer
from sql_redis.parser import Condition, ParsedQuery, SQLParser
from sql_redis.query_builder import QueryBuilder
from sql_redis.schema import SchemaRegistry


@dataclass
class TranslatedQuery:
    """Result of translating SQL to Redis."""

    command: str  # FT.SEARCH or FT.AGGREGATE
    index: str
    query_string: str
    args: list[str] = field(default_factory=list)
    params: dict[str, object] = field(default_factory=dict)  # Named parameters

    def to_command_list(self) -> list[str]:
        """Return as a list suitable for redis.execute_command()."""
        return [self.command, self.index, self.query_string, *self.args]

    def to_command_string(self) -> str:
        """Return as a human-readable command string."""
        parts = [self.command, self.index, f'"{self.query_string}"']
        parts.extend(self.args)
        return " ".join(parts)


class Translator:
    """Translates SQL queries to Redis FT.SEARCH/FT.AGGREGATE commands."""

    def __init__(self, schema_registry: SchemaRegistry):
        """Initialize translator with schema registry.

        Args:
            schema_registry: Registry containing index schemas.
        """
        self._schema_registry = schema_registry
        self._parser = SQLParser()
        self._query_builder = QueryBuilder()

    def translate(self, sql: str) -> TranslatedQuery:
        """Translate a SQL SELECT into a Redis search command.

        Args:
            sql: SQL SELECT statement.

        Returns:
            TranslatedQuery with command details.

        Raises:
            ValueError: If SQL is invalid or references unknown index/fields.
        """
        # Parse
        parsed = self._parser.parse(sql)

        # Get schema and analyze
        schemas = {parsed.index: self._schema_registry.get_schema(parsed.index)}
        analyzer = Analyzer(schemas)
        analyzed = analyzer.analyze(parsed)

        # Build query
        return self._build_command(analyzed)

    def _build_command(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
        """Build the Redis command from analyzed query."""
        parsed = analyzed.parsed

        # Determine if we need FT.AGGREGATE
        use_aggregate = (
            len(analyzed.aggregations) > 0
            or len(analyzed.groupby_fields) > 0
            or len(analyzed.computed_fields) > 0
        )

        # Build query string from conditions
        query_string = self._build_query_string(analyzed)

        # Build arguments
        args: list[str] = []
        params: dict[str, object] = {}

        if use_aggregate:
            return self._build_aggregate(analyzed, query_string)
        else:
            return self._build_search(analyzed, query_string)

    def _build_query_string(self, analyzed: AnalyzedQuery) -> str:
        """Build the RediSearch query string from conditions."""
        parsed = analyzed.parsed
        conditions = parsed.conditions

        if not conditions and not analyzed.vector_search:
            return "*"

        # Build condition strings by type
        condition_strings: list[str] = []

        for condition in conditions:
            field_type = analyzed.get_field_type(condition.field)
            condition_str = self._build_condition(condition, field_type)
            condition_strings.append(condition_str)

        # Combine with boolean operator
        combined = self._query_builder.combine_conditions(
            condition_strings, parsed.boolean_operator
        )

        # Handle vector search with prefilter
        if analyzed.vector_search:
            vs = analyzed.vector_search
            # Vector search uses KNN syntax
            if analyzed.has_prefilter:
                # Prefilter: (filter)=>[KNN k @field $vec]
                return f"({combined})=>[KNN {vs.k} @{vs.field} $vector AS {vs.alias}]"
            else:
                # Pure KNN: *=>[KNN k @field $vec]
                return f"*=>[KNN {vs.k} @{vs.field} $vector AS {vs.alias}]"

        return combined

    def _build_condition(self, condition: Condition, field_type: str | None) -> str:
        """Build a single condition string based on field type."""
        # Determine if this is a negation (either explicit or via != operator)
        operator = condition.operator
        is_negated = condition.negated or operator == "!="
        if condition.negated and operator == "=":
            operator = "!="

        if field_type == "TEXT":
            return self._query_builder.build_text_condition(
                condition.field,
                operator,
                str(condition.value),
                is_negated,
            )
        elif field_type == "TAG":
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
            )
        elif field_type == "NUMERIC":
            # Cast value to expected type for numeric conditions
            numeric_value: int | float | tuple[int | float, int | float]
            if isinstance(condition.value, tuple):
                numeric_value = condition.value  # type: ignore[assignment]
            elif isinstance(condition.value, (int, float)):
                numeric_value = condition.value
            else:
                numeric_value = float(condition.value)  # type: ignore[arg-type]
            return self._query_builder.build_numeric_condition(
                condition.field,
                operator,
                numeric_value,
            )
        else:
            # GEO, VECTOR, and unknown field types - default to text search
            return self._query_builder.build_text_condition(
                condition.field,
                operator,
                str(condition.value),
                condition.negated,
            )

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
            args.append("DIALECT")
            args.append("2")
            params["vector"] = None  # Placeholder for vector bytes

        # RETURN clause - include vector distance alias if present
        return_fields = list(parsed.fields) if parsed.fields else []
        if analyzed.vector_search and analyzed.vector_search.alias:
            # Add vector distance alias to return fields (like VectorQuery with return_score=True)
            if analyzed.vector_search.alias not in return_fields:
                return_fields.append(analyzed.vector_search.alias)

        if return_fields and return_fields != ["*"]:
            args.append("RETURN")
            args.append(str(len(return_fields)))
            args.extend(return_fields)

        # SORTBY
        if parsed.orderby_fields:
            field_name, direction = parsed.orderby_fields[0]
            args.extend(["SORTBY", field_name, direction])

        # LIMIT
        if parsed.limit is not None:
            offset = parsed.offset or 0
            args.extend(["LIMIT", str(offset), str(parsed.limit)])

        return TranslatedQuery(
            command="FT.SEARCH",
            index=parsed.index,
            query_string=query_string,
            args=args,
            params=params,
        )

    def _build_aggregate(
        self, analyzed: AnalyzedQuery, query_string: str
    ) -> TranslatedQuery:
        """Build FT.AGGREGATE command."""
        parsed = analyzed.parsed
        args: list[str] = []

        # LOAD fields if needed
        load_fields = set()
        for agg in analyzed.aggregations:
            if agg.field:
                load_fields.add(agg.field)
        for field_name in analyzed.groupby_fields:
            load_fields.add(field_name)

        if load_fields:
            args.append("LOAD")
            args.append(str(len(load_fields)))
            args.extend(sorted(load_fields))

        # APPLY for computed fields
        for computed in analyzed.computed_fields:
            # Prefix field references with @ for Redis
            expression = self._prefix_fields_in_expression(
                computed.expression, analyzed.field_types
            )
            args.extend(["APPLY", expression, "AS", computed.alias])

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

        return TranslatedQuery(
            command="FT.AGGREGATE",
            index=parsed.index,
            query_string=query_string,
            args=args,
        )

    def _prefix_fields_in_expression(
        self, expression: str, schema: dict[str, str]
    ) -> str:
        """Prefix field names with @ in an expression for Redis APPLY."""
        import re

        result = expression
        for field_name in schema:
            # Match field name as a whole word, not already prefixed with @
            pattern = rf"(?<!@)\b{re.escape(field_name)}\b"
            result = re.sub(pattern, f"@{field_name}", result)
        return result
