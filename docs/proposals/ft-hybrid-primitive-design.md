# FT.HYBRID Primitive Design - Recommendation

**Status:** Design recommendation for RAAE-1322
**Supersedes:** Design discussion in `ft-hybrid.md`
**Date:** 2026-06-23

## Executive Summary

After reviewing the `ft-hybrid.md` proposal and the sql-redis codebase, I recommend **Design C with composable ranking functions**, but with a significant improvement: introduce a **new primitive abstraction** that better encapsulates hybrid fusion and aligns with sql-redis's design philosophy.

## Core Issue with Current Proposal

The three designs (A, B, C) in `ft-hybrid.md` all treat hybrid fusion as a **syntax mapping problem** rather than a **data structure problem**. The proposals focus on where to put `rrf()` or `hybrid()` in SQL, but don't address the deeper design question:

**How should hybrid fusion be represented in sql-redis's internal data model?**

Currently, sql-redis has:
- `VectorSearchSpec` - represents a single vector leg
- `Condition` - represents text/filter predicates
- `ScoringSpec` - represents relevance scoring (BM25, etc.)

None of these primitives can cleanly express **two independent ranking signals fused together**. Adding `FT.HYBRID` support by bolting detection logic onto existing specs will create technical debt.

## Recommended Primitive: `HybridFusionSpec`

### Data Structure

```python
@dataclass
class TextRankingLeg:
    """Text ranking leg for hybrid fusion."""
    field: str              # TEXT field to search
    query: str | None       # Search query text
    alias: str              # Score alias (e.g., "tscore")
    scorer: str = "BM25"    # BM25, TFIDF, DISMAX

@dataclass
class VectorRankingLeg:
    """Vector ranking leg for hybrid fusion."""
    field: str              # VECTOR field to search
    alias: str              # Score alias (e.g., "vscore")
    k: int | None = None    # KNN candidate pool size

@dataclass
class HybridFusionSpec:
    """Specification for FT.HYBRID fusion."""
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

This primitive makes hybrid fusion **first-class** instead of inferring it from the presence of multiple specs.

### Integration into ParsedQuery

```python
@dataclass
class ParsedQuery:
    # ... existing fields ...
    vector_search: VectorSearchSpec | None = None     # For filter-then-KNN
    hybrid_fusion: HybridFusionSpec | None = None     # For FT.HYBRID fusion
```

**Mutual exclusion:** A query has EITHER `vector_search` (filter-then-KNN) OR `hybrid_fusion` (server-side fusion), never both.

## Why This Is Better

### 1. **Explicit over implicit**
Design C detects `fulltext(...) AS tscore` + `vector_distance(...) AS vscore` + `rrf(tscore, vscore)` and infers hybrid mode. The new primitive makes the intent **explicit** in the data model.

### 2. **Single source of truth**
All hybrid parameters live in `HybridFusionSpec`. No need to reconcile state scattered across `VectorSearchSpec`, `Condition`, and `ORDER BY`.

### 3. **Clear validation**
The analyzer can enforce:
- `text_leg.field` is TYPE `TEXT`
- `vector_leg.field` is TYPE `VECTOR`
- Fusion method matches parameters (RRF → constant, LINEAR → alpha/beta)
- Filters are compatible with both legs

### 4. **Command-path clarity**
```python
if analyzed.hybrid_fusion:
    return build_ft_hybrid_command(analyzed)
elif analyzed.vector_search:
    return build_ft_search_knn_command(analyzed)
else:
    return build_ft_search_or_aggregate_command(analyzed)
```

No branching on combinations of flags — the data structure dictates the path.

### 5. **Extensibility**
When Redis adds more fusion methods (e.g., `DBSF`, `CombSUM`), you add a new `fusion_method` value. When `FT.HYBRID` gains a third leg (e.g., sparse vector), you add `SparseVectorRankingLeg`.

## Recommended SQL Syntax (Design C+)

Keep Design C's composability, but tighten the semantics around the new primitive:

```sql
SELECT page_text, file_id,
       vector_distance(embedding, :vec) AS vscore,
       fulltext(page_text, 'quarterly earnings') AS tscore
FROM "KM_abc123"
WHERE ticker = 'MSFT'
ORDER BY rrf(vscore, tscore, constant => 60) DESC
LIMIT 10;
```

### Detection Logic (Parser)

The parser builds `HybridFusionSpec` when **all** of these are true:

1. `SELECT` contains `vector_distance(vec_field, :param) AS <vec_alias>`
2. `SELECT` contains `fulltext(text_field, 'query') AS <text_alias>`
3. `ORDER BY` contains `rrf(<vec_alias>, <text_alias>, ...)` or `linear(<vec_alias>, <text_alias>, ...)`

If only (1) is present → `VectorSearchSpec` (filter-then-KNN, existing behavior).
If (1) + (2) but no fusion in `ORDER BY` → `ValueError("ambiguous: use rrf() or linear() to specify fusion")`.

This forces users to be explicit about fusion vs. returning two separate scores.

### Why Not Design B (`hybrid()` predicate)?

Design B is more compact:

```sql
WHERE hybrid(page_text, 'quarterly earnings', embedding, :vec)
```

But it has problems:

1. **Breaks the separation of concerns.** `WHERE` is for filtering; ranking belongs in `SELECT`/`ORDER BY`.
2. **No place for score aliases.** Users can't reference `tscore`/`vscore` separately for debugging.
3. **Harder to extend.** Adding scorer config (BM25 vs TFIDF), KNN params, or window size requires kwargs in `WHERE`, which is unnatural.

Design C keeps ranking in `SELECT`/`ORDER BY` where it belongs.

## Analyzer Changes

```python
@dataclass
class AnalyzedQuery:
    # ... existing fields ...
    hybrid_fusion: HybridFusionAnalysis | None = None

@dataclass
class HybridFusionAnalysis:
    """Analyzed hybrid fusion with resolved field types."""
    spec: HybridFusionSpec
    text_field_type: str     # Confirmed TEXT from schema
    vector_field_type: str   # Confirmed VECTOR from schema
    filters: list[Condition] # Per-leg filters (applied to both SEARCH and VSIM)
```

The analyzer:
1. Validates `text_leg.field` exists and is `TEXT`
2. Validates `vector_leg.field` exists and is `VECTOR`
3. Splits `WHERE` conditions into per-leg filters (no special text-leg-only logic — apply to both for consistency)
4. Computes KNN `K` from `window` if not explicitly set

## Translator Changes

Add a new command-path branch **before** the search-vs-aggregate decision:

```python
def _build_command(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    if analyzed.hybrid_fusion:
        return self._build_ft_hybrid(analyzed)
    elif analyzed.use_aggregate:
        return self._build_ft_aggregate(analyzed)
    else:
        return self._build_ft_search(analyzed)

def _build_ft_hybrid(self, analyzed: AnalyzedQuery) -> TranslatedQuery:
    """Build FT.HYBRID command from HybridFusionAnalysis."""
    fusion = analyzed.hybrid_fusion.spec

    # Build SEARCH leg
    search_query = build_text_query(fusion.text_leg.field, fusion.text_leg.query)
    search_filter = build_filter_expr(analyzed.hybrid_fusion.filters)
    search_leg = f'SEARCH "{search_query} {search_filter}" YIELD_SCORE_AS {fusion.text_leg.alias}'

    # Build VSIM leg
    vsim_filter_count = len(analyzed.hybrid_fusion.filters)
    k = fusion.vector_leg.k or fusion.window
    vsim_leg = (
        f'VSIM @{fusion.vector_leg.field} $vec '
        f'FILTER {vsim_filter_count} "{search_filter}" '
        f'KNN 2 K {k} YIELD_SCORE_AS {fusion.vector_leg.alias}'
    )

    # Build COMBINE clause
    if fusion.fusion_method == "RRF":
        combine = f'COMBINE RRF 2 CONSTANT {fusion.rrf_constant} WINDOW {fusion.window}'
    else:  # LINEAR
        combine = (
            f'COMBINE LINEAR 2 ALPHA {fusion.linear_alpha} '
            f'BETA {fusion.linear_beta} WINDOW {fusion.window}'
        )

    # Assemble command
    cmd = [
        "FT.HYBRID", analyzed.parsed.index, "2",
        search_leg,
        vsim_leg,
        combine,
        "LOAD", str(len(analyzed.parsed.fields)), *analyzed.parsed.fields,
        "LIMIT", str(analyzed.parsed.offset or 0), str(analyzed.parsed.limit or 10),
        "DIALECT", "2"
    ]

    return TranslatedQuery(command="FT.HYBRID", args=cmd[1:], ...)
```

This is **much cleaner** than trying to reuse `_build_ft_search` and injecting hybrid logic via conditionals.

## Version Gating

Add a Redis version check in the executor:

```python
def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
    translated = self._translator.translate(sql)

    if translated.command == "FT.HYBRID":
        # Check Redis version >= 8.4
        info = self._client.info("server")
        version = info.get("redis_version", "0.0.0")
        if not _meets_version(version, "8.4.0"):
            raise ValueError(
                f"FT.HYBRID requires Redis 8.4+, found {version}. "
                "Use filter-then-KNN syntax instead."
            )

    # ... execute command ...
```

Fail **fast** with a clear message rather than letting Redis return a cryptic error.

## Migration Path

This design is **backward compatible**:

- Existing `vector_distance()` queries without `fulltext()` → still build `VectorSearchSpec` → still emit `FT.SEARCH` with KNN.
- Existing `fulltext()` queries in `WHERE` → still build `Condition` → still work as filters.

No existing queries break. Hybrid fusion is purely **additive**.

## Comparison to Original Designs

| Aspect | Design A | Design B | Design C (original) | **Design C+ (new primitive)** |
|--------|----------|----------|---------------------|-------------------------------|
| Syntax clarity | ❌ Ambiguous | ✅ Compact | ✅ Composable | ✅ Composable + explicit |
| Data model | ❌ Inferred | ⚠️ New WHERE func | ⚠️ Scattered state | ✅ Dedicated primitive |
| Extensibility | ❌ Breaking | ⚠️ Kwargs hell | ⚠️ Inference fragile | ✅ Clean extension points |
| Command path | ❌ Heuristics | ⚠️ Detection | ⚠️ Detection | ✅ Type-driven dispatch |
| Score surfacing | ❌ Unclear | ❌ No aliases | ✅ Explicit aliases | ✅ Explicit aliases |
| Backward compat | ❌ Breaking | ✅ New func | ✅ Additive | ✅ Additive |

**Verdict:** Design C+ (composable syntax + dedicated primitive) is the cleanest path forward.

## Open Questions for Confirmation

1. **Fusion defaults:** Should `ORDER BY DESC` on a score without `rrf()/linear()` default to RRF, or require explicit fusion? **Recommendation:** Require explicit fusion (fail fast on ambiguity).

2. **Score surfacing:** Should the fused score be auto-projected as a column (e.g., `__fused_score`)? **Recommendation:** Let users explicitly `SELECT` it via an alias if needed.

3. **K vs WINDOW:** Should KNN `K` default to `window`, or be independently configurable? **Recommendation:** Default `K = window` for v1; add `k =>` kwarg to `rrf()/linear()` later if needed.

4. **Filter distribution:** Apply `WHERE` filters to both legs, or SEARCH-only? **Recommendation:** Both legs (consistency > simplicity).

## Next Steps

1. Implement `HybridFusionSpec`, `TextRankingLeg`, `VectorRankingLeg` dataclasses in `parser.py`.
2. Add detection logic in `SQLParser._process_order_by()` to build `HybridFusionSpec` when fusion functions are present.
3. Add `HybridFusionAnalysis` to analyzer and validation logic.
4. Implement `Translator._build_ft_hybrid()` command builder.
5. Add version gating in executor.
6. Update docs to relabel "hybrid search" → "filtered KNN" and add "Hybrid Fusion (FT.HYBRID)" guide.
7. Bump test Redis image to 8.4+ and add integration tests.

**Estimated effort:** 3-4 days for implementation + tests + docs (assuming no blockers on Redis 8.4 availability).


