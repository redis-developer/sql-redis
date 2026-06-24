# FT.HYBRID Implementation Sketch (Design C+)

**Purpose:** Concrete code examples showing how to implement the `HybridFusionSpec` primitive in sql-redis.

## 1. Parser Changes (parser.py)

### New Data Classes

```python
@dataclass
class TextRankingLeg:
    """Text ranking leg for hybrid fusion."""
    field: str              # TEXT field name
    query: str | None       # Search query string
    alias: str              # Score alias (e.g., "tscore")
    scorer: str = "BM25"    # BM25, TFIDF, DISMAX

@dataclass
class VectorRankingLeg:
    """Vector ranking leg for hybrid fusion."""
    field: str              # VECTOR field name
    alias: str              # Score alias (e.g., "vscore")
    k: int | None = None    # KNN K (defaults to window if None)

@dataclass
class HybridFusionSpec:
    """Specification for FT.HYBRID server-side fusion.

    Represents two independently-ranked legs (text + vector) fused by
    RRF or LINEAR combination. Mutually exclusive with VectorSearchSpec
    (filter-then-KNN).
    """
    text_leg: TextRankingLeg
    vector_leg: VectorRankingLeg
    fusion_method: str      # "RRF" or "LINEAR"

    # RRF parameters
    rrf_constant: int = 60

    # LINEAR parameters
    linear_alpha: float = 0.5
    linear_beta: float = 0.5

    # Common parameters
    window: int = 20        # Fusion window size
```

### Update ParsedQuery

```python
@dataclass
class ParsedQuery:
    # ... existing fields ...
    vector_search: VectorSearchSpec | None = None     # Filter-then-KNN (existing)
    hybrid_fusion: HybridFusionSpec | None = None     # Server-side fusion (NEW)
    # Note: vector_search and hybrid_fusion are mutually exclusive
```

### Detection Logic in _process_order_by

```python
def _process_order_by(self, order: exp.Order, result: ParsedQuery) -> None:
    """Process ORDER BY clause, detecting fusion functions."""
    for ordered in order.expressions:
        expression = ordered.this

        # Check if it's a fusion function: rrf() or linear()
        if isinstance(expression, exp.Anonymous):
            func_name = expression.name.upper()

            if func_name in ("RRF", "LINEAR"):
                self._build_hybrid_fusion_spec(expression, func_name, result)
                continue

        # Regular ORDER BY field
        field_name = expression.name if isinstance(expression, exp.Column) else None
        direction = "DESC" if ordered.args.get("desc") else "ASC"
        if field_name:
            result.orderby_fields.append((field_name, direction))

def _build_hybrid_fusion_spec(
    self, expression: exp.Anonymous, fusion_method: str, result: ParsedQuery
) -> None:
    """Build HybridFusionSpec from rrf() or linear() in ORDER BY.

    Expected signatures:
    - rrf(vscore, tscore [, constant => 60] [, window => 20])
    - linear(vscore, tscore [, alpha => 0.5] [, beta => 0.5] [, window => 20])
    """
    args = expression.expressions
    if len(args) < 2:
        raise ValueError(
            f"{fusion_method.lower()}() requires at least 2 arguments: "
            f"{fusion_method.lower()}(vector_alias, text_alias), got {len(args)}"
        )

    # Extract aliases (first two args)
    vector_alias = args[0].name if isinstance(args[0], exp.Column) else None
    text_alias = args[1].name if isinstance(args[1], exp.Column) else None

    if not vector_alias or not text_alias:
        raise ValueError(
            f"{fusion_method.lower()}() requires column aliases: "
            f"{fusion_method.lower()}(vscore, tscore)"
        )

    # Find the corresponding vector_distance and fulltext in SELECT
    vector_leg = self._find_vector_leg(result, vector_alias)
    text_leg = self._find_text_leg(result, text_alias)

    if not vector_leg:
        raise ValueError(
            f"No vector_distance(...) AS {vector_alias} found in SELECT"
        )
    if not text_leg:
        raise ValueError(
            f"No fulltext(...) AS {text_alias} found in SELECT"
        )

    # Extract fusion parameters from kwargs
    params = self._extract_fusion_params(args[2:], fusion_method)

    # Build HybridFusionSpec
    result.hybrid_fusion = HybridFusionSpec(
        text_leg=text_leg,
        vector_leg=vector_leg,
        fusion_method=fusion_method,
        **params
    )

    # Clear vector_search since hybrid_fusion takes precedence
    result.vector_search = None

def _find_vector_leg(self, result: ParsedQuery, alias: str) -> VectorRankingLeg | None:
    """Find vector_distance(...) AS alias in SELECT."""
    if result.vector_search and result.vector_search.alias == alias:
        return VectorRankingLeg(
            field=result.vector_search.field,
            alias=alias,
            k=result.vector_search.k
        )
    return None

def _find_text_leg(self, result: ParsedQuery, alias: str) -> TextRankingLeg | None:
    """Find fulltext(...) AS alias in SELECT.

    This requires detecting fulltext() in SELECT (not WHERE).
    """
    # Look for a computed field or condition with FULLTEXT operator
    # that has the matching alias
    for cond in result.conditions:
        if cond.operator == "FULLTEXT" and hasattr(cond, "alias") and cond.alias == alias:
            return TextRankingLeg(
                field=cond.field,
                query=cond.value,
                alias=alias,
                scorer="BM25"  # Default; could be configurable
            )
    return None

def _extract_fusion_params(self, kwarg_exprs: list, fusion_method: str) -> dict:
    """Extract kwargs like constant => 60, window => 20."""
    params = {}

    for expr in kwarg_exprs:
        if isinstance(expr, exp.EQ):
            key = expr.this.name if isinstance(expr.this, exp.Column) else None
            value = self._extract_literal_value(expr.expression)

            if key and value is not None:
                params[key] = value

    return params
```

## 2. Analyzer Changes (analyzer.py)

### New Data Class

```python
@dataclass
class HybridFusionAnalysis:
    """Analyzed hybrid fusion with validated field types."""
    spec: HybridFusionSpec
    text_field_type: str     # Confirmed TEXT from schema
    vector_field_type: str   # Confirmed VECTOR from schema
    filters: list[Condition] # Per-leg filters (applied to both SEARCH and VSIM)
```

### Update AnalyzedQuery

```python
@dataclass
class AnalyzedQuery:
    # ... existing fields ...
    hybrid_fusion: HybridFusionAnalysis | None = None
```

### Validation Logic

```python
def analyze(self, parsed: ParsedQuery) -> AnalyzedQuery:
    # ... existing logic ...

    if parsed.hybrid_fusion:
        hybrid_fusion = self._analyze_hybrid_fusion(parsed)
    else:
        hybrid_fusion = None

    return AnalyzedQuery(
        # ... existing fields ...
        hybrid_fusion=hybrid_fusion
    )

def _analyze_hybrid_fusion(self, parsed: ParsedQuery) -> HybridFusionAnalysis:
    """Validate hybrid fusion spec against schema."""
    spec = parsed.hybrid_fusion
    schema = self._schemas[parsed.index]

    # Validate text field is TEXT
    text_field_type = schema.get(spec.text_leg.field)
    if not text_field_type:
        raise ValueError(
            f"Text field '{spec.text_leg.field}' not found in index '{parsed.index}'"
        )
    if text_field_type != "TEXT":
        raise ValueError(
            f"Text field '{spec.text_leg.field}' must be TEXT, got {text_field_type}"
        )

    # Validate vector field is VECTOR
    vector_field_type = schema.get(spec.vector_leg.field)
    if not vector_field_type:
        raise ValueError(
            f"Vector field '{spec.vector_leg.field}' not found in index '{parsed.index}'"
        )
    if vector_field_type != "VECTOR":
        raise ValueError(
            f"Vector field '{spec.vector_leg.field}' must be VECTOR, got {vector_field_type}"
        )

    # Extract filters from conditions (exclude hybrid-related conditions)
    filters = [
        c for c in parsed.conditions
        if c.field != spec.text_leg.field or c.operator != "FULLTEXT"
    ]

    return HybridFusionAnalysis(
        spec=spec,
        text_field_type=text_field_type,
        vector_field_type=vector_field_type,
        filters=filters
    )
```

## 3. Translator Changes (translator.py)

### Command Dispatch

```python
def _build_command(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    """Build Redis command from analyzed query.

    Dispatch order:
    1. FT.HYBRID (hybrid fusion)
    2. FT.AGGREGATE (aggregations/groupby)
    3. FT.SEARCH (default)
    """
    if analyzed.hybrid_fusion:
        return self._build_ft_hybrid(analyzed)
    elif analyzed.use_aggregate:
        return self._build_ft_aggregate(analyzed)
    else:
        return self._build_ft_search(analyzed)
```

### FT.HYBRID Builder

```python
def _build_ft_hybrid(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    """Build FT.HYBRID command."""
    fusion = analyzed.hybrid_fusion.spec
    parsed = analyzed.parsed

    # Build filter expression
    filter_expr = self._build_filter_expr(analyzed.hybrid_fusion.filters)

    # Build SEARCH leg
    text_query = f"@{fusion.text_leg.field}:({fusion.text_leg.query})"
    if filter_expr:
        text_query = f"({text_query}) {filter_expr}"
    search_leg = f'SEARCH "{text_query}" YIELD_SCORE_AS {fusion.text_leg.alias}'

    # Build VSIM leg
    k = fusion.vector_leg.k or fusion.window
    filter_count = len(analyzed.hybrid_fusion.filters)
    vsim_parts = [
        f'VSIM @{fusion.vector_leg.field} $vec',
    ]
    if filter_count > 0:
        vsim_parts.append(f'FILTER {filter_count} "{filter_expr}"')
    vsim_parts.append(f'KNN 2 K {k}')
    vsim_parts.append(f'YIELD_SCORE_AS {fusion.vector_leg.alias}')
    vsim_leg = ' '.join(vsim_parts)

    # Build COMBINE clause
    if fusion.fusion_method == "RRF":
        combine = f'COMBINE RRF 2 CONSTANT {fusion.rrf_constant} WINDOW {fusion.window}'
    else:  # LINEAR
        combine = (
            f'COMBINE LINEAR 2 '
            f'ALPHA {fusion.linear_alpha} '
            f'BETA {fusion.linear_beta} '
            f'WINDOW {fusion.window}'
        )

    # Build LOAD clause
    load_fields = parsed.fields if parsed.fields else ["*"]
    load_clause = ["LOAD", str(len(load_fields))] + load_fields

    # Build LIMIT clause
    offset = parsed.offset or 0
    limit = parsed.limit or 10
    limit_clause = ["LIMIT", str(offset), str(limit)]

    # Assemble command
    cmd = [
        "FT.HYBRID",
        parsed.index,
        "2",  # Number of legs
        search_leg,
        vsim_leg,
        combine,
        *load_clause,
        *limit_clause,
        "DIALECT", "2"
    ]

    return TranslatedQuery(
        command="FT.HYBRID",
        args=cmd[1:],
        index=parsed.index,
        return_fields=parsed.fields
    )
```

## 4. Executor Changes (executor.py)

### Version Gating

```python
def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
    """Execute SQL query against Redis."""
    params = params or {}

    # Substitute and translate
    sql = _substitute_params(sql, params)
    translated = self._translator.translate(sql)

    # Version gate for FT.HYBRID
    if translated.command == "FT.HYBRID":
        self._check_hybrid_support()

    # Execute command
    # ... existing logic ...

def _check_hybrid_support(self) -> None:
    """Check if Redis supports FT.HYBRID (8.4+)."""
    info = self._client.info("server")
    version = info.get("redis_version", "0.0.0")

    if not self._meets_version(version, "8.4.0"):
        raise ValueError(
            f"FT.HYBRID requires Redis 8.4 or later, found {version}. "
            "Use filter-then-KNN syntax (vector_distance without fulltext in SELECT) instead."
        )

def _meets_version(self, current: str, required: str) -> bool:
    """Check if current version >= required version."""
    current_parts = [int(x) for x in current.split(".")[:3]]
    required_parts = [int(x) for x in required.split(".")]

    for c, r in zip(current_parts, required_parts):
        if c > r:
            return True
        if c < r:
            return False
    return True
```

## Summary

This implementation sketch shows:

1. **Parser:** Detect fusion functions in `ORDER BY`, build `HybridFusionSpec`.
2. **Analyzer:** Validate field types, separate filters.
3. **Translator:** Dispatch to `_build_ft_hybrid()` when `hybrid_fusion` is present.
4. **Executor:** Gate on Redis 8.4+ before executing `FT.HYBRID`.

All changes are **additive** and **backward compatible** with existing filter-then-KNN queries.


