# Async Invariants

`AsyncExecutor` and `AsyncSchemaRegistry` are not just sync code with `await` sprinkled in. The async path makes three guarantees that the sync path has no need for, because async exposes new failure modes (cancellation, concurrency) that sync does not.

## Invariant 1: at most one in-flight `FT.INFO` per index

A burst of concurrent `executor.execute()` calls for an index whose schema is not yet cached would, in a naive implementation, all race to issue `FT.INFO`. That is a thundering herd against Redis: N callers, N round-trips, identical results, only one cache write needed.

The async registry coalesces these. The first caller starts an `asyncio.Task` that issues the `FT.INFO`; subsequent callers find the task already in flight and `await` the same task. Only the first caller pays the round-trip cost; the others get the result for free as soon as it lands.

Implementation: `AsyncSchemaRegistry._loading` is a `dict[str, Task]`. `ensure_schema(index)` checks for an in-flight task before starting one.

## Invariant 2: cancellation is shielded

A user can cancel an `await executor.execute(...)`, deliberately (`task.cancel()`) or by side effect (`asyncio.wait_for(...)` timeout). When that happens, the cancellation must not propagate into a shared schema-load task that other awaiters are still relying on.

The fix is `asyncio.shield`. The shared `FT.INFO` task is awaited inside a shield, so the caller's cancellation aborts the *await*, not the underlying task. The task keeps running and resolves for any remaining awaiters.

Without shielding, one caller's `wait_for` timeout could cancel the shared task, and every concurrent waiter would see `CancelledError` from a query that had nothing wrong with it. The sync registry needs none of this because its `FT.INFO` call cannot be cancelled mid-flight.

## Invariant 3: `invalidate()` cancels any in-flight load deliberately

The previous invariant says caller cancellation must not stop the shared task. The reverse is also true: cache invalidation must stop it.

If you call `invalidate("products")` while an `FT.INFO("products")` is in flight, the in-flight task is cancelled and removed from `_loading`. Otherwise the task could complete after invalidation and write a now-stale schema back into the cache. The race window is narrow but real.

The next call to `ensure_schema("products")` finds nothing in `_loading`, starts a fresh task, and gets the post-invalidate state.

## What this means for callers

In practice, you get three properties that are easy to take for granted but expensive to implement:

1. A burst of concurrent first-time queries against a new index issues exactly one `FT.INFO`. Coalescing is automatic; you never write any locking yourself.
2. Cancelling a query (timeout or `cancel()`) does not affect concurrent queries. The shared schema fetch survives.
3. After `invalidate()`, the next access reads fresh state. There is no risk of an in-flight stale-write race.

If you are implementing something analogous in your own code (a different cache that loads from Redis), the three invariants are a useful checklist.

## What is *not* an invariant

The following are explicitly **not** guaranteed:

- **`FT.INFO` order across indexes.** `load_all()` uses `asyncio.gather`; schemas land in arrival order, not in `FT._LIST` order. Code that depends on a specific load order is wrong.
- **Sync registry parity.** The sync `SchemaRegistry` has none of these mechanisms. Its `get_schema()` is blocking and serial; concurrent threads using a shared sync registry can race. If you need thread safety, wrap it yourself or use the async registry on an event loop.

## Reference

The recipes for using these mechanisms (cancellation-safe queries, post-alteration invalidation, change watching) are in {doc}`/user_guide/how_to_guides/async-usage` and {doc}`/user_guide/how_to_guides/lazy-vs-eager-schemas`.
