# Build and Test

## Prerequisites

- Python 3.9 or newer.
- `uv` (https://docs.astral.sh/uv/).
- Docker. The integration tests use `testcontainers[redis]` to spin up a
  real Redis with the search module on a free port. Without Docker the tests
  cannot run.

## Make targets

```
make install        uv sync (dev group)
make format         black + isort
make check-format   black --check
make check-types    mypy ./sql_redis
make lint           format + check-types
make test           pytest
make test-verbose   pytest -vv -s
make test-cov       pytest with coverage report (terminal + htmlcov/)
make check          lint + test
make build          uv build (wheel + sdist)
make docs-build     Build MkDocs HTML to ./site (strict mode)
make docs-serve     Run mkdocs serve on http://127.0.0.1:8000
make clean          Remove caches and build output
```

## Coverage policy

100% line coverage is enforced in CI. `# pragma: no cover` is forbidden. If a
branch can't be tested, delete it. The two-pronged test layout (unit per
module + integration via real Redis) makes this achievable.

## Running a single test

```
uv run pytest tests/test_executor.py::test_select_with_filter -vv
```

## Running with coverage HTML

```
make test-cov
open htmlcov/index.html
```

## Building the docs

```
uv sync --group docs
make docs-build         # writes ./site
make docs-serve         # http://127.0.0.1:8000
```

The MkDocs build runs with `--strict`, so any warning is fatal. To run the
strict build directly:

```
uv run --group docs mkdocs build --strict
```

## CI gates (target state)

- `make check` (lint + tests) on every PR, across Python 3.9 to 3.13.
- `pytest -m protocol` against redis-py 6.3.0, 7.4.0 and 8.1.0 (the
  `redis-py-compat` job). Mark any test whose result depends on the RESP
  protocol or the redis-py version with `@pytest.mark.protocol` so it runs
  there.
- `make docs-build` with `-W` on every PR.
- 100% coverage gate.

## Fast iteration loops

When changing parsing logic:

```
uv run pytest tests/test_parser.py tests/test_translator.py -x -vv
```

These do not need Redis and run in well under a second.

When changing reply parsing:

```
uv run pytest tests/test_reply_shapes.py tests/test_nil_field_array.py -x -vv
```

These feed canned replies through the parser with no server, so they are the
fastest signal on a parsing change. Follow with
`tests/test_protocol_matrix.py`, which asserts the same behaviour against a
real server on both protocols.

When changing other executor logic:

```
uv run pytest tests/test_executor.py tests/test_sql_queries.py -x -vv
```

The first run pays for the testcontainers Redis startup (a few seconds).
Subsequent runs in the same session reuse it.
