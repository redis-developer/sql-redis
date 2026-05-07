# Agent Guide for sql-redis

This document is for AI agents that want to **use** sql-redis to query Redis.
For agents that want to **modify** the library itself, see
[`docs/for-ais-only/`](docs/for-ais-only/).

## What sql-redis is

A SQL-to-Redis translator. It accepts a SQL `SELECT` string, looks up the
target index's schema in Redis, and emits a `FT.SEARCH` or `FT.AGGREGATE`
command. The library does not invent its own query language: SQL goes in,
Redis search results come out as a `QueryResult(rows, count)`.

## When to reach for it

- The agent has a SQL string (from a planner, an LLM, an ORM, a user) and
  wants to run it against an existing RediSearch / RedisVL index.
- The agent needs vector search, full-text search, GEO filters, or date
  filtering and wants a single uniform interface instead of building Redis
  command lists by hand.
- The target index already exists. sql-redis does not run `FT.CREATE`. If
  there is no index, the query fails. Create the index first.

## When NOT to reach for it

- Plain key-value `GET`/`SET` work, list/set/sorted-set commands, streams,
  pub/sub. Use `redis-py` directly for those; sql-redis is a search-only
  translator.
- Writes. There is no `INSERT`, `UPDATE`, or `DELETE`. Write through `redis-py`.
- Index creation. Use `FT.CREATE` directly via `redis-py` first.
- Cross-index joins, subqueries, `HAVING`, `DISTINCT`. Not implemented.

## The minimum useful snippet

```python
from redis import Redis
from sql_redis import create_executor

client = Redis()
executor = create_executor(client)        # lazy schema loading; no I/O yet

result = executor.execute(
    "SELECT title, price FROM products WHERE category = :cat AND price < :max LIMIT 10",
    params={"cat": "electronics", "max": 500},
)

for row in result.rows:                    # row keys are bytes by default
    print(row[b"title"], row[b"price"])
```

Pass `decode_responses=True` to the `Redis` client if the agent prefers string
keys.

## Surface map

| Task | Symbol |
|---|---|
| Run a SQL query | [`Executor.execute`](sql_redis/executor.py) / [`AsyncExecutor.execute`](sql_redis/executor.py) |
| Build an executor | [`create_executor`](sql_redis/executor.py) / [`create_async_executor`](sql_redis/executor.py) |
| Read or refresh schema | [`SchemaRegistry`](sql_redis/schema.py) / [`AsyncSchemaRegistry`](sql_redis/schema.py) |
| Translate without executing | [`Translator.translate`](sql_redis/translator.py) returns a `TranslatedQuery` |

Full reference, generated from docstrings, is at `docs/api/`.

## Gotchas an agent should know

1. **Field-key types.** Result rows come back with `bytes` keys when the
   underlying `Redis` client uses default `decode_responses=False`. Use
   `Redis(decode_responses=True)` if you want string keys end-to-end.
2. **Parameter substitution is token-based.** `:id` will not match inside
   `:product_id`. String values are SQL-escaped automatically (`O'Brien`
   becomes `'O''Brien'`). Pass `bytes` for vector parameters; they are
   substituted as raw bytes after translation.
3. **`=` on a TEXT field is exact-phrase, not tokenized.** Use `fulltext()`
   for tokenized AND search. See `docs/user_guide/how_to_guides/text-search.md`.
4. **Stopwords are stripped automatically** before queries reach Redis. A
   `UserWarning` is emitted when this happens. Index with `STOPWORDS 0` to
   keep them.
5. **`IS NULL` requires Redis 7.4+** and `INDEXMISSING` declared on the field.
6. **Lazy schema loading is the default.** The first query that touches an
   index issues one `FT.INFO`. Pass `schema_cache_strategy="load_all"` to
   `create_executor` if you want to fail fast on missing indexes at startup.
7. **No JOIN, subquery, HAVING, or DISTINCT.** The translator raises
   `ValueError`; do not retry with rephrasing.
8. **GEO uses `POINT(lon, lat)` order.** Longitude first, matching Redis.

## Error model an agent can expect

| Exception | Meaning | Recovery |
|---|---|---|
| `ValueError` from `Translator.translate` | The SQL referenced an unknown index, unknown field, or unsupported clause. | Inspect the message; the index/field name is included. Do not retry the same SQL. |
| `redis.ResponseError` from `Executor.execute` | Redis rejected the generated command. The most common cases are wrong field type or `INDEXMISSING` not declared. | Check the index schema. The library wraps `ismissing()` failures with a hint about Redis 7.4 + `INDEXMISSING`. |
| `UserWarning` (not raised) | Stopwords stripped, or `IS NULL` used without `INDEXMISSING` declared. | Informational; does not affect the result. |

## Discoverable artifacts in this repo

| Artifact | Purpose |
|---|---|
| [`docs/llms.txt`](docs/llms.txt) | Flat index of every doc page with one-line summaries. Cheap to grep, good for in-context injection. |
| [`docs/api/`](docs/api/) | Sphinx `autoclass` reference for every public symbol. Source of truth for method signatures. |
| [`docs/user_guide/how_to_guides/`](docs/user_guide/how_to_guides/) | Task-oriented recipes (vector search, GEO, dates, async, parameters). |
| [`docs/concepts/`](docs/concepts/) | Why-style explanation: architecture, parameter substitution, schema-aware translation. |
| [`docs/for-ais-only/`](docs/for-ais-only/) | Internal repo map for agents modifying the library. |

## Hub context

sql-redis sits in the [Redis AI Hub](https://redis.io/ai-hub/) under the
*Experimental* tier as "SQL for Redis". Public docs URL:
[`docs.redisvl.com/projects/sql-redis/`](https://docs.redisvl.com/projects/sql-redis/).
The hub's docs standards (Diataxis layout, autoclass-driven API reference,
AI-agent affordances) are documented at
[`HUB_DOCS_STANDARDS.md`](https://github.com/redis/docs/blob/main/HUB_DOCS_STANDARDS.md)
in the hub repo.
