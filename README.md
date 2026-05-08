# sql-redis

[![Status: Experimental](https://img.shields.io/badge/status-experimental-orange)](https://redis.io/ai-hub/)

Query Redis collections with familiar SQL on top of RediSearch and RedisVL indexes. Converts SQL `SELECT` statements into Redis `FT.SEARCH` and `FT.AGGREGATE` commands.

> **Status: Experimental.** sql-redis is in the [Redis AI Hub](https://redis.io/ai-hub/) under the Experimental tier. The API can change between minor releases. Not yet production-ready; we are validating the design and the SQL surface in real use.

## Install

```bash
pip install sql-redis
```

## Quick example

```python
from redis import Redis
from sql_redis import create_executor

client = Redis()
executor = create_executor(client)

result = executor.execute("""
    SELECT title, price
    FROM products
    WHERE category = 'electronics' AND price < 500
    ORDER BY price ASC
    LIMIT 10
""")

for row in result.rows:
    print(row[b"title"], row[b"price"])
```

## Documentation

Full documentation is published at **[docs.redisvl.com/projects/sql-redis/](https://docs.redisvl.com/projects/sql-redis/)**.

- **Getting started:** [User Guide](https://docs.redisvl.com/projects/sql-redis/en/latest/user_guide/getting-started.html)
- **How-to guides:** [How-to Guides](https://docs.redisvl.com/projects/sql-redis/en/latest/user_guide/how_to_guides/)
- **Concepts and design:** [Concepts](https://docs.redisvl.com/projects/sql-redis/en/latest/concepts/)
- **API reference:** [API](https://docs.redisvl.com/projects/sql-redis/en/latest/api/)
- **SQL syntax catalog:** [SQL Syntax](https://docs.redisvl.com/projects/sql-redis/en/latest/api/sql-syntax.html)

## For AI agents

- **[`AGENTS.md`](AGENTS.md):** how to use sql-redis from an agent, including gotchas and the error model.
- **[`llms.txt`](https://docs.redisvl.com/projects/sql-redis/llms.txt):** auto-generated flat index of every doc page with one-line summaries.
- **[`docs/for-ais-only/`](docs/for-ais-only/):** repository map, build and test guide, and intentional failure modes for agents modifying the library.

To build the docs locally:

```bash
uv sync --group docs
make docs-build
make docs-serve   # http://localhost:8000
```

## Development

```bash
make install       # uv sync
make test          # requires Docker for testcontainers
make test-cov      # with coverage report
make lint          # format + mypy
```

The project uses strict TDD with 100% coverage enforced in CI. See [`docs/concepts/testing-philosophy.md`](docs/concepts/testing-philosophy.md).

## License

MIT
