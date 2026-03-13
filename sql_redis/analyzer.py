"""SQL analyzer component - resolves field types from schema."""

from __future__ import annotations

from dataclasses import dataclass, field

from sql_redis.parser import AggregationSpec, ComputedField, Condition, ParsedQuery


@dataclass
class VectorSearchAnalysis:
    """Analyzed vector search details."""

    field: str
    k: int
    alias: str


@dataclass
class AnalyzedQuery:
    """Result of analyzing a parsed SQL query with schema context."""

    parsed: ParsedQuery = field(default_factory=ParsedQuery)
    field_types: dict[str, str] = field(default_factory=dict)
    aggregations: list[AggregationSpec] = field(default_factory=list)
    computed_fields: list[ComputedField] = field(default_factory=list)
    groupby_fields: list[str] = field(default_factory=list)
    is_global_aggregation: bool = False
    vector_search: VectorSearchAnalysis | None = None
    has_prefilter: bool = False

    def get_field_type(self, field_name: str) -> str | None:
        """Get the type of a field."""
        return self.field_types.get(field_name)

    def get_conditions_by_type(self, field_type: str) -> list[Condition]:
        """Get conditions for fields of a specific type."""
        return [
            c
            for c in self.parsed.conditions
            if self.field_types.get(c.field) == field_type
        ]


class Analyzer:
    """Analyzes parsed SQL queries with schema context."""

    def __init__(self, schemas: dict[str, dict[str, str]]):
        """Initialize analyzer with schema registry data.

        Args:
            schemas: Dict mapping index names to field->type dicts.
        """
        self._schemas = schemas

    def analyze(self, parsed: ParsedQuery) -> AnalyzedQuery:
        """Analyze a parsed query, resolving field types.

        Args:
            parsed: The parsed SQL query.

        Returns:
            An AnalyzedQuery with field types resolved.

        Raises:
            ValueError: If the index or a field is unknown.
        """
        # Validate index exists
        if parsed.index not in self._schemas:
            raise ValueError(f"Unknown index: {parsed.index}")

        schema = self._schemas[parsed.index]
        result = AnalyzedQuery(parsed=parsed)

        # Collect all fields referenced in the query
        referenced_fields: set[str] = set()

        # Fields from SELECT
        for field_name in parsed.fields:
            if field_name != "*":
                referenced_fields.add(field_name)

        # Fields from conditions
        for condition in parsed.conditions:
            referenced_fields.add(condition.field)

        # Fields from geo_conditions
        for geo_cond in parsed.geo_conditions:
            referenced_fields.add(geo_cond.field)

        # Fields from geo_distance selects
        for geo_select in parsed.geo_distance_selects:
            referenced_fields.add(geo_select.field)

        # Fields from aggregations
        for agg in parsed.aggregations:
            if agg.field:
                referenced_fields.add(agg.field)

        # Fields from computed fields (extract field references from expressions)
        for computed in parsed.computed_fields:
            # Simple extraction - look for field names in the expression
            for field_name in schema.keys():
                if field_name in computed.expression:
                    referenced_fields.add(field_name)

        # Fields from vector search
        if parsed.vector_search:
            referenced_fields.add(parsed.vector_search.field)

        # Fields from GROUP BY
        for field_name in parsed.groupby_fields:
            referenced_fields.add(field_name)

        # Resolve field types
        for field_name in referenced_fields:
            if field_name not in schema:
                raise ValueError(f"Unknown field: {field_name}")
            result.field_types[field_name] = schema[field_name]

        # Copy aggregations and computed fields
        result.aggregations = parsed.aggregations
        result.computed_fields = parsed.computed_fields
        result.groupby_fields = parsed.groupby_fields

        # Determine if this is a global aggregation
        result.is_global_aggregation = (
            len(parsed.aggregations) > 0 and len(parsed.groupby_fields) == 0
        )

        # Analyze vector search
        if parsed.vector_search:
            result.vector_search = VectorSearchAnalysis(
                field=parsed.vector_search.field,
                k=parsed.limit or parsed.vector_search.k or 10,
                alias=parsed.vector_search.alias,
            )
            # Has prefilter if there are conditions
            result.has_prefilter = len(parsed.conditions) > 0

        return result
