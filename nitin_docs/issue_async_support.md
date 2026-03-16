# Feature Request: Add Async Support to sql-redis

## Summary

Add async-compatible versions of `SchemaRegistry` and `Executor` classes to support async Redis clients (redis-py's `redis.asyncio`).

---

## Motivation

RedisVL (redis-vl-python) provides both sync and async APIs via `SearchIndex` and `AsyncSearchIndex`. When integrating `sql-redis` for SQL query support, the async path fails because `sql-redis` only supports synchronous Redis clients.

**Error encountered:**
```
TypeError: 'coroutine' object is not iterable
```

This occurs because `SchemaRegistry.load_all()` and `Executor.execute()` call `client.execute_command()` synchronously, but when passed an async client, this returns a coroutine instead of the result.

---

## Current Sync-Only Implementation

### `schema.py` - SchemaRegistry
```python
class SchemaRegistry:
    def __init__(self, redis_client: redis.Redis):  # <-- sync only
        self._client = redis_client
        
    def load_all(self) -> None:
        indexes = self._client.execute_command("FT._LIST")  # <-- sync call
        for index_name in indexes:
            self._load_index_schema(index_name)
            
    def _load_index_schema(self, index_name: str) -> None:
        info = self._client.execute_command("FT.INFO", index_name)  # <-- sync call
```

### `executor.py` - Executor
```python
class Executor:
    def __init__(self, client: redis.Redis, schema_registry: SchemaRegistry):
        self._client = client
        
    def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
        # ...
        raw_result = self._client.execute_command(*cmd)  # <-- sync call
```

---

## Proposed Solution

Add async versions of both classes:

### Option A: Separate Async Classes (Recommended)

Create `AsyncSchemaRegistry` and `AsyncExecutor` classes:

```python
# schema.py
from redis.asyncio import Redis as AsyncRedis

class AsyncSchemaRegistry:
    def __init__(self, redis_client: AsyncRedis):
        self._client = redis_client
        self._schemas: dict[str, dict[str, str]] = {}
        
    async def load_all(self) -> None:
        self._schemas.clear()
        indexes = await self._client.execute_command("FT._LIST")
        for index_name in indexes:
            if isinstance(index_name, bytes):
                index_name = index_name.decode("utf-8")
            await self._load_index_schema(index_name)
            
    async def _load_index_schema(self, index_name: str) -> None:
        try:
            info = await self._client.execute_command("FT.INFO", index_name)
            schema = self._parse_schema_from_info(info)
            self._schemas[index_name] = schema
        except Exception:
            self._schemas.pop(index_name, None)
```

```python
# executor.py
class AsyncExecutor:
    def __init__(self, client: AsyncRedis, schema_registry: AsyncSchemaRegistry):
        self._client = client
        self._schema_registry = schema_registry
        self._translator = Translator(schema_registry)
        
    async def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
        # ... same logic as sync version ...
        raw_result = await self._client.execute_command(*cmd)
        # ... parse results ...
        return QueryResult(rows=rows, count=count)
```

### Option B: Unified Classes with Protocol/ABC

Use a protocol or ABC to support both sync and async clients, with runtime detection.

---

## Files to Modify

| File | Changes |
|------|---------|
| `sql_redis/schema.py` | Add `AsyncSchemaRegistry` class |
| `sql_redis/executor.py` | Add `AsyncExecutor` class |
| `sql_redis/__init__.py` | Export new async classes |
| `tests/` | Add async test coverage |

---

## Acceptance Criteria

1. `AsyncSchemaRegistry` works with `redis.asyncio.Redis` client
2. `AsyncExecutor` works with `redis.asyncio.Redis` client  
3. All existing sync functionality remains unchanged
4. Async tests pass with testcontainers
5. Type hints are correct for both sync and async variants

---

## Related

- **RedisVL Issue**: [#487 - Add SQLQuery support to AsyncSearchIndex.query()](https://github.com/redis/redis-vl-python/issues/487)
- **Blocked PR**: RedisVL async SQLQuery implementation waiting on this
