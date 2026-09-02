# Repository Map

A module-by-module guide to the sql-redis source tree, written for an agent
that needs to change something and wants to know where to look.

## Source layout

```
sql_redis/
  __init__.py         Public API exports. The 11 symbols here are the contract.
  version.py          __version__ string.
  parser.py           SQL string → ParsedQuery dataclass. Wraps sqlglot.
                      Owns: Condition, GeoDistanceCondition, ParsedQuery,
                      SQLParser, SQL_TO_REDIS_DATE_FUNCTIONS,
                      parse_date_to_timestamp.
  analyzer.py         ParsedQuery + SchemaRegistry → AnalyzedQuery.
                      Classifies each WHERE condition by field type so the
                      query builder knows whether to emit @field:term,
                      @field:[min max], @field:{value}, etc.
  schema.py           SchemaRegistry, AsyncSchemaRegistry. Caches FT.INFO
                      output. Lazy by default, eager via load_all().
  query_builder.py    Per-field-type RediSearch syntax emission.
                      Stateless. Reads AnalyzedQuery, returns a query string.
  translator.py       Translator, TranslatedQuery. Orchestrates parser →
                      analyzer → query builder. Decides FT.SEARCH vs
                      FT.AGGREGATE; emits a TranslatedQuery (command, index,
                      query_string, args, score_alias).
  executor.py         Executor, AsyncExecutor, factories, QueryResult,
                      SchemaCacheStrategy. The only module that talks to
                      Redis at query time.
```

## Test layout

```
tests/
  test_parser.py                    sqlglot wrapping; pure logic, no Redis.
  test_analyzer.py                  Field-type classification.
  test_schema.py                    Sync registry: lazy, eager, invalidate.
  test_async_schema.py              Async registry: ensure_schema, cancellation,
                                    coalescing, shielded loads.
  test_query_builder.py             Per-type syntax emission.
  test_translator.py                Pipeline orchestration. Uses fakes for the
                                    schema registry.
  test_executor.py                  End-to-end with testcontainers Redis.
  test_async_executor.py            Async equivalent.
  test_sql_queries.py               Integration: SQL → executor → real rows.
                                    Mirrored by test_redis_queries.py which
                                    runs the equivalent FT.AGGREGATE directly.
                                    Both must produce identical rows.
  test_redis_queries.py             See above.
  test_parameter_substitution.py    The 12 TDD tests for token-based substitution.
```

## Where features live

| SQL feature | Module(s) |
|---|---|
| `SELECT field, expr AS alias` | `parser.py` (extraction), `query_builder.py` (RETURN/LOAD) |
| `WHERE` operators (`=`, `<`, `BETWEEN`, `IN`) | `parser.py`, `analyzer.py`, `query_builder.py` |
| Boolean `AND`/`OR`/`NOT` | `query_builder.py` |
| Aggregations (`COUNT`, `SUM`, etc.) | `query_builder.py` (FT.AGGREGATE branch in `translator.py`) |
| `GROUP BY` | `query_builder.py` (GROUPBY) |
| `ORDER BY`, `LIMIT`, `OFFSET` | `query_builder.py` |
| Vector KNN (`vector_distance`) | `parser.py`, `query_builder.py` (KNN clause), `executor.py` (`$vector` byte injection) |
| Full-text (`fulltext`, `fuzzy`, `LIKE`) | `parser.py`, `query_builder.py` |
| GEO (`geo_distance`, `POINT`) | `parser.py::GeoDistanceCondition`, `query_builder.py` |
| Date functions (`YEAR`, `MONTH`, `DATE_FORMAT`) | `parser.py::SQL_TO_REDIS_DATE_FUNCTIONS` |
| `IS NULL`, `IS NOT NULL`, `exists()` | `parser.py`, `query_builder.py` |
| Parameter substitution | `executor.py::_substitute_params` |
| FT.SEARCH vs FT.AGGREGATE branching | `translator.py::translate_parsed` (the `use_aggregate` boolean) |
| Result-row parsing | `executor.py::_parse_reply` (dispatches by reply shape), `_parse_array_reply` (the four branches: WITHSCORES + RETURN 0, WITHSCORES, plain SEARCH, AGGREGATE), `_fold_map_to_array` (folds a RESP3 map into the array shape) |

## What to read before changing X

- **Parser changes.** Read `parser.py` end to end. Then look at how
  `translator.py::translate_parsed` consumes the result so you do not break
  the `AsyncExecutor` path that reuses a pre-parsed query.
- **Analyzer/query-builder changes.** Read `analyzer.py` and the matching
  branch in `query_builder.py`. The two are tightly coupled: a new field type
  in the analyzer needs a corresponding emitter in the builder.
- **Executor changes.** The sync and async executors share the module-level
  reply parsers, so a parsing change lands once. Their `execute()` bodies are
  still near-identical above the `_parse_reply` call, so a change there needs
  applying twice.
- **Schema registry changes.** The two registry classes do not share code,
  but they share semantics. Lazy loading, invalidation, and load coalescing
  must be consistent across sync and async.

## What is intentionally not exported

`Analyzer`, `QueryBuilder`, `SQLParser`, `ParsedQuery`, `Condition`,
`AnalyzedQuery` are internal. Tests import them, but they are not part of the
public contract. If a user imports them, they are on their own when we
refactor.
