---
description: Sync and async executors that run translated SQL against Redis.
---

# Executor

The executor runs a translated SQL query against Redis and parses the response
into a [`QueryResult`][sql_redis.QueryResult]. There are sync and async variants
and factory functions that wire up a schema registry for you.

| Symbol | Description |
|---|---|
| [`Executor`](#executor) | Sync executor. |
| [`AsyncExecutor`](#asyncexecutor) | Async executor. |
| [`create_executor`](#create_executor) | Factory for the sync executor with a configurable cache strategy. |
| [`create_async_executor`](#create_async_executor) | Factory for the async executor. |
| [`QueryResult`](#queryresult) | Result rows and total count. |
| [`SchemaCacheStrategy`](#schemacachestrategy) | `"lazy"` or `"load_all"` literal. |

## Executor

::: sql_redis.Executor

## AsyncExecutor

::: sql_redis.AsyncExecutor

## create_executor

::: sql_redis.create_executor

## create_async_executor

::: sql_redis.create_async_executor

## QueryResult

::: sql_redis.QueryResult

## SchemaCacheStrategy

::: sql_redis.SchemaCacheStrategy

## \_\_version\_\_

::: sql_redis.__version__

The installed package version, as a string. Useful for log lines, bug
reports, and version-gated feature checks.
