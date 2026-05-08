# Schema-aware Translation

A SQL clause does not map to a single RediSearch syntax. The right output depends on the underlying field's type.

| Field Type | Redis Syntax | Example |
|---|---|---|
| `TEXT` | `@field:term` | `@title:laptop` |
| `NUMERIC` | `@field:[min max]` | `@price:[100 500]` |
| `TAG` | `@field:{value}` | `@category:{books}` |
| `GEO` | `@field:[lon lat radius unit]` | `@loc:[-122.4 37.7 5 km]` |

## Why schema awareness is necessary

Consider `WHERE category = 'books'`. Without knowing what `category` is in Redis, the translator has two valid outputs and they return different rows:

- If `category` is `TEXT`, the right output is `@category:books` (a tokenized term match).
- If `category` is `TAG`, the right output is `@category:{books}` (an exact tag match).

A naive translator that always emits one or the other will silently produce wrong results in the case it picked badly. The library refuses to make that choice without information; it asks Redis for the schema.

## How the registry resolves the choice

The schema registry calls `FT.INFO` on the index and parses the response into a `{field_name: field_type}` map. The map is cached in process memory. When the analyzer encounters `WHERE category = 'books'`, it consults the cache, learns that `category` is a `TAG`, and tells the query builder to emit the tag-match form.

A single `FT.INFO` call captures every field on an index, so the per-query overhead after the first lookup is zero.

## Lazy versus eager: a startup-cost tradeoff

The registry can fill its cache in two ways. Both end at the same place; they differ only in *when* the round-trips happen.

**Lazy** is the default. Schemas are loaded on demand, the first time a given index is referenced in a query. A process that touches three indexes pays for three `FT.INFO` calls, spread across the queries that needed them. A process that never queries a given index never asks Redis about it. Startup is essentially free.

**Eager** loads everything at construction: one `FT._LIST` followed by one `FT.INFO` per index. Construction blocks until they all return. Subsequent queries do no schema I/O. The cost moves to startup, but a missing or misspelled index name fails immediately rather than at first use.

The right choice depends on whether startup latency or first-query latency matters more for your workload. Recipes for both modes live in [Lazy vs eager schemas](../user_guide/how_to_guides/lazy-vs-eager-schemas.md).

## Cache coherence

A cached schema can drift from reality. If you alter or drop an index after the schema has been read, the next translation will be based on the old layout. The library cannot detect this on its own; RediSearch does not emit keyspace notifications for `FT.*` commands.

The user is therefore responsible for invalidating the cache when their index changes. The mechanism is provided as an explicit call rather than automatic, because automatic invalidation would either require polling (expensive) or a hook the application has to wire anyway. The recipe is in [Lazy vs eager schemas](../user_guide/how_to_guides/lazy-vs-eager-schemas.md).

There is also a polling mode for processes that want to detect index creation and deletion in the background; see the same how-to.

## Async coalescing

In an async process, a burst of requests for a freshly-seen index can produce a thundering herd of `FT.INFO` calls. The async registry deduplicates these: concurrent calls for the same index share a single in-flight request, and only the first caller pays for the round-trip. The concept of in-flight coalescing is part of the broader async story; see [Async invariants](async-invariants.md).
