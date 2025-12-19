"""SQL analyzer component - resolves field types from schema."""

from dataclasses import dataclass, field

from sql_redis.parser import ParsedQuery, AggregationSpec, ComputedField, Condition


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
            c for c in self.parsed.conditions
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
        raise NotImplementedError("Analyzer.analyze is not yet implemented")

