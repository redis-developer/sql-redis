<div align="center">
    <img width="300" src="https://raw.githubusercontent.com/redis/redis-vl-python/main/docs/_static/Redis_Logo_Red_RGB.svg" alt="Redis">
    <h1>sql-redis</h1>
    <p><strong>SQL on top of RediSearch and RedisVL indexes</strong></p>
</div>

<div align="center">

**[Documentation](https://redis-developer.github.io/sql-redis/)** • **[GitHub](https://github.com/redis-developer/sql-redis)** • **[PyPI](https://pypi.org/project/sql-redis/)**

</div>

---

A SQL-to-Redis translator that converts SQL `SELECT` statements into Redis `FT.SEARCH` and `FT.AGGREGATE` commands. Query Redis collections with familiar SQL on top of RediSearch and RedisVL indexes.

## Install

```bash
pip install sql-redis
```

## Quick example

```python
from redis import Redis
from sql_redis import create_executor

client = Redis()
executor = create_executor(client)        # lazy schema loading; no I/O yet

# Simple query
result = executor.execute("""
    SELECT title, price
    FROM products
    WHERE category = 'electronics' AND price < 500
    ORDER BY price ASC
    LIMIT 10
""")

for row in result.rows:
    print(row[b"title"], row[b"price"])

# Vector search with parameter substitution
result = executor.execute(
    """
    SELECT title, vector_distance(embedding, :vec) AS score
    FROM products
    LIMIT 5
    """,
    params={"vec": vector_bytes},
)
```

Pass `decode_responses=True` to the `Redis` client if you want string keys instead of bytes.

## What's implemented

- [x] Basic `SELECT` with field selection
- [x] `WHERE` with TEXT, NUMERIC, TAG, GEO field types
- [x] Comparison operators: `=`, `!=`, `<`, `<=`, `>`, `>=`, `BETWEEN`, `IN`
- [x] Boolean operators: `AND`, `OR`, `NOT`
- [x] Aggregations: `COUNT`, `SUM`, `AVG`, `MIN`, `MAX`
- [x] `GROUP BY` with multiple aggregations
- [x] `ORDER BY` with `ASC`/`DESC`
- [x] `LIMIT` and `OFFSET` pagination
- [x] Computed fields: `price * 0.9 AS discounted`
- [x] Vector KNN search: `vector_distance(field, :param)`
- [x] Hybrid search (filters + vector)
- [x] Full-text search: exact phrase, fuzzy, proximity, OR/union, LIKE patterns, BM25 scoring
- [x] GEO field queries with full operator support
- [x] Date functions: `YEAR()`, `MONTH()`, `DAY()`, `DATE_FORMAT()`, etc.
- [x] `IS NULL` / `IS NOT NULL` via `ismissing()` (requires Redis 7.4+)
- [x] `exists()` function for field presence checks

## What's not implemented (yet)

- [ ] JOINs (Redis doesn't support cross-index joins)
- [ ] Subqueries
- [ ] HAVING clause
- [ ] DISTINCT
- [ ] Index creation from SQL (`CREATE INDEX`)

The translator raises `ValueError` for unsupported clauses; do not retry with rephrasing.

## How-to guides

The next sections are task-oriented recipes. Each shows the SQL syntax, the RediSearch command produced, and any gotchas.

- [TEXT search](#text-search)
- [`IS NULL` / `IS NOT NULL`](#is-null--is-not-null-ismissing)
- [`exists()` field presence](#exists--field-presence-check)
- [DATE / DATETIME handling](#datedatetime-handling)
- [Date functions](#date-functions)
- [GEO field support](#geo-field-support)

### TEXT search

Full-text search on TEXT fields with multiple search modes:

| Feature | SQL Syntax | RediSearch Output | Notes |
|---------|-----------|-------------------|-------|
| Exact phrase | `title = 'gaming laptop'` | `@title:"gaming laptop"` | Stopwords stripped |
| Tokenized search | `fulltext(title, 'gaming laptop')` | `@title:(gaming laptop)` | Stopwords stripped |
| Fuzzy LD=1 | `fuzzy(title, 'laptap')` | `@title:%laptap%` | |
| Fuzzy LD=2 | `fuzzy(title, 'laptap', 2)` | `@title:%%laptap%%` | |
| Fuzzy LD=3 | `fuzzy(title, 'laptap', 3)` | `@title:%%%laptap%%%` | |
| OR / union | `fulltext(title, 'laptop OR tablet')` | `@title:(laptop\|tablet)` | |
| Prefix | `title LIKE 'lap%'` | `@title:lap*` | |
| Suffix | `title LIKE '%top'` | `@title:*top` | |
| Contains | `title LIKE '%apt%'` | `@title:*apt*` | |
| Proximity (slop) | `fulltext(title, 'gaming laptop', 2)` | `@title:(gaming laptop) => { $slop: 2; }` | |
| Proximity + order | `fulltext(title, 'gaming laptop', 2, true)` | `@title:(gaming laptop) => { $slop: 2; $inorder: true; }` | |
| Optional term | `fulltext(title, 'laptop ~gaming')` | `@title:(laptop ~gaming)` | |
| BM25 score | `SELECT score() AS relevance FROM idx` | `FT.SEARCH ... WITHSCORES` | |
| Negation | `NOT fulltext(title, 'refurbished')` | `-@title:refurbished` | |

**Examples:**

```sql
-- Exact phrase match (stopwords like "of" are stripped automatically)
SELECT * FROM products WHERE title = 'bank of america'
-- Produces: @title:"bank america"

-- Fuzzy search for typos (Levenshtein distance 2)
SELECT * FROM products WHERE fuzzy(title, 'laptap', 2)

-- OR search across terms
SELECT * FROM products WHERE fulltext(title, 'laptop OR tablet OR phone')

-- Proximity: terms within 3 words of each other, in order
SELECT * FROM products WHERE fulltext(title, 'gaming laptop', 3, true)

-- Suffix/contains pattern matching
SELECT * FROM products WHERE title LIKE '%phone%'

-- BM25 relevance scoring
SELECT title, score() AS relevance FROM products WHERE fulltext(title, 'laptop')

-- Multi-field search
SELECT * FROM products WHERE fulltext(title, 'laptop') OR fulltext(description, 'laptop')
```

**Stopword handling:**

Both `=` (exact phrase) and `fulltext()` (tokenized search) automatically strip [Redis default stopwords](https://redis.io/docs/latest/develop/ai/search-and-query/advanced-concepts/stopwords/) before sending queries to RediSearch. This is necessary because RediSearch does not index stopwords, so including them in queries causes syntax errors or failed matches. A `UserWarning` is emitted when stopwords are removed.

For example, `WHERE title = 'bank of america'` produces `@title:"bank america"` because "of" is a default stopword and is never stored in the inverted index. The stripped phrase still matches correctly because the indexer assigns consecutive token positions after dropping stopwords.

To include stopwords in your queries, create your index with `STOPWORDS 0`:

```
FT.CREATE myindex ON HASH PREFIX 1 doc: STOPWORDS 0 SCHEMA title TEXT
```

**Notes:**
- `=` on TEXT fields performs **exact phrase** matching (double-quoted)
- `fulltext()` performs **tokenized** AND search (parenthesized)
- Both operators strip stopwords and emit a warning when they do
- `fuzzy()` and `fulltext()` only work on TEXT fields; using them on TAG or NUMERIC raises `ValueError`
- OR must be **uppercase**: `'laptop OR tablet'` triggers union; lowercase `'laptop or tablet'` is treated as a regular three-word AND search
- Special characters (`@`, `|`, `-`, `*`, `+`, etc.) in search terms are automatically escaped

### IS NULL / IS NOT NULL (ismissing)

Check for missing (absent) fields using standard SQL `IS NULL` / `IS NOT NULL` syntax. Requires **Redis 7.4+** (RediSearch 2.10+) with `INDEXMISSING` declared on the field.

| SQL | RediSearch Output |
|-----|-------------------|
| `WHERE email IS NULL` | `ismissing(@email)` |
| `WHERE email IS NOT NULL` | `-ismissing(@email)` |

```sql
-- Find users without an email
SELECT * FROM users WHERE email IS NULL

-- Find users with an email
SELECT * FROM users WHERE email IS NOT NULL

-- Combine with other filters
SELECT * FROM users WHERE category = 'eng' AND email IS NULL
```

**Note:** The field must be declared with `INDEXMISSING` in the index schema. A warning is emitted at translation time as a reminder.

### exists() — Field presence check

Check whether a field has a value using `exists()` in SELECT or HAVING. This uses `FT.AGGREGATE` with `APPLY exists(@field)`.

```sql
-- Check if fields exist (returns 1 or 0)
SELECT name, exists(email) AS has_email FROM users

-- Filter to only rows where a field exists
SELECT name FROM users HAVING exists(email) = 1

-- Combine with other computed fields
SELECT name, exists(email) AS has_email, exists(phone) AS has_phone FROM users
```

**Note:** `exists()` is different from `IS NOT NULL` — it works via `FT.AGGREGATE APPLY` and doesn't require `INDEXMISSING` on the field, but returns `1`/`0` rather than filtering rows directly.

### DATE/DATETIME handling

Redis does not have a native DATE field type. Dates are stored as **NUMERIC fields** with Unix timestamps.

**sql-redis automatically converts ISO 8601 date literals to Unix timestamps:**

```sql
-- Date literal (automatically converted to timestamp 1704067200)
SELECT * FROM events WHERE created_at > '2024-01-01'

-- Datetime literal with time
SELECT * FROM events WHERE created_at > '2024-01-01T12:00:00'

-- Date range with BETWEEN
SELECT * FROM events WHERE created_at BETWEEN '2024-01-01' AND '2024-01-31'

-- Multiple date conditions
SELECT * FROM events WHERE created_at > '2024-01-01' AND created_at < '2024-12-31'
```

**Supported date formats:**
- Date: `'2024-01-01'` (interpreted as midnight UTC)
- Datetime: `'2024-01-01T12:00:00'` or `'2024-01-01 12:00:00'`
- Datetime with timezone: `'2024-01-01T12:00:00Z'`, `'2024-01-01T12:00:00+00:00'`

**Note:** All dates without timezone are interpreted as UTC. You can also use raw Unix timestamps if preferred:

```sql
SELECT * FROM events WHERE created_at > 1704067200
```

### Date functions

Extract date parts using SQL functions that map to Redis `APPLY` expressions:

| SQL Function | Redis Function | Description |
|--------------|----------------|-------------|
| `YEAR(field)` | `year(@field)` | Extract year (e.g., 2024) |
| `MONTH(field)` | `monthofyear(@field)` | Extract month (0-11) |
| `DAY(field)` | `dayofmonth(@field)` | Extract day of month (1-31) |
| `HOUR(field)` | `hour(@field)` | Round to hour |
| `MINUTE(field)` | `minute(@field)` | Round to minute |
| `DAYOFWEEK(field)` | `dayofweek(@field)` | Day of week (0=Sunday) |
| `DAYOFYEAR(field)` | `dayofyear(@field)` | Day of year (0-365) |
| `DATE_FORMAT(field, fmt)` | `timefmt(@field, fmt)` | Format timestamp |

**Examples:**

```sql
-- Extract year and month
SELECT name, YEAR(created_at) AS year, MONTH(created_at) AS month FROM events

-- Filter by year
SELECT name FROM events WHERE YEAR(created_at) = 2024

-- Group by date parts
SELECT YEAR(created_at) AS year, COUNT(*) FROM events GROUP BY year

-- Format dates
SELECT name, DATE_FORMAT(created_at, '%Y-%m-%d') AS date FROM events
```

**Note:** Redis's `monthofyear()` returns 0-11 (not 1-12), and `dayofweek()` returns 0 for Sunday.

**Limitations:**
- `NOT YEAR(field) = 2024` is not supported (raises `ValueError`)
- `DATE_FORMAT()` is only supported in SELECT, not in WHERE (raises `ValueError`)
- Date functions combined with `OR` are not supported (raises `ValueError`)

### GEO field support

GEO fields are fully implemented with standard SQL-like syntax:

| Feature | Status |
|---------|--------|
| Coordinate order | `POINT(lon, lat)` — matches Redis native format |
| Default unit | Meters (`m`) — SQL standard |
| All operators | `<`, `<=`, `>`, `>=`, `BETWEEN` |
| Distance calculation | `geo_distance()` in SELECT clause |
| Combined filters | GEO + TEXT/TAG/NUMERIC |

**Coordinate order: `POINT(lon, lat)`**

Use **longitude first**, matching Redis's native GEO format:

```sql
-- San Francisco coordinates: lon=-122.4194, lat=37.7749
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000
```

**Units:**

| Unit | Code | Example |
|------|------|---------|
| Meters | `m` | `geo_distance(location, POINT(-122.4194, 37.7749)) < 5000` |
| Kilometers | `km` | `geo_distance(location, POINT(-122.4194, 37.7749), 'km') < 5` |
| Miles | `mi` | `geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 3` |
| Feet | `ft` | `geo_distance(location, POINT(-122.4194, 37.7749), 'ft') < 16400` |

Default is meters when no unit is specified.

**Operators:**

```sql
-- Less than (uses optimized GEOFILTER)
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- Less than or equal (uses optimized GEOFILTER)
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) <= 5000

-- Greater than (uses FT.AGGREGATE with FILTER)
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) > 100000

-- Greater than or equal (uses FT.AGGREGATE with FILTER)
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749)) >= 100000

-- Between (uses FT.AGGREGATE with FILTER)
SELECT name FROM stores WHERE geo_distance(location, POINT(-122.4194, 37.7749), 'km') BETWEEN 10 AND 100
```

**Distance calculation in SELECT:**

```sql
-- Get distance to each store (returns meters)
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749)) AS distance
FROM stores

-- With explicit unit
SELECT name, geo_distance(location, POINT(-122.4194, 37.7749), 'km') AS distance_km
FROM stores
```

**Combined filters:**

```sql
-- GEO + TAG filter
SELECT name FROM stores
WHERE category = 'retail' AND geo_distance(location, POINT(-122.4194, 37.7749)) < 5000

-- GEO + NUMERIC filter
SELECT name FROM stores
WHERE rating >= 4.0 AND geo_distance(location, POINT(-122.4194, 37.7749), 'mi') < 10

-- GEO + TEXT filter
SELECT name FROM stores
WHERE name = 'Downtown' AND geo_distance(location, POINT(-122.4194, 37.7749)) < 10000
```

## Concepts and design

The Diataxis "explanation" tier: why the library is shaped the way it is. The full versions live under [`docs/concepts/`](docs/concepts/).

### Why SQL instead of a pandas-like Python DSL?

| Approach | Example | Trade-offs |
|----------|---------|------------|
| **SQL** | `SELECT * FROM products WHERE price > 100` | Universal, well-understood, tooling exists |
| **Pandas-like** | `df[df.price > 100]` | Pythonic but limited to Python, no standard |
| **Builder pattern** | `query.select("*").where(price__gt=100)` | Type-safe but verbose, learning curve |

We chose SQL because:

1. **Universality** — SQL is the lingua franca of data. Developers, analysts, and tools all speak it.
2. **No new DSL to learn** — Users already know SQL. A pandas-like API requires learning our specific dialect.
3. **Tooling compatibility** — SQL strings can be generated by ORMs, query builders, or AI assistants.
4. **Clear mapping** — SQL semantics map reasonably well to RediSearch operations (SELECT→LOAD, WHERE→filter, GROUP BY→GROUPBY).

The downside is losing Python's type checking and IDE support, but for a query interface, the universality trade-off is worth it.

### Why sqlglot instead of writing a custom parser?

Options considered: custom parser (regex / hand-rolled recursive descent), PLY/Lark (parser generators), sqlparse (tokenizer only), and sqlglot (production SQL parser). We chose sqlglot because:

1. **Battle-tested** — Used in production by companies like Tobiko (SQLMesh). Handles edge cases we'd miss.
2. **Full AST** — Provides a complete abstract syntax tree, not just tokens. We can traverse and analyze queries properly.
3. **Dialect support** — Handles SQL variations. Users can write MySQL-style or PostgreSQL-style queries.
4. **Active maintenance** — Regular releases, responsive maintainers, good documentation.

Writing a custom parser would be error-prone and time-consuming for a POC. sqlglot lets us focus on the translation logic rather than parsing edge cases.

### Why schema-aware translation?

Redis field types determine query syntax:

| Field Type | Redis Syntax | Example |
|------------|--------------|---------|
| TEXT | `@field:term` | `@title:laptop` |
| NUMERIC | `@field:[min max]` | `@price:[100 500]` |
| TAG | `@field:{value}` | `@category:{books}` |

Without schema knowledge, we can't translate `category = 'books'` correctly — it could be `@category:books` (TEXT search) or `@category:{books}` (TAG exact match). The `SchemaRegistry` fetches index schemas via `FT.INFO` and the translator uses this to generate correct syntax per field type. This adds a Redis round-trip at initialization but ensures correct query generation.

### Architecture

```
SQL String
    ↓
┌─────────────────┐
│   SQLParser     │  Parse SQL → ParsedQuery dataclass
└────────┬────────┘
         ↓
┌─────────────────┐
│ SchemaRegistry  │  Load field types from Redis
└────────┬────────┘
         ↓
┌─────────────────┐
│    Analyzer     │  Classify conditions by field type
└────────┬────────┘
         ↓
┌─────────────────┐
│  QueryBuilder   │  Generate RediSearch syntax per type
└────────┬────────┘
         ↓
┌─────────────────┐
│   Translator    │  Orchestrate pipeline, build command
└────────┬────────┘
         ↓
┌─────────────────┐
│    Executor     │  Execute command, parse results
└────────┬────────┘
         ↓
QueryResult(rows, count)
```

Each layer has focused unit tests; 100% coverage is achievable because responsibilities are clear. Adding a new field type (e.g., GEO) means updating Analyzer and QueryBuilder, not rewriting everything. Early prototypes combined parsing and translation, which led to tests that required Redis connections for simple SQL parsing tests, difficulty testing edge cases in isolation, and tangled code that was hard to modify. The layered approach emerged from TDD — writing tests first revealed natural boundaries.

## For AI agents

- **[`AGENTS.md`](AGENTS.md):** how to use sql-redis from an agent, including gotchas and the error model.
- **[`llms.txt`](https://redis-developer.github.io/sql-redis/llms.txt):** auto-generated flat index of every doc page with one-line summaries (built by `mkdocs-llmstxt`).
- **[`docs/for-ais-only/`](docs/for-ais-only/):** repository map, build and test guide, and intentional failure modes for agents modifying the library.

## Development

```bash
make install       # uv sync --all-extras
make test          # requires Docker for testcontainers
make test-cov      # with coverage report
make lint          # format + mypy
make docs-serve    # uv sync --group docs && preview at http://localhost:8000
```

## Testing philosophy

This project uses strict TDD with 100% test coverage as a hard requirement:

1. **Write failing tests first** — Define expected behavior before implementation.
2. **One test at a time** — Implement just enough to pass each test.
3. **No untestable code** — If we can't test it, we don't write it.
4. **Integration tests mirror raw Redis** — `test_sql_queries.py` verifies SQL produces the same results as equivalent `FT.AGGREGATE` commands in `test_redis_queries.py`.

Coverage is enforced in CI. Pragmas (`# pragma: no cover`) are forbidden — if code can't be tested, it shouldn't exist. See [`docs/concepts/testing-philosophy.md`](docs/concepts/testing-philosophy.md) for the long form.

## License

MIT
