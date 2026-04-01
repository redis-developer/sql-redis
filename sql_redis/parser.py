"""SQL parser component using sqlglot."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlglot
from sqlglot import exp

# Regex patterns for ISO 8601 date/datetime detection
# Date: YYYY-MM-DD
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Datetime: YYYY-MM-DDTHH:MM:SS or YYYY-MM-DD HH:MM:SS (with optional timezone)
DATETIME_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def parse_date_to_timestamp(value: str) -> int | None:
    """Parse an ISO 8601 date/datetime string to Unix timestamp.

    Supports:
    - Date: '2024-01-01' (interpreted as midnight UTC)
    - Datetime: '2024-01-01T12:00:00' or '2024-01-01 12:00:00'
    - Datetime with timezone: '2024-01-01T12:00:00Z', '2024-01-01T12:00:00+00:00'

    Args:
        value: The string value to parse.

    Returns:
        Unix timestamp as integer, or None if not a valid date string.
    """
    # Check if it matches date pattern
    if DATE_PATTERN.match(value):
        try:
            dt = datetime.strptime(value, "%Y-%m-%d")
            # Treat as UTC midnight
            dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None

    # Check if it matches datetime pattern
    if DATETIME_PATTERN.match(value):
        # Normalize: replace space with T for parsing
        normalized = value.replace(" ", "T")

        # Normalize 'Z' (UTC designator) to '+00:00' for fromisoformat
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"

        # Normalize timezone offsets without colon (+0000 -> +00:00)
        # This ensures compatibility with datetime.fromisoformat
        normalized = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", normalized)

        try:
            # Use fromisoformat for robust parsing (handles fractional seconds)
            dt = datetime.fromisoformat(normalized)
            # If no timezone info, treat as UTC
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            return None

    return None


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
class DateFunctionSpec:
    """Specification for a date extraction function.

    Maps SQL date functions to Redis APPLY functions:
    - YEAR(field) → year(@field)
    - MONTH(field) → monthofyear(@field)
    - DAY(field) → dayofmonth(@field)
    - DAYOFWEEK(field) → dayofweek(@field)
    - DAYOFYEAR(field) → dayofyear(@field)
    - HOUR(field) → hour(@field)
    - MINUTE(field) → minute(@field)
    - DATE_FORMAT(field, format) → timefmt(@field, format)
    """

    function: str  # SQL function name (YEAR, MONTH, etc.)
    field: str  # Field name
    alias: str  # Output alias
    format_string: str | None = None  # For DATE_FORMAT only


# Mapping from SQL date function names to Redis APPLY function names
SQL_TO_REDIS_DATE_FUNCTIONS = {
    "YEAR": "year",
    "MONTH": "monthofyear",
    "DAY": "dayofmonth",
    "DAYOFWEEK": "dayofweek",
    "DAYOFYEAR": "dayofyear",
    "HOUR": "hour",
    "MINUTE": "minute",
    "DATE_FORMAT": "timefmt",
}

# Mapping from sqlglot expression type names to SQL function names
SQLGLOT_TO_SQL_DATE_FUNCTIONS = {
    "Year": "YEAR",
    "Month": "MONTH",
    "Day": "DAY",
    "DayOfWeek": "DAYOFWEEK",
    "DayOfYear": "DAYOFYEAR",
    "DayOfMonth": "DAY",  # DAY and DayOfMonth are equivalent
    "Hour": "HOUR",
    "Minute": "MINUTE",
}

# Mapping from sqlglot expression types to SQL function names (for type checking)
SQLGLOT_DATE_EXPR_TYPES = {
    exp.Year: "YEAR",
    exp.Month: "MONTH",
    exp.Day: "DAY",
    exp.DayOfWeek: "DAYOFWEEK",
    exp.DayOfYear: "DAYOFYEAR",
    exp.DayOfMonth: "DAY",
    exp.Hour: "HOUR",
    exp.Minute: "MINUTE",
}


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
    fuzzy_level: int | None = None  # Levenshtein distance for FUZZY (1, 2, or 3)
    slop: int | None = None  # Max distance between terms for proximity search
    inorder: bool = False  # Require terms in order (used with slop)


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
class ScoringSpec:
    """Specification for relevance scoring.

    Triggers WITHSCORES and optional SCORER on FT.SEARCH.
    """

    alias: str = "score"  # Column alias for the score
    scorer: str = "BM25"  # Scorer algorithm (BM25, TFIDF, DISMAX, etc.)


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
    date_functions: list[DateFunctionSpec] = dataclasses.field(default_factory=list)
    vector_search: VectorSearchSpec | None = None
    groupby_fields: list[str] = dataclasses.field(default_factory=list)
    orderby_fields: list[tuple[str, str]] = dataclasses.field(
        default_factory=list
    )  # (field, ASC|DESC)
    limit: int | None = None
    offset: int | None = None
    filters: list[str] = dataclasses.field(default_factory=list)
    scoring: ScoringSpec | None = None  # Relevance scoring config


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

        # Extract HAVING clause — exists() in HAVING → FILTER
        having = ast.find(exp.Having)
        if having:
            self._process_having_clause(having.this, result)

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
        elif isinstance(
            expression,
            (
                exp.Year,
                exp.Month,
                exp.Day,
                exp.DayOfWeek,
                exp.DayOfYear,
                exp.DayOfMonth,
                exp.Hour,
                exp.Minute,
            ),
        ):
            # Date extraction functions
            self._process_date_expression(expression, result, alias)
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
        elif isinstance(expression, exp.Exists):
            # exists(field) — RediSearch aggregation function
            # sqlglot parses exists(col) as exp.Exists(this=Column),
            # distinct from EXISTS (SELECT ...) which has this=Select.
            inner = expression.this
            if isinstance(inner, exp.Column):
                field_name = inner.name
                expr_str = f"exists({field_name})"
                field_alias = alias if alias else f"exists_{field_name}"
                result.computed_fields.append(
                    ComputedField(expression=expr_str, alias=field_alias)
                )
            else:
                raise ValueError(
                    "exists() in SELECT expects a column reference, "
                    f"got {type(inner).__name__}. "
                    "Use exists(field_name) for RediSearch field existence checks."
                )
        elif isinstance(expression, exp.Anonymous):
            # Custom function call (e.g., vector_distance) - check before exp.Func
            # since Anonymous is a subclass of Func
            func_name = expression.name.upper()
            func_name_lower = func_name.lower()
            # Redis-specific reducer functions that sqlglot doesn't recognize
            redis_reducers = {
                "count_distinct",
                "count_distinctish",
                "quantile",
                "random_sample",
            }
            if func_name_lower == "vector_distance":
                # Extract the vector field name from first argument
                if expression.expressions:
                    first_arg = expression.expressions[0]
                    if isinstance(first_arg, exp.Column):
                        field_name = first_arg.name
                        result.vector_search = VectorSearchSpec(
                            field=field_name,
                            alias=alias or func_name_lower,
                        )
            elif func_name_lower == "geo_distance":
                # geo_distance(field, POINT(lon, lat), unit) in SELECT
                self._process_geo_distance_select(expression, result, alias)
            elif func_name_lower == "score":
                # score() or score('BM25') — triggers WITHSCORES + SCORER
                scorer = "BM25"
                if expression.expressions:
                    scorer_val = self._extract_literal_value(expression.expressions[0])
                    if scorer_val is not None:
                        scorer = str(scorer_val)
                if result.scoring is not None:
                    raise ValueError(
                        "Only one score() expression is allowed per query."
                    )
                result.scoring = ScoringSpec(
                    alias=alias or "score",
                    scorer=scorer,
                )
            elif func_name_lower in redis_reducers:
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
                        function=func_name,
                        field=field_name,
                        alias=alias,
                        extra_args=reducer_extra_args,
                    )
                )
            elif func_name in SQL_TO_REDIS_DATE_FUNCTIONS:
                # Date extraction functions: YEAR, MONTH, DAY, etc.
                self._process_date_function(expression, result, alias)
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
        """Process geo_distance() in SELECT clause for FT.AGGREGATE APPLY.

        Expected signature: geo_distance(field, POINT(lon, lat)[, unit])
        Raises ValueError for malformed usage rather than silently ignoring.
        """
        func_args = expression.expressions
        if not func_args:
            raise ValueError(
                "geo_distance() requires at least 2 arguments: "
                "geo_distance(field, POINT(lon, lat)[, unit])"
            )

        # First arg: field name must be a column
        if not isinstance(func_args[0], exp.Column):
            raise ValueError("geo_distance() first argument must be a column reference")
        field_name = func_args[0].name

        # Second arg: POINT(lon, lat) required
        if len(func_args) < 2:
            raise ValueError(
                "geo_distance() requires a POINT(lon, lat) second argument"
            )
        if not isinstance(func_args[1], exp.Anonymous):
            raise ValueError("geo_distance() second argument must be POINT(lon, lat)")
        point_func = func_args[1]
        if point_func.name.upper() != "POINT" or len(point_func.expressions) < 2:
            raise ValueError("geo_distance() second argument must be POINT(lon, lat)")

        # Extract literal lon/lat values
        geo_lon = self._extract_literal_value(point_func.expressions[0])
        geo_lat = self._extract_literal_value(point_func.expressions[1])
        if geo_lon is None or geo_lat is None:
            raise ValueError(
                "geo_distance() POINT(lon, lat) arguments must be literal values"
            )

        # Third arg (optional): unit
        geo_unit = "m"  # Default to meters
        if len(func_args) >= 3:
            unit_val = self._extract_literal_value(func_args[2])
            if unit_val is None:
                raise ValueError("geo_distance() unit argument must be a literal value")
            geo_unit = self._validate_geo_unit(unit_val)

        result.geo_distance_selects.append(
            GeoDistanceSelect(
                field=field_name,
                lon=float(geo_lon),
                lat=float(geo_lat),
                alias=alias or "geo_distance",
                unit=geo_unit,
            )
        )

    def _process_date_function(
        self, expression, result: ParsedQuery, alias: str | None
    ) -> None:
        """Process a date extraction function (YEAR, MONTH, DAY, etc.).

        Args:
            expression: The sqlglot Anonymous expression for the function.
            result: The ParsedQuery to update.
            alias: Optional alias for the result.
        """
        func_name = expression.name.upper()
        field_name = None
        format_string = None

        args = expression.expressions or []

        if func_name == "DATE_FORMAT":
            # DATE_FORMAT requires exactly 2 arguments: field, format_string
            if len(args) != 2:
                raise ValueError(
                    "DATE_FORMAT requires exactly 2 arguments: field, format_string"
                )
            first_arg, second_arg = args
            if isinstance(first_arg, exp.Column):
                field_name = first_arg.name
            # Format argument must be a literal string
            if not isinstance(second_arg, exp.Literal) or not second_arg.is_string:
                raise ValueError("DATE_FORMAT format argument must be a literal string")
            format_string = second_arg.this
        elif args:
            first_arg = args[0]
            if isinstance(first_arg, exp.Column):
                field_name = first_arg.name

        if field_name:
            # Generate default alias if not provided
            if alias is None:
                if func_name == "DATE_FORMAT":
                    alias = f"formatted_{field_name}"
                else:
                    alias = f"{func_name.lower()}_{field_name}"

            result.date_functions.append(
                DateFunctionSpec(
                    function=func_name,
                    field=field_name,
                    alias=alias,
                    format_string=format_string,
                )
            )

    def _process_date_expression(
        self, expression, result: ParsedQuery, alias: str | None
    ) -> None:
        """Process a sqlglot date expression (Year, Month, Day, etc.).

        Args:
            expression: The sqlglot date expression (exp.Year, exp.Month, etc.).
            result: The ParsedQuery to update.
            alias: Optional alias for the result.
        """
        expr_type = type(expression).__name__
        func_name = SQLGLOT_TO_SQL_DATE_FUNCTIONS.get(expr_type)

        if func_name and expression.this:
            field_name = None
            if isinstance(expression.this, exp.Column):
                field_name = expression.this.name

            if field_name:
                # Generate default alias if not provided
                if alias is None:
                    alias = f"{func_name.lower()}_{field_name}"

                result.date_functions.append(
                    DateFunctionSpec(
                        function=func_name,
                        field=field_name,
                        alias=alias,
                        format_string=None,
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
        elif isinstance(expression, exp.Like):
            # LIKE 'pattern%' / '%pattern' / '%pattern%'
            self._add_condition(expression, "LIKE", result, negated)
        elif isinstance(expression, exp.And):
            result.boolean_operator = "AND"
            self._process_where_clause(expression.this, result, negated)
            self._process_where_clause(expression.expression, result, negated)
        elif isinstance(expression, exp.Or):
            result.boolean_operator = "OR"
            self._process_where_clause(expression.this, result, negated)
            self._process_where_clause(expression.expression, result, negated)
        elif isinstance(expression, exp.Not):
            self._process_where_clause(expression.this, result, negated=not negated)
        elif isinstance(expression, exp.Paren):
            self._process_where_clause(expression.this, result, negated=negated)
        elif isinstance(expression, exp.Is):
            # IS NULL: exp.Is(this=Column, expression=Null())
            # IS NOT NULL arrives here with negated=True via the exp.Not handler above
            if isinstance(expression.this, exp.Column) and isinstance(
                expression.expression, exp.Null
            ):
                operator = "IS_NOT_NULL" if negated else "IS_NULL"
                result.conditions.append(
                    Condition(
                        field=expression.this.name,
                        operator=operator,
                        value=None,
                        negated=False,
                    )
                )
            else:
                raise ValueError(
                    "Unsupported IS expression in WHERE clause; only "
                    "`column IS NULL` and `column IS NOT NULL` are supported."
                )
        elif isinstance(expression, exp.Exists):
            # Distinguish exists(column) from EXISTS (SELECT ...)
            inner = expression.this
            if isinstance(inner, exp.Column):
                # exists(field) — RediSearch aggregate function, not valid in WHERE
                raise ValueError(
                    "exists() is a RediSearch aggregate function and cannot be "
                    "used in WHERE clauses. Use HAVING exists(field) instead "
                    "for post-aggregate filtering."
                )
            # EXISTS (SELECT ...) — SQL subquery, silently ignored (not supported)
        elif isinstance(expression, exp.Anonymous):
            # Custom function like MATCH(field, value)
            self._add_function_condition(expression, result, negated)

    def _process_having_clause(self, expression, result: ParsedQuery) -> None:
        """Process HAVING clause — routes exists() to filters."""
        if isinstance(expression, exp.Exists):
            inner = expression.this
            if isinstance(inner, exp.Column):
                result.filters.append(f"exists({inner.name})")
            else:
                raise ValueError(
                    "exists() in HAVING expects a column reference, "
                    f"got {type(inner).__name__}."
                )
        elif isinstance(expression, exp.Paren):
            self._process_having_clause(expression.this, result)
        elif isinstance(expression, exp.And):
            self._process_having_clause(expression.this, result)
            self._process_having_clause(expression.expression, result)
        else:
            raise ValueError(
                f"Unsupported HAVING expression: {type(expression).__name__}. "
                "Only exists(field) is supported in HAVING."
            )

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
            # Function call like geo_distance(location, POINT(...)) or DATE_FORMAT
            func_name = expression.this.name.upper()
            # DATE_FORMAT in WHERE is not supported - format string can't be
            # represented in the Condition model. Use DATE_FORMAT in SELECT instead.
            if func_name == "DATE_FORMAT":
                raise ValueError(
                    "DATE_FORMAT in WHERE conditions is not supported. "
                    "Use DATE_FORMAT in the SELECT clause instead."
                )
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
                        geo_unit = self._validate_geo_unit(unit_val)
            elif func_args:
                # Other function calls
                first_arg = func_args[0]
                if isinstance(first_arg, exp.Column):
                    field_name = first_arg.name
                    operator = f"{func_name}_{operator}"
        elif type(expression.this) in SQLGLOT_DATE_EXPR_TYPES:
            # Built-in date expression like YEAR(field), MONTH(field), etc.
            func_name = SQLGLOT_DATE_EXPR_TYPES[type(expression.this)]
            if expression.this.this and isinstance(expression.this.this, exp.Column):
                field_name = expression.this.this.name
                # Use function name as operator prefix
                operator = f"{func_name}_{operator}"

        # Get value from right side (handles numbers, strings, and date literals)
        value = self._extract_literal_value(expression.expression)

        if field_name is not None:
            if is_geo_distance:
                # Fail fast if POINT(lon, lat) coordinates couldn't be parsed
                if geo_lon is None or geo_lat is None:
                    raise ValueError(
                        "geo_distance() requires POINT(lon, lat) with literal values"
                    )
                # Negated geo_distance is not supported; fail clearly
                if negated:
                    raise ValueError(
                        "Negated geo_distance comparisons (NOT geo_distance(...)) "
                        "are not supported"
                    )
                # Validate radius is provided
                if value is None:
                    raise ValueError(
                        "Geo distance comparison requires a literal radius value"
                    )
                try:
                    radius = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Invalid radius for geo distance comparison"
                    ) from exc
                # Create GeoDistanceCondition with extracted coordinates
                result.geo_conditions.append(
                    GeoDistanceCondition(
                        field=field_name,
                        lon=float(geo_lon),
                        lat=float(geo_lat),
                        radius=radius,
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
                        geo_unit = self._validate_geo_unit(unit_val)

        low = expression.args.get("low")
        high = expression.args.get("high")

        low_val = self._extract_literal_value(low)
        high_val = self._extract_literal_value(high)

        if field_name is not None:
            if is_geo_distance:
                # Fail fast if POINT(lon, lat) coordinates couldn't be parsed
                if geo_lon is None or geo_lat is None:
                    raise ValueError(
                        "geo_distance() BETWEEN requires POINT(lon, lat) with literal values"
                    )
                # Negation is not supported for geo_distance BETWEEN; fail clearly
                if negated:
                    raise ValueError(
                        "Negation (NOT) is not supported for geo_distance(...) BETWEEN"
                    )
                # Validate BETWEEN bounds are provided
                if low_val is None or high_val is None:
                    raise ValueError(
                        "Geo distance BETWEEN requires literal low and high values"
                    )
                try:
                    low_radius = float(low_val)
                    high_radius = float(high_val)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "Invalid radius values for geo distance BETWEEN"
                    ) from exc
                # Create GeoDistanceCondition with BETWEEN operator
                result.geo_conditions.append(
                    GeoDistanceCondition(
                        field=field_name,
                        lon=float(geo_lon),
                        lat=float(geo_lat),
                        radius=(low_radius, high_radius),  # Tuple for BETWEEN
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
        """Add a condition from a function call like fulltext(field, value) or fuzzy(field, value, level)."""
        func_name = expression.name.upper()
        args = expression.expressions

        if func_name == "FULLTEXT" and len(args) >= 2:
            field_name = args[0].name if isinstance(args[0], exp.Column) else None
            value = self._extract_literal_value(args[1])

            # Optional 3rd arg: slop (non-negative int)
            slop = None
            if len(args) >= 3:
                slop_val = self._extract_literal_value(args[2])
                if slop_val is not None:
                    # Reject booleans and non-integer floats — only real
                    # integers are valid for slop.
                    if isinstance(slop_val, bool):
                        raise ValueError(
                            f"FULLTEXT slop argument must be an integer (got {slop_val})"
                        )
                    if isinstance(slop_val, float) and slop_val != int(slop_val):
                        raise ValueError(
                            f"FULLTEXT slop argument must be an integer (got {slop_val})"
                        )
                    slop = int(slop_val)
                    if slop < 0:
                        raise ValueError(
                            f"FULLTEXT slop argument must be a non-negative integer (got {slop})"
                        )

            # Optional 4th arg: inorder (boolean-like: true/false or 1/0)
            inorder = False
            if len(args) >= 4:
                inorder_val = self._extract_literal_value(args[3])
                if inorder_val is not None:
                    if isinstance(inorder_val, bool):
                        inorder = inorder_val
                    elif str(inorder_val).lower() in ("1", "0", "true", "false"):
                        inorder = str(inorder_val).lower() in ("1", "true")
                    else:
                        raise ValueError(
                            f"FULLTEXT inorder argument must be a boolean "
                            f"(true/false or 1/0), got {inorder_val!r}"
                        )

            if field_name is not None:
                result.conditions.append(
                    Condition(
                        field=field_name,
                        operator="FULLTEXT",
                        value=value,
                        negated=negated,
                        slop=slop,
                        inorder=inorder,
                    )
                )

        elif func_name == "FUZZY" and len(args) >= 2:
            field_name = args[0].name if isinstance(args[0], exp.Column) else None
            value = self._extract_literal_value(args[1])

            # Optional 3rd arg: fuzzy level (1, 2, or 3)
            fuzzy_level = None
            if len(args) >= 3:
                level_val = self._extract_literal_value(args[2])
                if level_val is not None:
                    if isinstance(level_val, bool):
                        raise ValueError(
                            f"FUZZY level argument must be an integer (got {level_val})"
                        )
                    if isinstance(level_val, float) and level_val != int(level_val):
                        raise ValueError(
                            f"FUZZY level argument must be an integer (got {level_val})"
                        )
                    fuzzy_level = int(level_val)

            if field_name is not None:
                result.conditions.append(
                    Condition(
                        field=field_name,
                        operator="FUZZY",
                        value=value,
                        negated=negated,
                        fuzzy_level=fuzzy_level,
                    )
                )

    def _extract_literal_value(self, expression, convert_dates: bool = False):
        """Extract a Python value from a sqlglot Literal or Neg expression.

        Args:
            expression: The sqlglot expression to extract from.
            convert_dates: If True, convert ISO 8601 date strings to Unix timestamps.
                          Default is False to avoid changing semantics for TEXT/TAG
                          fields. Date conversion should be handled by the translator
                          when the field type is known to be NUMERIC.

        Returns:
            The extracted value, or None if not a literal.
        """
        if isinstance(expression, exp.Literal):
            value = expression.this
            if expression.is_number:
                return int(value) if "." not in str(value) else float(value)
            # Check if string value is a date/datetime and convert to timestamp
            if convert_dates and isinstance(value, str):
                timestamp = self._parse_date_to_timestamp(value)
                if timestamp is not None:
                    return timestamp
            return value
        elif isinstance(expression, exp.Boolean):
            # Handle TRUE/FALSE keywords parsed by sqlglot
            return expression.this
        elif isinstance(expression, exp.Neg):
            # Handle negative numbers: Neg(Literal(122.4)) -> -122.4
            inner_value = self._extract_literal_value(expression.this)
            if inner_value is not None:
                return -inner_value
        return None

    def _validate_geo_unit(self, unit_val: object) -> str:
        """Validate and normalize a geo distance unit.

        Args:
            unit_val: The unit value to validate.

        Returns:
            Normalized unit string (lowercase).

        Raises:
            ValueError: If the unit is not supported.
        """
        normalized_unit = str(unit_val).lower()
        if normalized_unit not in {"m", "km", "mi", "ft"}:
            raise ValueError(
                f"Unsupported geo distance unit: {unit_val!r}. "
                "Supported units are 'm', 'km', 'mi', 'ft'."
            )
        return normalized_unit

    def _parse_date_to_timestamp(self, value: str) -> int | None:
        """Parse an ISO 8601 date/datetime string to Unix timestamp.

        Delegates to module-level parse_date_to_timestamp function.
        """
        return parse_date_to_timestamp(value)
