# GitHub Issue: Add Async Support

**Copy/paste the content below into GitHub issue form**

---

## Title

Feature: Add async support (AsyncSchemaRegistry, AsyncExecutor)

---

## Body

### Summary

Add async-compatible versions of `SchemaRegistry` and `Executor` to support `redis.asyncio` clients.

### Motivation

RedisVL ([redis-vl-python](https://github.com/redis/redis-vl-python)) provides both sync and async APIs. When integrating `sql-redis` for SQL query support ([RedisVL #487](https://github.com/redis/redis-vl-python/issues/487)), the async path fails because `sql-redis` only supports synchronous Redis clients.

**Error:**
```python
TypeError: 'coroutine' object is not iterable
```

### Root Cause

`SchemaRegistry.load_all()` and `Executor.execute()` call `client.execute_command()` synchronously. When passed an async client, this returns a coroutine instead of the result.

### Proposed Solution

Add async versions of the affected classes:

**1. `AsyncSchemaRegistry`** (`schema.py`)
```python
class AsyncSchemaRegistry:
    async def load_all(self) -> None:
        indexes = await self._client.execute_command("FT._LIST")
        # ...
        
    async def _load_index_schema(self, index_name: str) -> None:
        info = await self._client.execute_command("FT.INFO", index_name)
        # ...
```

**2. `AsyncExecutor`** (`executor.py`)
```python
class AsyncExecutor:
    async def execute(self, sql: str, *, params: dict | None = None) -> QueryResult:
        # ... translation logic (sync, reuses Translator) ...
        raw_result = await self._client.execute_command(*cmd)
        # ... parse results ...
```

**Note:** `Translator` can be reused as-is since `get_schema()` is just an in-memory dict lookup.

### Files to Modify

| File | Changes |
|------|---------|
| `sql_redis/schema.py` | Add `AsyncSchemaRegistry` class |
| `sql_redis/executor.py` | Add `AsyncExecutor` class |
| `sql_redis/__init__.py` | Export `AsyncSchemaRegistry`, `AsyncExecutor` |
| `tests/` | Add async test coverage |

### Acceptance Criteria

- [ ] `AsyncSchemaRegistry` works with `redis.asyncio.Redis`
- [ ] `AsyncExecutor` works with `redis.asyncio.Redis`
- [ ] Existing sync functionality unchanged
- [ ] Async tests pass
- [ ] Type hints correct

### Related

- Blocks: [redis-vl-python #487](https://github.com/redis/redis-vl-python/issues/487)

---

## Labels

`enhancement`, `async`
