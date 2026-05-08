# Testing Philosophy

sql-redis uses strict TDD with 100% test coverage as a hard requirement.

## Practices

1. **Write failing tests first.** Define the expected behavior before writing the implementation.
2. **One test at a time.** Implement just enough to pass the test in front of you.
3. **No untestable code.** If a branch cannot be tested, it should not exist. Coverage pragmas (`# pragma: no cover`) are forbidden.
4. **Integration tests mirror raw Redis.** `test_sql_queries.py` runs SQL through the translator and executor, and `test_redis_queries.py` runs the equivalent `FT.AGGREGATE` commands directly. Both must produce the same rows.

## Why no mocks for Redis

A mocked Redis happily returns whatever the test sets up. That tells us our code matches our assumptions about Redis, which is not the same thing as matching Redis itself. Mocked tests pass; production breaks.

Integration tests use `testcontainers[redis]` to start a real Redis with the search module on a free port. The startup cost is paid once per test session.

## Why 100% coverage is achievable here

Because the layers are decoupled (see [Architecture](architecture.md)), each component has a clear contract and a small surface. There are no untestable branches because there are no hidden dependencies.

Coverage is enforced in CI.
