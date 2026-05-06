---
myst:
  html_meta:
    "description lang=en": |
      sql-redis documentation. SQL to Redis FT.SEARCH and FT.AGGREGATE translator.
---

# sql-redis

```{admonition} Status: Experimental
:class: warning

sql-redis is part of the [Redis AI Hub](https://redis.io/ai-hub/) under the
**Experimental** tier. The Python API can change between minor releases. The
project is validating its design and SQL surface in real use; we welcome bug
reports and feedback at the [issue tracker](https://github.com/redis-applied-ai/sql-redis/issues).
```

Query Redis collections with familiar SQL on top of RediSearch and RedisVL indexes. sql-redis converts SQL `SELECT` statements into Redis `FT.SEARCH` and `FT.AGGREGATE` commands, looking up index schemas via `FT.INFO` so the translation respects the underlying field types.

## Quick Start

```bash
pip install sql-redis
```

```bash
docker run -d --name redis -p 6379:6379 redis:8.4
```

→ *{doc}`user_guide/getting-started`*

---

## Explore the Docs

::::{grid} 2
:gutter: 4

:::{grid-item-card} 📖 Concepts
:link: concepts/index
:link-type: doc
:class-card: sd-shadow-sm

Understand how sql-redis works. Architecture, design decisions, and the why behind every layer.
:::

:::{grid-item-card} 🚀 User Guide
:link: user_guide/index
:link-type: doc
:class-card: sd-shadow-sm

Step by step. Installation, first query, and task-oriented recipes for every feature.
:::

:::{grid-item-card} 💡 Examples
:link: examples/index
:link-type: doc
:class-card: sd-shadow-sm

Worked examples and patterns built on the sql-redis primitives.
:::

:::{grid-item-card} 📚 API Reference
:link: api/index
:link-type: doc
:class-card: sd-shadow-sm

Every public class, method, and parameter, generated from docstrings.
:::

::::

## For AI agents

If you are an AI agent reading these docs, start with
[`AGENTS.md`](https://github.com/redis-applied-ai/sql-redis/blob/main/AGENTS.md)
at the repo root for a usage-oriented quick reference, or
{doc}`for-ais-only/index` for an internal map of the source tree. A flat
[`llms.txt`](https://github.com/redis-applied-ai/sql-redis/blob/main/docs/llms.txt)
index of every doc page is also available.

```{toctree}
:maxdepth: 2
:hidden:

Concepts <concepts/index>
User Guide <user_guide/index>
Examples <examples/index>
API <api/index>
For AI Agents <for-ais-only/index>
Changelog <https://github.com/redis-developer/sql-redis/releases>
```
