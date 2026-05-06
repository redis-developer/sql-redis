# SQL Syntax Reference

The complete catalog of SQL clauses, operators, and functions sql-redis recognises, with their RediSearch translation.

## Supported

| Clause / feature | Status |
|---|---|
| `SELECT` field list and `*` | yes |
| `SELECT expr AS alias` (computed fields) | yes |
| `WHERE` with `TEXT`, `NUMERIC`, `TAG`, `GEO` | yes |
| Comparison operators `=`, `!=`, `<`, `<=`, `>`, `>=` | yes |
| `BETWEEN` | yes |
| `IN` | yes |
| Boolean `AND`, `OR`, `NOT` | yes |
| Aggregations `COUNT`, `SUM`, `AVG`, `MIN`, `MAX` | yes |
| `GROUP BY` | yes |
| `ORDER BY` `ASC` / `DESC` | yes |
| `LIMIT` and `OFFSET` | yes |
| Vector KNN via `vector_distance(field, :param)` | yes |
| Hybrid search (filters + vector) | yes |
| Full-text modes (exact phrase, fuzzy, proximity, OR, `LIKE`, BM25) | yes |
| GEO via `geo_distance(field, POINT(lon, lat))` | yes |
| Date functions (`YEAR`, `MONTH`, `DAY`, `DATE_FORMAT`, ...) | yes |
| `IS NULL` / `IS NOT NULL` (Redis 7.4+) | yes |
| `exists()` for field presence | yes |

## Not supported

| Clause | Why |
|---|---|
| `JOIN` | Redis has no cross-index join. |
| Subqueries | Out of scope for the POC. |
| `HAVING` | Out of scope (use `WHERE` plus `GROUP BY` where possible). |
| `DISTINCT` | Out of scope. |
| `CREATE INDEX` | sql-redis does not create schemas. Use `FT.CREATE`. |

## TEXT search

| Feature | SQL syntax | RediSearch output |
|---|---|---|
| Exact phrase | `title = 'gaming laptop'` | `@title:"gaming laptop"` |
| Tokenized AND | `fulltext(title, 'gaming laptop')` | `@title:(gaming laptop)` |
| Fuzzy LD=1 | `fuzzy(title, 'laptap')` | `@title:%laptap%` |
| Fuzzy LD=2 | `fuzzy(title, 'laptap', 2)` | `@title:%%laptap%%` |
| Fuzzy LD=3 | `fuzzy(title, 'laptap', 3)` | `@title:%%%laptap%%%` |
| OR / union | `fulltext(title, 'a OR b')` | `@title:(a\|b)` |
| Prefix | `title LIKE 'lap%'` | `@title:lap*` |
| Suffix | `title LIKE '%top'` | `@title:*top` |
| Contains | `title LIKE '%apt%'` | `@title:*apt*` |
| Proximity (slop) | `fulltext(title, 'a b', 2)` | `@title:(a b) => { $slop: 2; }` |
| Proximity + order | `fulltext(title, 'a b', 2, true)` | `@title:(a b) => { $slop: 2; $inorder: true; }` |
| Optional term | `fulltext(title, 'a ~b')` | `@title:(a ~b)` |
| Negation | `NOT fulltext(title, 'x')` | `-@title:x` |
| BM25 score | `score() AS rel` | `WITHSCORES` |

See {doc}`/user_guide/how_to_guides/text-search` for a task-oriented walkthrough.

## GEO

| Feature | Notes |
|---|---|
| Coordinates | `POINT(lon, lat)`, longitude first |
| Default unit | meters |
| Units | `m`, `km`, `mi`, `ft` |
| Operators | `<`, `<=`, `>`, `>=`, `BETWEEN` |
| In `SELECT` | `geo_distance(loc, POINT(lon, lat)) AS d` |

See {doc}`/user_guide/how_to_guides/geo-queries`.

## Date functions

| SQL function | Redis function | Notes |
|---|---|---|
| `YEAR(field)` | `year(@field)` | |
| `MONTH(field)` | `monthofyear(@field)` | 0-11 |
| `DAY(field)` | `dayofmonth(@field)` | 1-31 |
| `HOUR(field)` | `hour(@field)` | |
| `MINUTE(field)` | `minute(@field)` | |
| `DAYOFWEEK(field)` | `dayofweek(@field)` | 0 = Sunday |
| `DAYOFYEAR(field)` | `dayofyear(@field)` | 0-365 |
| `DATE_FORMAT(field, fmt)` | `timefmt(@field, fmt)` | `SELECT` only |

ISO 8601 date and datetime literals in `WHERE` are converted to Unix timestamps automatically.

See {doc}`/user_guide/how_to_guides/date-queries`.

## Missing fields

| Feature | SQL | Output |
|---|---|---|
| Filter by absence | `WHERE email IS NULL` | `ismissing(@email)` |
| Filter by presence | `WHERE email IS NOT NULL` | `-ismissing(@email)` |
| Add 0/1 column | `SELECT exists(email) AS has_email` | `APPLY exists(@email)` |
| Filter via aggregate | `HAVING exists(email) = 1` | `FILTER` after `APPLY` |

`IS NULL` requires Redis 7.4+ and `INDEXMISSING` on the field.

See {doc}`/user_guide/how_to_guides/missing-fields`.
