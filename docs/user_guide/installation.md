# Installation

## Install the package

```bash
pip install sql-redis
```

Or with `uv`:

```bash
uv add sql-redis
```

Python 3.9 or newer is required.

## Run Redis with the search module

The library targets RediSearch 2.x. The simplest way to get a compatible Redis is the official Redis image, version 8.x:

```bash
docker run -d --name redis -p 6379:6379 redis:8.4
```

For features that depend on `INDEXMISSING` (used by `IS NULL` translation), Redis 7.4 or newer is required.

## Verify the install

```python
from sql_redis import __version__
print(__version__)
```

## Optional: development setup

If you are working on sql-redis itself:

```bash
git clone https://github.com/redis-developer/sql-redis
cd sql-redis
make install        # uv sync
make test           # requires Docker for testcontainers
```

To build the docs locally:

```bash
uv sync --group docs
make docs-build
make docs-serve     # http://localhost:8000
```
