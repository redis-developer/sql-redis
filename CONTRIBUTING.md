# Contributing to sql-redis

Thanks for your interest in contributing! `sql-redis` is an early-stage,
proof-of-concept SQL-to-Redis translator and we welcome bug reports, feature
ideas, and pull requests from the community.

## Reporting bugs and requesting features

- Open a [GitHub issue](https://github.com/redis-developer/sql-redis/issues)
  describing the problem or idea.
- For bug reports, include:
  - The SQL input that misbehaved.
  - The translated Redis command (`SQLQuery.redis_query_string()` output) or
    the actual results, plus what you expected.
  - The `sql-redis` and `redis` versions you're running.
- For security-sensitive reports, follow [SECURITY.md](./SECURITY.md) instead
  of opening a public issue.

## Development setup

`sql-redis` uses [uv](https://docs.astral.sh/uv/) for dependency management
and [hatchling](https://hatch.pypa.io/latest/) as the build backend.

```bash
# Clone and install
git clone https://github.com/redis-developer/sql-redis.git
cd sql-redis
uv sync

# Common tasks (see the Makefile for the full list)
make format        # black + isort
make lint          # format + mypy
make test          # pytest
make test-cov      # pytest with coverage
make build         # wheel + sdist
```

Tests use [`testcontainers`](https://testcontainers.com/) to spin up an
ephemeral Redis instance, so a local Docker daemon is required to run the
full suite.

## Pull requests

1. Fork and create a branch off `main`.
2. Make your change, including tests for any new behavior.
3. Run `make lint test` and confirm everything passes.
4. Open a pull request against `main`. Apply one of the
   [`auto:` labels](./.autorc) to indicate the intended version bump
   (`auto:major`, `auto:minor`, `auto:patch`, or `auto:skip-release`).
   Maintainers add `auto:release` when the change is ready to ship; the
   release workflow handles the version bump, tag, GitHub release, and
   PyPI publish automatically.

## Code style

- Black and isort are enforced via `make format` and pre-commit hooks.
- Type hints required on new public APIs; `mypy` runs in CI.
- Keep new modules focused and add tests in the corresponding `tests/test_*.py`.
