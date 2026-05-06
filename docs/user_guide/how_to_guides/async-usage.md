# Use the async API

You want to run queries from async code (FastAPI, ASGI, an asyncio worker).

## Construct an async executor

```python
import redis.asyncio as redis_async
from sql_redis import create_async_executor

client = redis_async.Redis(host="localhost", port=6379)

executor = await create_async_executor(client)
```

Like the sync factory, `create_async_executor` defaults to lazy schema loading. Pass `schema_cache_strategy="load_all"` to load every index at construction.

## Execute

```python
result = await executor.execute(
    "SELECT title FROM products WHERE category = :cat LIMIT 10",
    params={"cat": "electronics"},
)

for row in result.rows:
    print(row)
```

`result` is the same `QueryResult` as the sync API.

## Lazy loading semantics

`AsyncSchemaRegistry.ensure_schema(index)` is the async equivalent of the sync lazy path. The first call issues one `FT.INFO`. Concurrent calls for the same index share the single in-flight request, so a burst of requests for a freshly seen index does not turn into a thundering herd.

If you cancel an `await executor.execute(...)` (for example via `asyncio.wait_for` timeout), the underlying schema load is shielded so other awaiters keep their result.

## Invalidating

Same shape as sync:

```python
executor._schema_registry.invalidate("products")
executor._schema_registry.invalidate()
```

`invalidate()` cancels in-flight schema loads as well, so a stale read cannot land after invalidation.

## Constructing manually

If you need an explicit registry (for sharing across executors, for example):

```python
from sql_redis import AsyncExecutor, AsyncSchemaRegistry

registry = AsyncSchemaRegistry(client)
executor = AsyncExecutor(client, registry)
```

`AsyncSchemaRegistry.load_all()` is async; call it explicitly if you want eager loading without going through the factory.
