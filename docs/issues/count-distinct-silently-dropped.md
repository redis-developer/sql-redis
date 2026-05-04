# `COUNT(DISTINCT x)` silently degrades to `COUNT(*)` (DISTINCT and column dropped)

## Summary
`COUNT(DISTINCT field)` in a SQL `SELECT` is parsed as a plain `COUNT(*)` and translated to `REDUCE COUNT 0`. Both the `DISTINCT` modifier and the column reference are silently discarded — no error is raised, but the query returns the total row count instead of the number of distinct values. The same code path also mis-handles other `<agg>(DISTINCT x)` forms (`SUM`, `AVG`, …), producing malformed reducers with no field.

## Reproduction

```python
from sql_redis.translator import Translator
t = Translator(...)  # schema with TAG `category` and TEXT `title`

t.translate(
    "SELECT category, COUNT(DISTINCT title) AS unique_titles "
    "FROM idx GROUP BY category"
).args
# Current:
#   ['GROUPBY', '1', '@category', 'REDUCE', 'COUNT', '0', 'AS', 'unique_titles', ...]
# Expected:
#   ['GROUPBY', '1', '@category', 'REDUCE', 'COUNT_DISTINCT', '1', '@title',
#    'AS', 'unique_titles', ...]
```

The user-observed behavior, summarized: `COUNT(DISTINCT x)` → `(COUNT x)` (i.e. it becomes an unqualified `COUNT` reducer with the column / `DISTINCT` dropped).

A working today-only workaround is the non-standard `COUNT_DISTINCT(title)` form, which the parser routes through its `redis_reducers` set:

```sql
SELECT category, COUNT_DISTINCT(title) AS unique_titles FROM idx GROUP BY category
```

## Root cause

In `sql_redis/parser.py::_process_select_expression_inner`, the aggregation branch only recognizes `exp.Column` and `exp.Star` as the inner expression:

```python
elif isinstance(
    expression,
    (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max,
     exp.Stddev, exp.Variance, exp.FirstValue, exp.ArrayAgg),
):
    func_name = expression.key.upper()
    ...
    field_name = None
    if expression.this:
        if isinstance(expression.this, exp.Column):
            field_name = expression.this.name
        elif isinstance(expression.this, exp.Star):
            field_name = None  # COUNT(*)
    result.aggregations.append(
        AggregationSpec(function=func_name, field=field_name, alias=alias)
    )
```

For `COUNT(DISTINCT title)`, sqlglot produces `exp.Count(this=exp.Distinct(expressions=[Column("title")]))`. Neither isinstance check matches `exp.Distinct`, so `field_name` stays `None` and `function` stays `"COUNT"` — the `AggregationSpec` is indistinguishable from `COUNT(*)`.

In `sql_redis/translator.py::_build_aggregate`, the COUNT branch then ignores `field` entirely:

```python
args.append("REDUCE")
args.append(agg.function.upper())
if agg.function.upper() == "COUNT":
    args.append("0")
elif agg.field:
    nargs = 1 + len(agg.extra_args)
    args.append(str(nargs))
    args.append(f"@{agg.field}")
    args.extend(agg.extra_args)
else:
    args.append("0")
```

So the emitted reducer is `REDUCE COUNT 0`, regardless of what was inside the `COUNT(...)`.

## Impact
- `COUNT(DISTINCT x)` returns wrong (always-greater-or-equal) counts with no error or warning.
- `SUM(DISTINCT x)`, `AVG(DISTINCT x)`, `MIN(DISTINCT x)`, etc. fall through to the `else: args.append("0")` branch, producing malformed reducers (`REDUCE SUM 0`) which Redis will reject or evaluate incorrectly.
- Behavior diverges from standard SQL semantics that most users expect.

## Suggested fix

1. In `_process_select_expression_inner`, when handling the aggregation node types, detect `exp.Distinct` as the inner expression:

   ```python
   inner = expression.this
   is_distinct = False
   if isinstance(inner, exp.Distinct):
       is_distinct = True
       # exp.Distinct.expressions holds the column(s)
       if inner.expressions and isinstance(inner.expressions[0], exp.Column):
           field_name = inner.expressions[0].name
       else:
           raise ValueError(
               "DISTINCT inside aggregate expects a single column reference."
           )
   elif isinstance(inner, exp.Column):
       field_name = inner.name
   elif isinstance(inner, exp.Star):
       field_name = None
   ```

2. Map `<AGG>(DISTINCT x)` to the corresponding RediSearch reducer:
   - `COUNT(DISTINCT x)` → `COUNT_DISTINCT` (1 arg: `@x`).
   - For `SUM`, `AVG`, `MIN`, `MAX`, etc., RediSearch has no native distinct variant — these should raise a clear `ValueError` ("`SUM(DISTINCT ...)` is not supported by RediSearch; use `SUM` over a pre-deduplicated dataset or `COUNT_DISTINCT` if you only need cardinality").

3. Ensure the `_build_aggregate` COUNT special case (`function == "COUNT"` → always 0 args) only fires when `field is None`; otherwise emit `REDUCE COUNT_DISTINCT 1 @field`.

4. Multi-column `COUNT(DISTINCT a, b)` is not supported by RediSearch; reject it with a clear error.

## Tests to add

Unit (`tests/test_translator.py`):

- `SELECT category, COUNT(DISTINCT title) AS n FROM idx GROUP BY category` →
  args contain `REDUCE COUNT_DISTINCT 1 @title AS n`.
- `SELECT COUNT(DISTINCT title) FROM idx` (global aggregation) →
  args contain `GROUPBY 0 REDUCE COUNT_DISTINCT 1 @title`.
- `SELECT COUNT(*) FROM idx` keeps emitting `REDUCE COUNT 0` (no regression).
- `SELECT COUNT_DISTINCT(title) FROM idx` (existing path) continues to work and emits the same args as `COUNT(DISTINCT title)`.
- `SELECT SUM(DISTINCT price) FROM idx` raises a clear `ValueError`.
- `SELECT COUNT(DISTINCT a, b) FROM idx` raises a clear `ValueError`.

Integration (`tests/test_sql_queries.py`):

- `SELECT category, COUNT(DISTINCT title) AS n FROM products GROUP BY category` returns the same per-category cardinalities as the equivalent `COUNT_DISTINCT(title)` query.

## Workaround

Use the Redis-specific reducer name directly until this is fixed:

```sql
SELECT category, COUNT_DISTINCT(title) AS unique_titles
FROM idx
GROUP BY category
```
