---
description: Translator that turns SQL into Redis FT.SEARCH or FT.AGGREGATE commands.
---

# Translator

The translator turns a SQL string into a Redis `FT.SEARCH` or `FT.AGGREGATE`
command. It does not execute anything; use [`Executor`][sql_redis.Executor]
for that.

## Translator

::: sql_redis.Translator

## TranslatedQuery

::: sql_redis.TranslatedQuery
