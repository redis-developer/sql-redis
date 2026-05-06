# Getting Started

By the end of this walkthrough you will have run a SQL `SELECT` against a Redis index and printed three rows back to your terminal. Allow about five minutes.

## Prerequisites

Before you start, confirm both:

- Redis 8.x is running locally on port 6379. See {doc}`installation`.
- `sql-redis` is installed in the current Python environment (`pip install sql-redis`).

If both are true, the following Python snippet should print a version string:

```python
from sql_redis import __version__
print(__version__)
```

You should see something like `0.4.0`.

## 1. Create an index in Redis

sql-redis queries existing indexes; it never creates them. Define one with `FT.CREATE` first. From a fresh Python session:

```python
from redis import Redis

client = Redis()

client.execute_command(
    "FT.CREATE", "products",
    "ON", "HASH",
    "PREFIX", "1", "product:",
    "SCHEMA",
    "title", "TEXT",
    "category", "TAG",
    "price", "NUMERIC", "SORTABLE",
)
```

You should see `b'OK'` printed (or no output, depending on your shell). If you see `Index already exists`, that is fine: it means a previous run created it.

## 2. Load three documents

```python
client.hset("product:1", mapping={"title": "gaming laptop", "category": "electronics", "price": 1499})
client.hset("product:2", mapping={"title": "mechanical keyboard", "category": "electronics", "price": 129})
client.hset("product:3", mapping={"title": "ergonomic chair", "category": "furniture", "price": 349})
```

Each call returns `3` (the number of fields written). Redis indexes the documents automatically because they match the `product:` prefix.

## 3. Build an executor

```python
from sql_redis import create_executor

executor = create_executor(client)
```

`create_executor` constructs a schema registry for you and uses lazy schema loading: no `FT.INFO` call is made yet.

## 4. Run a query

```python
result = executor.execute("""
    SELECT title, price
    FROM products
    WHERE category = 'electronics' AND price < 500
    ORDER BY price ASC
""")

for row in result.rows:
    print(row[b"title"], row[b"price"])
```

You should see exactly one line:

```
b'mechanical keyboard' b'129'
```

The other two rows are filtered out: the laptop is over the price cap, the chair is in the wrong category. Field keys come back as bytes because the default `Redis()` client does not decode responses; if you want strings, construct it as `Redis(decode_responses=True)` and the snippet will print `mechanical keyboard 129` instead.

`result.count` is `1` here, the total match count from Redis.

## You are done

You created an index, loaded data, ran a SQL query, and got rows back. Everything else in the docs builds on those four steps.

## Where next

- Inject runtime values into a query: {doc}`how_to_guides/use-parameters`.
- Search a `TEXT` field beyond simple equality: {doc}`how_to_guides/text-search`.
- Find the K most similar items to a query embedding: {doc}`how_to_guides/vector-search`.
- Filter by geographic distance: {doc}`how_to_guides/geo-queries`.
- Use the async API: {doc}`how_to_guides/async-usage`.
- The full SQL surface: {doc}`/api/sql-syntax`.
