---
description: sql-redis API reference. Generated from docstrings.
---

# API Reference

Reference documentation for the public sql-redis API. Each class and function is generated from the docstrings in the source.

<div class="grid cards" markdown>

-   :material-translate:{ .lg .middle } **[Translator](translator.md)**

    ---

    Turn SQL into a Redis `FT.SEARCH` or `FT.AGGREGATE` command without executing it.

-   :material-database-search:{ .lg .middle } **[Schema Registries](schema.md)**

    ---

    Cache index field types from `FT.INFO`. Sync and async variants.

-   :material-play-circle:{ .lg .middle } **[Executor](executor.md)**

    ---

    Run a translated query against Redis. Sync and async, with factory helpers.

-   :material-code-tags:{ .lg .middle } **[SQL Syntax](sql-syntax.md)**

    ---

    The supported SQL surface: clauses, operators, functions, vector search.

</div>
