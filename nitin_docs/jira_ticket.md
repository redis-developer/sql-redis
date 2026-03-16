# Jira Ticket

## Title
Add async support to sql-redis (AsyncSchemaRegistry, AsyncExecutor)

## Type
Feature

## Priority
High

## Labels
`async`, `redis-asyncio`, `redisvl-integration`

## Epic Link
RedisVL SQL Support

---

## Summary
Add async-compatible versions of `SchemaRegistry` and `Executor` classes to support `redis.asyncio` clients.

## Description
RedisVL provides both sync (`SearchIndex`) and async (`AsyncSearchIndex`) APIs. The SQLQuery feature currently only works with sync because `sql-redis` only supports synchronous Redis clients.

When passing an async Redis client to `SchemaRegistry` or `Executor`, the code fails with:
```
TypeError: 'coroutine' object is not iterable
```

### Root Cause
- `SchemaRegistry.load_all()` calls `client.execute_command("FT._LIST")` synchronously
- `Executor.execute()` calls `client.execute_command(*cmd)` synchronously
- With async client, these return coroutines instead of results

### Solution
Add `AsyncSchemaRegistry` and `AsyncExecutor` classes that use `await` for Redis calls.

Note: `Translator` class can be reused as-is since it only performs in-memory operations.

## Acceptance Criteria
- [ ] `AsyncSchemaRegistry` class with async `load_all()` and `_load_index_schema()`
- [ ] `AsyncExecutor` class with async `execute()`
- [ ] Export new classes from `__init__.py`
- [ ] Async test coverage
- [ ] Type hints correct for async variants
- [ ] Existing sync functionality unchanged

## Story Points
3

## Blocked By
None

## Blocks
- RedisVL Issue #487: Add SQLQuery support to AsyncSearchIndex.query()
