"""SQL analyzer component - resolves field types from schema."""

from __future__ import annotations

from dataclasses import dataclass, field

from sql_redis.parser import (
    AggregationSpec,
    ComputedField,
    Condition,
    DateFunctionSpec,
    HybridSearchSpec,
    ParsedQuery,
)


@dataclass
class VectorSearchAnalysis:
    """Analyzed vector search details."""

    field: str
    k: int
    alias: str


@dataclass
class HybridSearchAnalysis:
    """Analyzed FT.HYBRID fusion search details.

    Wraps the parsed spec and adds the resolved KNN ``k`` (from LIMIT). Field
    types are validated during analysis (text leg must be TEXT, vector leg must
    be VECTOR).
    """

    spec: HybridSearchSpec
    k: int


@dataclass
class AnalyzedQuery:
    """Result of analyzing a parsed SQL query with schema context."""

    parsed: ParsedQuery = field(default_factory=ParsedQuery)
    field_types: dict[str, str] = field(default_factory=dict)
    aggregations: list[AggregationSpec] = field(default_factory=list)
    computed_fields: list[ComputedField] = field(default_factory=list)
    date_functions: list[DateFunctionSpec] = field(default_factory=list)
    groupby_fields: list[str] = field(default_factory=list)
    is_global_aggregation: bool = False
    vector_search: VectorSearchAnalysis | None = None
    hybrid_search: HybridSearchAnalysis | None = None
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

        # Fields from filters (HAVING exists(field))
        for filter_expr in parsed.filters:
            for field_name in schema.keys():
                if field_name in filter_expr:
                    referenced_fields.add(field_name)

        # Fields from vector search
        if parsed.vector_search:
            referenced_fields.add(parsed.vector_search.field)

        # Fields from hybrid fusion search (both legs)
        if parsed.hybrid_search:
            referenced_fields.add(parsed.hybrid_search.vector_field)
            referenced_fields.add(parsed.hybrid_search.text_field)

        # Fields from date functions (YEAR, MONTH, etc.)
        for date_func in parsed.date_functions:
            referenced_fields.add(date_func.field)

        # Collect aliases from date functions, computed fields, the score()
        # alias, and aggregation aliases. These are computed in the query
        # pipeline rather than loaded from documents, so GROUP BY / ORDER BY
        # references to them must not be looked up in the schema.
        alias_names = {df.alias for df in parsed.date_functions}
        alias_names.update(cf.alias for cf in parsed.computed_fields)
        if parsed.scoring is not None:
            alias_names.add(parsed.scoring.alias)
        for agg in parsed.aggregations:
            if agg.alias:
                alias_names.add(agg.alias)
        if parsed.vector_search is not None and parsed.vector_search.alias:
            # ORDER BY <vector-distance-alias> is the canonical way to sort by
            # KNN similarity; the alias is a computed column, not an indexed
            # field, so it must not be looked up in the schema.
            alias_names.add(parsed.vector_search.alias)
        if parsed.hybrid_search is not None and parsed.hybrid_search.alias:
            # ORDER BY <combined-score-alias> sorts by the fused score; like the
            # vector alias, it is a computed column, not an indexed field.
            alias_names.add(parsed.hybrid_search.alias)

        # Fields from GROUP BY (exclude aliases since they're computed)
        for field_name in parsed.groupby_fields:
            if field_name not in alias_names:
                referenced_fields.add(field_name)

        # Fields from ORDER BY (so they are validated against the schema
        # and available for LOAD in the FT.AGGREGATE path)
        for field_name, _ in parsed.orderby_fields:
            if field_name not in alias_names:
                referenced_fields.add(field_name)

        # Resolve field types
        for field_name in referenced_fields:
            if field_name not in schema:
                raise ValueError(f"Unknown field: {field_name}")
            result.field_types[field_name] = schema[field_name]

        # Copy aggregations, computed fields, and date functions
        result.aggregations = parsed.aggregations
        result.computed_fields = parsed.computed_fields
        result.date_functions = parsed.date_functions
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

        # Analyze hybrid fusion search
        if parsed.hybrid_search:
            spec = parsed.hybrid_search
            vector_type = schema.get(spec.vector_field)
            if vector_type != "VECTOR":
                raise ValueError(
                    f"hybrid_vector_search() vector leg field "
                    f"'{spec.vector_field}' must be a VECTOR field, "
                    f"got {vector_type}."
                )
            text_type = schema.get(spec.text_field)
            if text_type != "TEXT":
                raise ValueError(
                    f"hybrid_vector_search() text leg field "
                    f"'{spec.text_field}' must be a TEXT field, got {text_type}."
                )
            result.hybrid_search = HybridSearchAnalysis(
                spec=spec,
                k=parsed.limit or spec.k or 10,
            )
            # Conditions become per-leg filters.
            result.has_prefilter = len(parsed.conditions) > 0

        return result
