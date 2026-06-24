# FT.HYBRID: Design C vs Design C+ Code Comparison

**Purpose:** Show concrete code differences between the original Design C (detection-based) and the recommended Design C+ (primitive-based).

## Scenario: Parse this SQL

```sql
SELECT page_text, file_id,
       vector_distance(embedding, :vec) AS vscore,
       fulltext(page_text, 'quarterly earnings') AS tscore
FROM "KM_abc123"
WHERE ticker = 'MSFT'
ORDER BY rrf(vscore, tscore, constant => 60) DESC
LIMIT 10;
```

---

## Design C (Original): Detection-Based

### Parser Output

```python
ParsedQuery(
    index="KM_abc123",
    fields=["page_text", "file_id"],
    vector_search=VectorSearchSpec(
        field="embedding",
        alias="vscore",
        k=None
    ),
    conditions=[
        Condition(field="page_text", operator="FULLTEXT", value="quarterly earnings"),
        Condition(field="ticker", operator="=", value="MSFT")
    ],
    orderby_fields=[("vscore", "DESC")],  # ??? How to represent rrf(vscore, tscore)?
    # Problem: No clear place to store fusion method + parameters
    # Requires adding fields like:
    # fusion_method: str | None = None
    # fusion_text_alias: str | None = None
    # fusion_params: dict | None = None
)
```

**Issues:**
1. `fulltext()` in SELECT is represented as a `Condition`, even though it's not a filter.
2. Fusion method (`rrf`) and its parameters are scattered across multiple optional fields.
3. `orderby_fields` can't represent `rrf(vscore, tscore)` cleanly.
4. Analyzer needs heuristics to detect "this is fusion, not filter-then-KNN."

### Translator Logic

```python
def _build_command(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    # Heuristic detection: if we have vector_search AND a FULLTEXT condition 
    # that's not in WHERE, assume hybrid fusion
    has_text_in_select = any(
        c.operator == "FULLTEXT" and c not in analyzed.filters 
        for c in analyzed.parsed.conditions
    )
    
    if analyzed.vector_search and has_text_in_select:
        # Infer this is FT.HYBRID
        if analyzed.parsed.fusion_method == "RRF":
            return self._build_ft_hybrid_rrf(analyzed)
        elif analyzed.parsed.fusion_method == "LINEAR":
            return self._build_ft_hybrid_linear(analyzed)
        else:
            raise ValueError("Fusion method not detected")
    elif analyzed.vector_search:
        # Filter-then-KNN
        return self._build_ft_search(analyzed)
    # ...
```

**Problems:**
- Lots of conditional logic to distinguish fusion from filter-then-KNN.
- Hard to extend when adding new fusion methods.
- Fragile: what if someone writes `fulltext()` in SELECT but doesn't want fusion?

---

## Design C+ (Recommended): Primitive-Based

### Parser Output

```python
ParsedQuery(
    index="KM_abc123",
    fields=["page_text", "file_id"],
    hybrid_fusion=HybridFusionSpec(
        text_leg=TextRankingLeg(
            field="page_text",
            query="quarterly earnings",
            alias="tscore",
            scorer="BM25"
        ),
        vector_leg=VectorRankingLeg(
            field="embedding",
            alias="vscore",
            k=None
        ),
        fusion_method="RRF",
        rrf_constant=60,
        window=20
    ),
    conditions=[
        Condition(field="ticker", operator="=", value="MSFT")
    ]
)
```

**Benefits:**
1. All hybrid fusion state lives in one object.
2. `fulltext()` is explicitly a ranking signal, not a filter.
3. Filters are separate from ranking legs.
4. No ambiguity: if `hybrid_fusion` is set, it's `FT.HYBRID`.

### Translator Logic

```python
def _build_command(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    if analyzed.hybrid_fusion:
        return self._build_ft_hybrid(analyzed)
    elif analyzed.vector_search:
        return self._build_ft_search(analyzed)
    elif analyzed.use_aggregate:
        return self._build_ft_aggregate(analyzed)
    else:
        return self._build_ft_search(analyzed)
```

**Benefits:**
- **Type-driven dispatch:** the data structure dictates the command path.
- No heuristics, no inference, no scattered state.
- Easy to add new fusion methods: just add a field to `HybridFusionSpec`.

---

## Side-by-Side: Building FT.HYBRID Command

### Design C (Detection-Based)

```python
def _build_ft_hybrid_rrf(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    # Extract text query from conditions
    text_cond = next(c for c in analyzed.parsed.conditions if c.operator == "FULLTEXT")
    text_field = text_cond.field
    text_query = text_cond.value
    text_alias = analyzed.parsed.fusion_text_alias or "tscore"
    
    # Extract vector field from vector_search
    vector_field = analyzed.vector_search.field
    vector_alias = analyzed.vector_search.alias or "vscore"
    
    # Extract fusion params
    rrf_constant = analyzed.parsed.fusion_params.get("constant", 60)
    window = analyzed.parsed.fusion_params.get("window", 20)
    
    # Build filters
    filters = [c for c in analyzed.parsed.conditions if c.operator != "FULLTEXT"]
    filter_expr = self._build_filter_expr(filters)
    
    # Assemble command
    search_leg = f'SEARCH "(@{text_field}:({text_query})) {filter_expr}" YIELD_SCORE_AS {text_alias}'
    vsim_leg = f'VSIM @{vector_field} $vec FILTER 1 "{filter_expr}" KNN 2 K {window} YIELD_SCORE_AS {vector_alias}'
    combine = f'COMBINE RRF 2 CONSTANT {rrf_constant} WINDOW {window}'
    # ...
```

**Problems:**
- Scattered extraction logic.
- Fragile assumptions (e.g., "first FULLTEXT condition is the ranking signal").
- Duplicate filter-building logic.

### Design C+ (Primitive-Based)

```python
def _build_ft_hybrid(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    fusion = analyzed.hybrid_fusion.spec
    
    # All fusion state is in one place
    text_field = fusion.text_leg.field
    text_query = fusion.text_leg.query
    text_alias = fusion.text_leg.alias
    
    vector_field = fusion.vector_leg.field
    vector_alias = fusion.vector_leg.alias
    k = fusion.vector_leg.k or fusion.window
    
    # Filters are already separated
    filter_expr = self._build_filter_expr(analyzed.hybrid_fusion.filters)
    
    # Assemble command
    search_leg = f'SEARCH "(@{text_field}:({text_query})) {filter_expr}" YIELD_SCORE_AS {text_alias}'
    vsim_leg = f'VSIM @{vector_field} $vec FILTER 1 "{filter_expr}" KNN 2 K {k} YIELD_SCORE_AS {vector_alias}'
    
    if fusion.fusion_method == "RRF":
        combine = f'COMBINE RRF 2 CONSTANT {fusion.rrf_constant} WINDOW {fusion.window}'
    else:  # LINEAR
        combine = f'COMBINE LINEAR 2 ALPHA {fusion.linear_alpha} BETA {fusion.linear_beta} WINDOW {fusion.window}'
    # ...
```

**Benefits:**
- All data is accessible via the spec.
- No searching through conditions.
- Handles both RRF and LINEAR in one method.

---

## Summary

| Aspect | Design C (Detection) | Design C+ (Primitive) |
|--------|----------------------|----------------------|
| **Data model** | Scattered across `vector_search`, `conditions`, optional fields | Single `HybridFusionSpec` object |
| **Parser complexity** | Must distinguish "fulltext as filter" vs "fulltext as ranking" | Explicit: `WHERE fulltext()` = filter, `SELECT fulltext()` = ranking leg |
| **Translator dispatch** | Heuristics and inference | Type-driven (if `hybrid_fusion`, call `_build_ft_hybrid`) |
| **Command builder** | Separate methods for RRF/LINEAR, duplication | Single method, `if fusion_method` switch |
| **Extensibility** | Add more optional fields, more heuristics | Add fields to `HybridFusionSpec`, no heuristics |
| **Backward compat** | Risk of breaking filter-then-KNN if detection misfires | Safe: new field, existing queries untouched |

**Recommendation:** Use Design C+ (primitive-based) to avoid technical debt and make the codebase easier to maintain as `FT.HYBRID` evolves.
