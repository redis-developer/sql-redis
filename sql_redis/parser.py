"""SQL parser component using sqlglot."""

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp


@dataclass
class AggregationSpec:
    """Specification for an aggregation function."""
    function: str
    field: str | None = None
    alias: str | None = None


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
class ParsedQuery:
    """Result of parsing a SQL query."""
    index: str = ""
    fields: list[str] = field(default_factory=list)
    conditions: list[Condition] = field(default_factory=list)
    boolean_operator: str = "AND"
    aggregations: list[AggregationSpec] = field(default_factory=list)
    computed_fields: list[ComputedField] = field(default_factory=list)
    vector_search: VectorSearchSpec | None = None
    groupby_fields: list[str] = field(default_factory=list)
    orderby_fields: list[tuple[str, str]] = field(default_factory=list)  # (field, ASC|DESC)
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
        elif isinstance(expression, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
            # Aggregation function
            func_name = expression.key.upper()
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
        elif isinstance(expression, exp.Anonymous):
            # Custom function call (e.g., vector_distance) - check before exp.Func
            # since Anonymous is a subclass of Func
            func_name = expression.name.lower()
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

        # Get field name from left side
        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name
        elif isinstance(expression.this, exp.Anonymous):
            # Function call like DISTANCE(location, POINT(...))
            # Extract field from first argument
            func_name = expression.this.name.upper()
            if expression.this.expressions:
                first_arg = expression.this.expressions[0]
                if isinstance(first_arg, exp.Column):
                    field_name = first_arg.name
                    # Use function name as operator prefix
                    operator = f"{func_name}_{operator}"

        # Get value from right side
        if isinstance(expression.expression, exp.Literal):
            value = expression.expression.this
            # Convert numeric strings to numbers
            if expression.expression.is_number:
                value = int(value) if "." not in str(value) else float(value)

        if field_name is not None:
            result.conditions.append(
                Condition(field=field_name, operator=operator, value=value, negated=negated)
            )

    def _add_between_condition(
        self, expression, result: ParsedQuery, negated: bool
    ) -> None:
        """Add a BETWEEN condition."""
        field_name = None
        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name

        low = expression.args.get("low")
        high = expression.args.get("high")

        low_val = self._extract_literal_value(low)
        high_val = self._extract_literal_value(high)

        if field_name is not None:
            result.conditions.append(
                Condition(
                    field=field_name,
                    operator="BETWEEN",
                    value=(low_val, high_val),
                    negated=negated,
                )
            )

    def _add_in_condition(
        self, expression, result: ParsedQuery, negated: bool
    ) -> None:
        """Add an IN condition."""
        field_name = None
        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name

        values = [self._extract_literal_value(e) for e in expression.expressions]

        if field_name is not None:
            result.conditions.append(
                Condition(field=field_name, operator="IN", value=values, negated=negated)
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
                    Condition(field=field_name, operator="FULLTEXT", value=value, negated=negated)
                )

    def _extract_literal_value(self, expression):
        """Extract a Python value from a sqlglot Literal."""
        if isinstance(expression, exp.Literal):
            value = expression.this
            if expression.is_number:
                return int(value) if "." not in str(value) else float(value)
            return value
        return None
