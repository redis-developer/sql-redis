# `FT.SEARCH` versus `FT.AGGREGATE`

Redis exposes two top-level search commands, and they are not interchangeable. sql-redis picks one per query based on what the SQL asks for. Knowing which command runs for a given SQL is useful: it predicts what features can combine, what cannot, and what the result rows will look like.

## What the two commands do

**`FT.SEARCH`** is the document-retrieval command. Given a query string, it finds matching documents and returns them with their fields. It supports relevance scoring (`WITHSCORES` and BM25), score-based sorting, and the optimised `GEOFILTER` clause. It cannot compute aggregations, group, or apply post-query filters.

**`FT.AGGREGATE`** is the analytical command. It runs a query and then a pipeline: `LOAD`, `APPLY`, `GROUPBY`, `REDUCE`, `FILTER`, `SORTBY`. It is the only path to `COUNT`, `SUM`, `AVG`, computed columns, date-function projections, and post-aggregation filtering. It does not support `WITHSCORES`.

## Which SQL forces which command

The translator picks `FT.AGGREGATE` if **any** of the following is true. Otherwise it uses `FT.SEARCH`.

| Trigger | Why |
|---|---|
| `SELECT COUNT(*)`, `SUM(x)`, `AVG(x)`, `MIN(x)`, `MAX(x)` | Aggregations require `REDUCE`. |
| `GROUP BY` | `GROUPBY` is an aggregate-only pipeline stage. |
| Computed expression in `SELECT` (`SELECT price * 0.9 AS d`) | Needs `APPLY`. |
| `geo_distance(...)` in `SELECT` | The distance is computed via `APPLY`. |
| `geo_distance(...) > N` or `>= N` or `BETWEEN ... AND ...` in `WHERE` | `FT.SEARCH`'s `GEOFILTER` only handles "within radius". Greater-than is implemented as a post-query `FILTER`. |
| `YEAR(field)`, `MONTH(field)`, etc. anywhere | Date functions are computed via `APPLY`; predicates on them become `FILTER`. |
| `HAVING exists(field) = 1` (or any `HAVING`) | `FILTER` is aggregate-only. |

Equivalently, `FT.SEARCH` runs when the query is a pure document fetch: `SELECT field-list FROM idx WHERE ... ORDER BY ... LIMIT ...`, optionally with `score()`, optionally with `geo_distance(...) <`/`<=` filters that fit `GEOFILTER`.

## Combinations that are not allowed

Some pairs are forced incompatible by the underlying Redis commands; sql-redis surfaces this with a `ValueError` rather than silently dropping a clause.

- **`score()` plus aggregation, GROUP BY, `geo_distance` >/>=/BETWEEN, date functions, or HAVING.** `score()` requires `WITHSCORES`, which is `FT.SEARCH` only. Anything that forces `FT.AGGREGATE` therefore conflicts. Error message: *"score() is not supported with FT.AGGREGATE queries"*.
- **`OR` combined with `geo_distance(...) >/>=/BETWEEN`.** The greater-than family is implemented as a top-level `FILTER`, which is ANDed with the rest of the query. Combining it with `OR` at the SQL level would silently change semantics. Error: *"Geo distance comparisons (>, >=, BETWEEN) cannot be combined with OR"*.
- **`OR` combined with date-function predicates** for the same reason. Date predicates become `FILTER` clauses.

These constraints are not bugs to fix; they are the cost of the abstraction. The translator could in principle synthesise post-query workarounds, but doing so would break the user's expectation that one SQL produces one Redis command.

## Why this matters for callers

Two practical consequences:

1. **The result-row shape changes.** `FT.SEARCH` returns rows with field-value pairs straight from the indexed documents, possibly with a score column. `FT.AGGREGATE` returns rows of computed fields, group keys, and reduced values; the original document fields are present only if the SQL asks for them. See {doc}`result-shape`.
2. **`LIMIT` semantics differ subtly.** Both commands honour `LIMIT`, but the `count` returned by `FT.AGGREGATE` reflects the post-pipeline row count, while `FT.SEARCH`'s `count` is the total match count regardless of the limit. The library exposes both as `QueryResult.count`; the meaning depends on which path ran.

If you need to know which command was issued for a given SQL, call `Translator.translate(sql)` directly and inspect `TranslatedQuery.command`.
