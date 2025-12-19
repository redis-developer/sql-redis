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

        # Extract SELECT fields
        select = ast.find(exp.Select)
        if select:
            for expression in select.expressions:
                if isinstance(expression, exp.Column):
                    result.fields.append(expression.name)
                elif isinstance(expression, exp.Star):
                    result.fields.append("*")

        return result

