"""SQL to Redis command translator."""

from __future__ import annotations

from dataclasses import dataclass, field

from sql_redis.analyzer import AnalyzedQuery, Analyzer
from sql_redis.parser import (
    Condition,
    GeoDistanceCondition,
    SQLParser,
)
from sql_redis.query_builder import QueryBuilder
from sql_redis.schema import AsyncSchemaRegistry, SchemaRegistry


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

        # Validate: geo_distance cannot be combined with OR
        # Geo filters are applied as top-level command args (GEOFILTER/FILTER) and
        # are not part of the boolean expression. Combining with OR would change
        # semantics (e.g., `A OR geo_distance(...)` would become `(A) AND geo_filter`).
        if parsed.geo_conditions and parsed.boolean_operator == "OR":
            raise ValueError(
                "Geo distance predicates cannot be combined with OR; "
                "they are applied as top-level filters and would change query "
                "semantics. Rewrite the query to avoid OR with geo_distance."
            )

        # Check if any geo conditions require FT.AGGREGATE (>, >=, BETWEEN)
        geo_requires_aggregate = any(
            geo.operator in (">", ">=", "BETWEEN") for geo in parsed.geo_conditions
        )

        # Determine if we need FT.AGGREGATE
        use_aggregate = (
            len(analyzed.aggregations) > 0
            or len(analyzed.groupby_fields) > 0
            or len(analyzed.computed_fields) > 0
            or len(parsed.geo_distance_selects) > 0  # geo_distance() in SELECT
            or geo_requires_aggregate  # geo_distance with >, >=, BETWEEN
        )

        # Build query string from conditions
        query_string = self._build_query_string(analyzed)

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
        load_fields = set()
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
        # Load regular SELECT fields for FT.AGGREGATE
        if parsed.fields and parsed.fields != ["*"]:
            for field in parsed.fields:
                # Skip computed fields (they have aliases from geo_distance)
                if field not in [gs.alias for gs in parsed.geo_distance_selects]:
                    load_fields.add(field)

        if load_fields:
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
                # Fallback - shouldn't happen
                return f"@{alias} >= 0"

        # Convert radius to meters if needed (geodistance() returns meters)
        # At this point, radius is guaranteed to be a float (BETWEEN case handled above)
        if isinstance(geo_cond.radius, tuple):
            # Shouldn't reach here, but handle gracefully
            return f"@{alias} >= 0"
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
