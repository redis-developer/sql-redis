# FT.HYBRID Proposal Documents

**JIRA:** [RAAE-1322](https://redislabs.atlassian.net/browse/RAAE-1322)  
**Status:** Design recommendation phase  
**Date:** 2026-06-23

## Overview

This directory contains design documents for adding `FT.HYBRID` support to sql-redis. The proposal introduces a new primitive abstraction that cleanly expresses server-side hybrid fusion (text + vector search combined via RRF or LINEAR).

## Documents

### 1. [ft-hybrid.md](ft-hybrid.md) - Original Proposal
**Author:** Robert Shelton  
**Purpose:** Initial spec with three syntax designs (A, B, C)

Contains:
- Goal: Enable server-side fusion (RRF/LINEAR) instead of filter-then-KNN
- Three syntax designs (A: overload, B: hybrid() predicate, C: composable)
- SQL → FT.HYBRID mapping
- Implementation plan by layer
- Testing strategy

**Key decision point:** Design C (composable, fusion in ORDER BY) was recommended.

### 2. [ft-hybrid-primitive-design.md](ft-hybrid-primitive-design.md) - Design Recommendation ⭐
**Author:** Claude (review of original)  
**Purpose:** Identify the core design issue and propose a better primitive

**Main insight:** The original designs treat hybrid fusion as a **syntax mapping problem** rather than a **data structure problem**. This document proposes:

- **New primitive:** `HybridFusionSpec` dataclass that encapsulates text leg + vector leg + fusion config
- **Design C+:** Keep Design C's SQL syntax but use dedicated primitives internally
- **Type-driven dispatch:** Command path is determined by data structure, not heuristics

**Key benefits:**
- Single source of truth for fusion state
- Explicit over implicit
- Clean extensibility (add fusion methods, add legs)
- No risk of breaking existing filter-then-KNN

### 3. [ft-hybrid-code-comparison.md](ft-hybrid-code-comparison.md) - Concrete Comparison
**Purpose:** Side-by-side code showing Design C vs Design C+

Compares:
- Parser output (scattered state vs single spec)
- Translator dispatch (heuristics vs type-driven)
- Command builder (extraction logic vs direct access)

**Takeaway:** Design C+ is cleaner, more maintainable, and safer.

### 4. [ft-hybrid-implementation-sketch.md](ft-hybrid-implementation-sketch.md) - Code Sketch
**Purpose:** Concrete implementation examples for Design C+

Shows:
- Data class definitions (TextRankingLeg, VectorRankingLeg, HybridFusionSpec)
- Parser detection logic (_process_order_by, _build_hybrid_fusion_spec)
- Analyzer validation (_analyze_hybrid_fusion)
- Translator command builder (_build_ft_hybrid)
- Executor version gating (_check_hybrid_support)

**Takeaway:** All changes are additive and backward compatible.

## Recommended Path Forward

**Adopt Design C+ (primitive-based) for the following reasons:**

1. **Better data model:** `HybridFusionSpec` makes fusion first-class, not inferred.
2. **Cleaner code:** No heuristics, no scattered state, no conditional detection.
3. **Safer extension:** Adding new fusion methods or legs doesn't require refactoring.
4. **Backward compatible:** Existing vector_distance() queries work unchanged.

## SQL Syntax (Final Recommendation)

```sql
SELECT page_text, file_id,
       vector_distance(embedding, :vec) AS vscore,
       fulltext(page_text, 'quarterly earnings') AS tscore
FROM "KM_abc123"
WHERE ticker = 'MSFT'
ORDER BY rrf(vscore, tscore, constant => 60) DESC
LIMIT 10;
```

**Detection rules:**
- `vector_distance(...) AS <alias>` in SELECT → vector leg
- `fulltext(...) AS <alias>` in SELECT → text leg
- `rrf()` or `linear()` in ORDER BY → fusion trigger

If all three are present → `FT.HYBRID`.  
If only vector_distance → `FT.SEARCH` with KNN (existing behavior).

## Implementation Checklist

- [ ] Add `HybridFusionSpec`, `TextRankingLeg`, `VectorRankingLeg` to parser.py
- [ ] Add detection logic in `SQLParser._process_order_by()`
- [ ] Add `HybridFusionAnalysis` to analyzer.py with field type validation
- [ ] Add `Translator._build_ft_hybrid()` command builder
- [ ] Add version gating in `Executor.execute()` (Redis 8.4+ check)
- [ ] Bump test Redis image to 8.4+ in conftest.py
- [ ] Add unit tests for parser, analyzer, translator
- [ ] Add integration tests in test_sql_queries.py
- [ ] Update docs: relabel "hybrid" → "filtered KNN", add "Hybrid Fusion" guide
- [ ] Update AGENTS.md and llms.txt with FT.HYBRID info

**Estimated effort:** 3-4 days (implementation + tests + docs)

## Open Questions

1. **Fusion defaults:** Require explicit `rrf()/linear()` or default to RRF?  
   **Recommendation:** Require explicit (fail fast on ambiguity).

2. **K vs WINDOW:** Default `K = window` or make independent?  
   **Recommendation:** Default `K = window` for v1; add kwarg later if needed.

3. **Filter distribution:** Apply WHERE to both legs or SEARCH-only?  
   **Recommendation:** Both legs (consistency > simplicity).

4. **Score surfacing:** Auto-project fused score or require explicit SELECT?  
   **Recommendation:** Let users SELECT it explicitly if needed.

## Next Steps

1. Confirm design decision (C+ vs C) with stakeholders.
2. Confirm Redis 8.4 availability in test environment.
3. Begin implementation with parser/analyzer/translator changes.
4. Add tests at each layer (unit → integration).
5. Update docs and deploy.

## Related Issues

- **Terminology fix:** Existing docs call filter-then-KNN "hybrid search" but that's not `FT.HYBRID`. Need to relabel to "filtered KNN" vs "hybrid fusion".
- **RedisVL alignment:** If RedisVL adds FT.HYBRID support, mirror parameter names (constant, window, alpha, beta).

---

**Questions?** See the individual docs above or reach out on RAAE-1322.
