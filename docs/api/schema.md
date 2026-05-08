---
description: Schema registries that cache index field types loaded from Redis.
---

# Schema Registries

The schema registry caches index field types loaded from Redis via `FT.INFO`.
There are sync and async variants. Conceptual background is in
[Schema-aware translation](../concepts/schema-aware-translation.md).

| Class | Description |
|---|---|
| [`SchemaRegistry`](#schemaregistry) | Sync registry. Lazy and eager loading, polling for index changes. |
| [`AsyncSchemaRegistry`](#asyncschemaregistry) | Async registry. Coalesced concurrent loads, cancellation-safe. |

## SchemaRegistry

::: sql_redis.SchemaRegistry

## AsyncSchemaRegistry

::: sql_redis.AsyncSchemaRegistry
