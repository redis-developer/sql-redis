---
description: sql-redis documentation. SQL to Redis FT.SEARCH and FT.AGGREGATE translator.
---

# sql-redis

!!! warning "Status: Experimental"

    sql-redis is part of the [Redis AI Hub](https://redis.io/ai-hub/) under the
    **Experimental** tier. The Python API can change between minor releases. The
    project is validating its design and SQL surface in real use; we welcome bug
    reports and feedback at the [issue tracker](https://github.com/redis-developer/sql-redis/issues).

Query Redis collections with familiar SQL on top of RediSearch and RedisVL indexes. sql-redis converts SQL `SELECT` statements into Redis `FT.SEARCH` and `FT.AGGREGATE` commands, looking up index schemas via `FT.INFO` so the translation respects the underlying field types.

## Quick Start

```bash
pip install sql-redis
```

```bash
docker run -d --name redis -p 6379:6379 redis:8.4
```

→ *[Getting Started](user_guide/getting-started.md)*

---

## Explore the Docs

<div class="grid cards" markdown>

-   :material-book-open-variant:{ .lg .middle } **[Concepts](concepts/index.md)**

    ---

    Understand how sql-redis works. Architecture, design decisions, and the why behind every layer.

-   :material-rocket-launch:{ .lg .middle } **[User Guide](user_guide/index.md)**

    ---

    Step by step. Installation, first query, and task-oriented recipes for every feature.

-   :material-lightbulb-on:{ .lg .middle } **[Examples](examples/index.md)**

    ---

    Worked examples and patterns built on the sql-redis primitives.

-   :material-api:{ .lg .middle } **[API Reference](api/index.md)**

    ---

    Every public class, method, and parameter, generated from docstrings.

</div>

## For AI agents

If you are an AI agent reading these docs, start with
[`AGENTS.md`](https://github.com/redis-developer/sql-redis/blob/main/AGENTS.md)
at the repo root for a usage-oriented quick reference, or
[For AI Agents](for-ais-only/index.md) for an internal map of the source tree. A
flat [`llms.txt`](https://docs.redisvl.com/projects/sql-redis/llms.txt) index of
every doc page is also auto-generated at build time.
