---
myst:
  html_meta:
    "description lang=en": |
      Internal AI-agent guide to the sql-redis source tree. Repo map, build, test, and failure modes.
---

# For AI Agents Modifying sql-redis

This section is the internal counterpart to the user-facing
[AGENTS.md](https://github.com/redis-applied-ai/sql-redis/blob/main/AGENTS.md).
It exists for an agent that has been asked to *change* the library: add a new
SQL feature, fix a parser bug, extend the schema registry.

```{toctree}
:maxdepth: 1

REPOSITORY_MAP
BUILD_AND_TEST
FAILURE_MODES
```

## Decision tree

Use this to find the right starting point fast.

| Task | Read first |
|---|---|
| Add a new SQL clause or operator | [Repository map](REPOSITORY_MAP.md), then `parser.py`, then `analyzer.py`, then `query_builder.py` |
| Add a new field type (e.g., a future GEO variant) | [Repository map](REPOSITORY_MAP.md), then `analyzer.py` and `query_builder.py` |
| Fix a translation bug | [Repository map](REPOSITORY_MAP.md), then `translator.py`. Run integration tests under `tests/test_sql_queries.py`. |
| Change parameter substitution | {doc}`/concepts/parameter-substitution`, then `executor.py::_substitute_params` |
| Change schema loading semantics | {doc}`/concepts/schema-aware-translation`, then `schema.py` |
| Change async behavior | {doc}`/concepts/async-invariants`, then `executor.py::AsyncExecutor` and `schema.py::AsyncSchemaRegistry` |
| Change the FT.SEARCH/FT.AGGREGATE branching | {doc}`/concepts/search-vs-aggregate`, then `translator.py::translate_parsed` |
| Change the result-row shape | {doc}`/concepts/result-shape`, then `executor.py` (`Executor.execute` parsing branches) |
| Run tests | [Build and test](BUILD_AND_TEST.md) |
| Diagnose "this looks broken" | [Failure modes](FAILURE_MODES.md) before assuming a bug |

## Project invariants the agent should preserve

1. **No mocks for Redis in integration tests.** Real `testcontainers[redis]`
   only. See {doc}`/concepts/testing-philosophy`.
2. **100% line coverage is enforced in CI.** No `# pragma: no cover`. If a
   branch can't be tested, it shouldn't exist.
3. **Public API is what `sql_redis/__init__.py` exports.** Anything else is
   internal and can change without notice. The autoclass-driven reference at
   `docs/api/` is the contract.
4. **Docstrings are the single source of truth for the API reference.**
   Sphinx `autoclass` reads them directly. If you change a method signature,
   update the docstring in the same change.
5. **No emdashes or `--` in prose.** Stylistic rule from the project's
   [`CLAUDE.md`](https://github.com/redis-applied-ai/sql-redis/blob/main/CLAUDE.md).
