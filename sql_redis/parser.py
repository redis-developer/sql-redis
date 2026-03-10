"""SQL parser component using sqlglot."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import sqlglot
from sqlglot import exp


@dataclass
class AggregationSpec:
    """Specification for an aggregation function."""

    function: str
    field: str | None = None
    alias: str | None = None
    extra_args: list[str] = dataclasses.field(
        default_factory=list
    )  # For reducers like QUANTILE


@dataclass
class ComputedField:
    """Specification for a computed/APPLY field."""

    expression: str
    alias: str


@dataclass
class VectorSearchSpec:
    """Specification for vector search."""

    field: str
    alias: str
    k: int | None = None


@dataclass
class Condition:
    """A WHERE condition."""

    field: str
    operator: str
    value: object
    negated: bool = False


@dataclass
class GeoDistanceCondition:
    """A GEO distance condition with coordinates.

    Represents: geo_distance(field, POINT(lon, lat), unit) < radius
    Uses POINT(lon, lat) order to match Redis's native format.
    """

    field: str
    lon: float
    lat: float
    radius: float | tuple[float, float]  # Single value or (low, high) for BETWEEN
    operator: str  # '<', '<=', '>', '>=', 'BETWEEN'
    unit: str = "m"  # m, km, mi, ft (default: meters)


@dataclass
class GeoDistanceSelect:
    """A geo_distance() call in SELECT clause for FT.AGGREGATE APPLY.

    Uses POINT(lon, lat) order to match Redis's native format.
    """

    field: str
    lon: float
    lat: float
    alias: str
    unit: str = "m"  # m, km, mi, ft (default: meters)


@dataclass
class ParsedQuery:
    """Result of parsing a SQL query."""

    index: str = ""
    fields: list[str] = dataclasses.field(default_factory=list)
    conditions: list[Condition] = dataclasses.field(default_factory=list)
    geo_conditions: list[GeoDistanceCondition] = dataclasses.field(default_factory=list)
    geo_distance_selects: list[GeoDistanceSelect] = dataclasses.field(
        default_factory=list
    )
    boolean_operator: str = "AND"
    aggregations: list[AggregationSpec] = dataclasses.field(default_factory=list)
    computed_fields: list[ComputedField] = dataclasses.field(default_factory=list)
    vector_search: VectorSearchSpec | None = None
    groupby_fields: list[str] = dataclasses.field(default_factory=list)
    orderby_fields: list[tuple[str, str]] = dataclasses.field(
        default_factory=list
    )  # (field, ASC|DESC)
    limit: int | None = None
    offset: int | None = None


class SQLParser:
    """Parses SQL into a ParsedQuery structure."""

    def parse(self, sql: str) -> ParsedQuery:
        """Parse a SQL statement into a ParsedQuery.

        Args:
            sql: The SQL statement to parse.

        Returns:
            A ParsedQuery containing the extracted components.
        """
        ast = sqlglot.parse_one(sql)
        result = ParsedQuery()

        # Extract FROM clause (index name)
        from_clause = ast.find(exp.From)
        if from_clause:
            table = from_clause.find(exp.Table)
            if table:
                result.index = table.name

        # Extract SELECT fields and aggregations
        select = ast.find(exp.Select)
        if select:
            for expression in select.expressions:
                self._process_select_expression(expression, result)

        # Extract WHERE clause conditions
        where = ast.find(exp.Where)
        if where:
            self._process_where_clause(where.this, result)

        # Extract GROUP BY clause
        group = ast.find(exp.Group)
        if group:
            for expr in group.expressions:
                if isinstance(expr, exp.Column):
                    result.groupby_fields.append(expr.name)

        # Extract ORDER BY clause
        order = ast.find(exp.Order)
        if order:
            for ordered in order.expressions:
                col = ordered.this
                if isinstance(col, exp.Column):
                    direction = "DESC" if ordered.args.get("desc") else "ASC"
                    result.orderby_fields.append((col.name, direction))
                elif isinstance(col, (exp.CosineDistance, exp.Distance)):
                    # ORDER BY vector distance - handled by KNN, don't add to orderby
                    # The vector_search should already be set from SELECT clause
                    pass

        # Extract LIMIT clause
        limit = ast.find(exp.Limit)
        if limit:
            limit_expr = limit.args.get("expression") or limit.this
            if isinstance(limit_expr, exp.Literal):
                result.limit = int(limit_expr.this)

        # Extract OFFSET clause
        offset = ast.find(exp.Offset)
        if offset:
            offset_expr = offset.args.get("expression") or offset.this
            if isinstance(offset_expr, exp.Literal):
                result.offset = int(offset_expr.this)

        return result

    def _process_select_expression(self, expression, result: ParsedQuery) -> None:
        """Process a single SELECT expression."""
        # Handle aliased expressions (e.g., COUNT(*) AS count)
        if isinstance(expression, exp.Alias):
            alias = expression.alias
            inner = expression.this
            self._process_select_expression_inner(inner, result, alias)
        else:
            self._process_select_expression_inner(expression, result, None)

    def _process_select_expression_inner(
        self, expression, result: ParsedQuery, alias: str | None
    ) -> None:
        """Process the inner part of a SELECT expression."""
        if isinstance(expression, exp.Column):
            result.fields.append(expression.name)
        elif isinstance(expression, exp.Star):
            result.fields.append("*")
        elif isinstance(
            expression,
            (
                exp.Count,
                exp.Sum,
                exp.Avg,
                exp.Min,
                exp.Max,
                exp.Stddev,
                exp.Variance,
                exp.FirstValue,
                exp.ArrayAgg,
            ),
        ):
            # Aggregation function
            # Map sqlglot function names to Redis reducer names
            func_name = expression.key.upper()
            redis_func_map = {
                "FIRSTVALUE": "FIRST_VALUE",
                "ARRAYAGG": "TOLIST",
            }
            func_name = redis_func_map.get(func_name, func_name)
            field_name = None
            # Get the field being aggregated (if any)
            if expression.this:
                if isinstance(expression.this, exp.Column):
                    field_name = expression.this.name
                elif isinstance(expression.this, exp.Star):
                    field_name = None  # COUNT(*)
            result.aggregations.append(
                AggregationSpec(function=func_name, field=field_name, alias=alias)
            )
        elif isinstance(expression, exp.Paren):
            # Parenthesized expression - computed field
            inner_expr = expression.this.sql()
            # Use alias if provided, otherwise generate one from expression
            field_alias = alias if alias else inner_expr
            result.computed_fields.append(
                ComputedField(expression=inner_expr, alias=field_alias)
            )
        elif isinstance(expression, (exp.Mul, exp.Div, exp.Add, exp.Sub)):
            # Arithmetic expression without parentheses - computed field
            expr_str = expression.sql()
            # Use alias if provided, otherwise generate one from expression
            field_alias = alias if alias else expr_str
            result.computed_fields.append(
                ComputedField(expression=expr_str, alias=field_alias)
            )
        elif isinstance(expression, (exp.Distance, exp.CosineDistance)):
            # Vector distance functions:
            # - Distance: L2/Euclidean distance
            # - CosineDistance: cosine_distance() function
            self._process_vector_distance(expression, result, alias)
        elif isinstance(expression, exp.Quantile):
            # QUANTILE(field, quantile_value) -> REDUCE QUANTILE 2 @field quantile_value
            field_name = None
            if expression.this and isinstance(expression.this, exp.Column):
                field_name = expression.this.name
            quantile_value = None
            if expression.args.get("quantile"):
                quantile_value = str(expression.args["quantile"].this)
            extra_args = [quantile_value] if quantile_value else []
            result.aggregations.append(
                AggregationSpec(
                    function="QUANTILE",
                    field=field_name,
                    alias=alias,
                    extra_args=extra_args,
                )
            )
        elif isinstance(expression, exp.Anonymous):
            # Custom function call (e.g., vector_distance) - check before exp.Func
            # since Anonymous is a subclass of Func
            func_name = expression.name.lower()
            # Redis-specific reducer functions that sqlglot doesn't recognize
            redis_reducers = {
                "count_distinct",
                "count_distinctish",
                "quantile",
                "random_sample",
            }
            if func_name == "vector_distance":
                # Extract the vector field name from first argument
                if expression.expressions:
                    first_arg = expression.expressions[0]
                    if isinstance(first_arg, exp.Column):
                        field_name = first_arg.name
                        result.vector_search = VectorSearchSpec(
                            field=field_name,
                            alias=alias or func_name,
                        )
            elif func_name == "geo_distance":
                # geo_distance(field, POINT(lon, lat), unit) in SELECT
                self._process_geo_distance_select(expression, result, alias)
            elif func_name in redis_reducers:
                # Redis-specific reducer functions
                field_name = None
                reducer_extra_args: list[str] = []
                if expression.expressions:
                    first_arg = expression.expressions[0]
                    if isinstance(first_arg, exp.Column):
                        field_name = first_arg.name
                    # Extract additional arguments (e.g., quantile value for QUANTILE)
                    for arg in expression.expressions[1:]:
                        if isinstance(arg, exp.Literal):
                            reducer_extra_args.append(str(arg.this))
                result.aggregations.append(
                    AggregationSpec(
                        function=func_name.upper(),
                        field=field_name,
                        alias=alias,
                        extra_args=reducer_extra_args,
                    )
                )
            else:
                # Other custom functions - treat as computed field
                expr_str = expression.sql()
                field_alias = alias if alias else expr_str
                result.computed_fields.append(
                    ComputedField(expression=expr_str, alias=field_alias)
                )
        elif isinstance(expression, exp.Func):
            # Built-in function call (e.g., UPPER, LOWER, etc.) - treat as computed field
            expr_str = expression.sql()
            field_alias = alias if alias else expr_str
            result.computed_fields.append(
                ComputedField(expression=expr_str, alias=field_alias)
            )

    def _process_vector_distance(
        self, expression, result: ParsedQuery, alias: str | None
    ) -> None:
        """Process a vector distance expression (cosine_distance, etc.)."""
        field_name = None

        # Extract field from the expression
        # Both Distance and CosineDistance have 'this' as the first argument
        if expression.this and isinstance(expression.this, exp.Column):
            field_name = expression.this.name

        if field_name:
            result.vector_search = VectorSearchSpec(
                field=field_name,
                alias=alias or "vector_distance",
            )

    def _process_geo_distance_select(
        self, expression, result: ParsedQuery, alias: str | None
    ) -> None:
        """Process geo_distance() in SELECT clause for FT.AGGREGATE APPLY."""
        func_args = expression.expressions
        if not func_args:
            return

        field_name = None
        geo_lon = None
        geo_lat = None
        geo_unit = "m"  # Default to meters for geodistance()

        # First arg: field name
        if isinstance(func_args[0], exp.Column):
            field_name = func_args[0].name

        # Second arg: POINT(lon, lat) - matches Redis's native format
        if len(func_args) >= 2 and isinstance(func_args[1], exp.Anonymous):
            point_func = func_args[1]
            if point_func.name.upper() == "POINT" and len(point_func.expressions) >= 2:
                # POINT(lon, lat) - no swap needed, matches Redis
                geo_lon = self._extract_literal_value(point_func.expressions[0])
                geo_lat = self._extract_literal_value(point_func.expressions[1])

        # Third arg (optional): unit
        if len(func_args) >= 3:
            unit_val = self._extract_literal_value(func_args[2])
            if unit_val:
                geo_unit = str(unit_val)

        if field_name and geo_lon is not None and geo_lat is not None:
            result.geo_distance_selects.append(
                GeoDistanceSelect(
                    field=field_name,
                    lon=float(geo_lon),
                    lat=float(geo_lat),
                    alias=alias or "geo_distance",
                    unit=geo_unit,
                )
            )

    def _process_where_clause(
        self, expression, result: ParsedQuery, negated: bool = False
    ) -> None:
        """Process WHERE clause expression recursively."""
        if isinstance(expression, exp.EQ):
            self._add_condition(expression, "=", result, negated)
        elif isinstance(expression, exp.GT):
            self._add_condition(expression, ">", result, negated)
        elif isinstance(expression, exp.GTE):
            self._add_condition(expression, ">=", result, negated)
        elif isinstance(expression, exp.LT):
            self._add_condition(expression, "<", result, negated)
        elif isinstance(expression, exp.LTE):
            self._add_condition(expression, "<=", result, negated)
        elif isinstance(expression, exp.NEQ):
            self._add_condition(expression, "!=", result, negated)
        elif isinstance(expression, exp.Between):
            self._add_between_condition(expression, result, negated)
        elif isinstance(expression, exp.In):
            self._add_in_condition(expression, result, negated)
        elif isinstance(expression, exp.And):
            result.boolean_operator = "AND"
            self._process_where_clause(expression.this, result, negated)
            self._process_where_clause(expression.expression, result, negated)
        elif isinstance(expression, exp.Or):
            result.boolean_operator = "OR"
            self._process_where_clause(expression.this, result, negated)
            self._process_where_clause(expression.expression, result, negated)
        elif isinstance(expression, exp.Not):
            self._process_where_clause(expression.this, result, negated=True)
        elif isinstance(expression, exp.Anonymous):
            # Custom function like MATCH(field, value)
            self._add_function_condition(expression, result, negated)

    def _add_condition(
        self, expression, operator: str, result: ParsedQuery, negated: bool
    ) -> None:
        """Add a condition from a comparison expression."""
        field_name = None
        value = None
        is_geo_distance = False
        geo_lon = None
        geo_lat = None
        geo_unit = "m"  # Default to meters

        # Get field name from left side
        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name
        elif isinstance(expression.this, exp.Anonymous):
            # Function call like geo_distance(location, POINT(...))
            func_name = expression.this.name.upper()
            func_args = expression.this.expressions
            if func_name == "GEO_DISTANCE" and func_args:
                is_geo_distance = True
                # First arg: field name
                if isinstance(func_args[0], exp.Column):
                    field_name = func_args[0].name
                # Second arg: POINT(lon, lat) - matches Redis's native format
                if len(func_args) >= 2 and isinstance(func_args[1], exp.Anonymous):
                    point_func = func_args[1]
                    if (
                        point_func.name.upper() == "POINT"
                        and len(point_func.expressions) >= 2
                    ):
                        # POINT(lon, lat) - no swap needed, matches Redis
                        geo_lon = self._extract_literal_value(point_func.expressions[0])
                        geo_lat = self._extract_literal_value(point_func.expressions[1])
                # Third arg (optional): unit
                if len(func_args) >= 3:
                    unit_val = self._extract_literal_value(func_args[2])
                    if unit_val:
                        geo_unit = str(unit_val)
            elif func_args:
                # Other function calls
                first_arg = func_args[0]
                if isinstance(first_arg, exp.Column):
                    field_name = first_arg.name
                    operator = f"{func_name}_{operator}"

        # Get value from right side
        if isinstance(expression.expression, exp.Literal):
            value = expression.expression.this
            # Convert numeric strings to numbers
            if expression.expression.is_number:
                value = int(value) if "." not in str(value) else float(value)

        if field_name is not None:
            if is_geo_distance and geo_lon is not None and geo_lat is not None:
                # Create GeoDistanceCondition with extracted coordinates
                result.geo_conditions.append(
                    GeoDistanceCondition(
                        field=field_name,
                        lon=float(geo_lon),
                        lat=float(geo_lat),
                        radius=float(value) if value else 0.0,
                        operator=operator,
                        unit=geo_unit,
                    )
                )
            else:
                result.conditions.append(
                    Condition(
                        field=field_name,
                        operator=operator,
                        value=value,
                        negated=negated,
                    )
                )

    def _add_between_condition(
        self, expression, result: ParsedQuery, negated: bool
    ) -> None:
        """Add a BETWEEN condition."""
        field_name = None
        is_geo_distance = False
        geo_lon = None
        geo_lat = None
        geo_unit = "m"  # Default to meters

        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name
        elif isinstance(expression.this, exp.Anonymous):
            # Function call like geo_distance(location, POINT(...))
            func_name = expression.this.name.upper()
            func_args = expression.this.expressions
            if func_name == "GEO_DISTANCE" and func_args:
                is_geo_distance = True
                # First arg: field name
                if isinstance(func_args[0], exp.Column):
                    field_name = func_args[0].name
                # Second arg: POINT(lon, lat) - matches Redis's native format
                if len(func_args) >= 2 and isinstance(func_args[1], exp.Anonymous):
                    point_func = func_args[1]
                    if (
                        point_func.name.upper() == "POINT"
                        and len(point_func.expressions) >= 2
                    ):
                        # POINT(lon, lat) - no swap needed, matches Redis
                        geo_lon = self._extract_literal_value(point_func.expressions[0])
                        geo_lat = self._extract_literal_value(point_func.expressions[1])
                # Third arg (optional): unit
                if len(func_args) >= 3:
                    unit_val = self._extract_literal_value(func_args[2])
                    if unit_val:
                        geo_unit = str(unit_val)

        low = expression.args.get("low")
        high = expression.args.get("high")

        low_val = self._extract_literal_value(low)
        high_val = self._extract_literal_value(high)

        if field_name is not None:
            if is_geo_distance and geo_lon is not None and geo_lat is not None:
                # Create GeoDistanceCondition with BETWEEN operator
                result.geo_conditions.append(
                    GeoDistanceCondition(
                        field=field_name,
                        lon=float(geo_lon),
                        lat=float(geo_lat),
                        radius=(float(low_val), float(high_val)),  # Tuple for BETWEEN
                        operator="BETWEEN",
                        unit=geo_unit,
                    )
                )
            else:
                result.conditions.append(
                    Condition(
                        field=field_name,
                        operator="BETWEEN",
                        value=(low_val, high_val),
                        negated=negated,
                    )
                )

    def _add_in_condition(self, expression, result: ParsedQuery, negated: bool) -> None:
        """Add an IN condition."""
        field_name = None
        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name

        values = [self._extract_literal_value(e) for e in expression.expressions]

        if field_name is not None:
            result.conditions.append(
                Condition(
                    field=field_name, operator="IN", value=values, negated=negated
                )
            )

    def _add_function_condition(
        self, expression, result: ParsedQuery, negated: bool
    ) -> None:
        """Add a condition from a function call like fulltext(field, value)."""
        func_name = expression.name.upper()
        if func_name == "FULLTEXT" and len(expression.expressions) >= 2:
            first_arg = expression.expressions[0]
            second_arg = expression.expressions[1]

            field_name = None
            if isinstance(first_arg, exp.Column):
                field_name = first_arg.name

            value = self._extract_literal_value(second_arg)

            if field_name is not None:
                result.conditions.append(
                    Condition(
                        field=field_name,
                        operator="FULLTEXT",
                        value=value,
                        negated=negated,
                    )
                )

    def _extract_literal_value(self, expression):
        """Extract a Python value from a sqlglot Literal or Neg expression."""
        if isinstance(expression, exp.Literal):
            value = expression.this
            if expression.is_number:
                return int(value) if "." not in str(value) else float(value)
            return value
        elif isinstance(expression, exp.Neg):
            # Handle negative numbers: Neg(Literal(122.4)) -> -122.4
            inner_value = self._extract_literal_value(expression.this)
            if inner_value is not None:
                return -inner_value
        return None
