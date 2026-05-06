# Choose lazy or eager schema loading

The schema registry can fetch index schemas in two ways. The right choice depends on how many indexes you query and how much you care about startup latency.

## Lazy (default)

Schemas are loaded the first time each index is referenced.

```python
from sql_redis import create_executor

executor = create_executor(client)
# No FT.INFO calls yet.

executor.execute("SELECT * FROM products LIMIT 1")
# One FT.INFO("products") call now.

executor.execute("SELECT * FROM products LIMIT 5")
# No additional FT.INFO call (cached).
```

Choose lazy when:

- You only query a subset of indexes per process.
- You care about startup latency (web app cold start, serverless function).
- You construct executors in tests and do not want test setup to block on Redis.

## Eager (`load_all`)

Every index is loaded at construction time:

```python
executor = create_executor(client, schema_cache_strategy="load_all")
# FT._LIST + one FT.INFO per index right now.
```

Choose eager when:

- You want to fail fast on a missing index at startup, not at first query.
- Your process is short-lived and queries many indexes; the up-front cost is the same either way.
- You are running batch scripts where startup cost is irrelevant.

## Invalidating cached schemas

If you alter or drop an index, the cached schema goes stale:

```python
executor._schema_registry.invalidate("products")  # one index
executor._schema_registry.invalidate()             # all
```

The next access re-fetches `FT.INFO`.

## Watching for changes (sync only)

The sync `SchemaRegistry` can poll for index creation and deletion:

```python
registry = executor._schema_registry
registry.start_watching(on_change=lambda evt, idx: print(evt, idx))

# In a loop, periodically:
registry.process_pending_events()
```

RediSearch does not emit keyspace notifications for `FT.*` commands, so this is poll-based via `FT._LIST`. Call `process_pending_events()` from a background thread or a periodic task.
