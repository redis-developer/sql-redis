---
description: Concepts behind sql-redis. Architecture and design decisions.
---

# Concepts

Foundational reading for sql-redis. Each page explains a single design choice or sub-system, with enough context to make informed extensions or contributions.

<div class="grid cards" markdown>

-   :material-sitemap:{ .lg .middle } **[Architecture](architecture.md)**

    ---

    The two top-level objects (Executor, SchemaRegistry) and the layered translator they contain.

-   :material-help-circle:{ .lg .middle } **[Why SQL?](why-sql.md)**

    ---

    The interface choice. SQL versus a pandas-style DSL versus a builder API.

-   :material-tools:{ .lg .middle } **[Why sqlglot?](why-sqlglot.md)**

    ---

    The parser choice. sqlglot versus a hand-rolled recursive-descent parser.

-   :material-folder-table:{ .lg .middle } **[Schema-aware translation](schema-aware-translation.md)**

    ---

    Why field types matter, how the schema registry caches them, lazy versus eager loading.

-   :material-source-branch:{ .lg .middle } **[FT.SEARCH vs FT.AGGREGATE](search-vs-aggregate.md)**

    ---

    Which Redis command runs for a given SQL, why the choice is forced, and which feature combinations are illegal.

-   :material-variable:{ .lg .middle } **[Parameter substitution](parameter-substitution.md)**

    ---

    The token-based substitution algorithm and the bugs it fixes.

-   :material-dna:{ .lg .middle } **[Vector substitution](vector-substitution.md)**

    ---

    Why bytes parameters take a different path: two-stage substitution that keeps vectors out of the SQL string.

-   :material-sync:{ .lg .middle } **[Async invariants](async-invariants.md)**

    ---

    Coalesced FT.INFO loads, shielded reads, invalidate-cancels-in-flight. The three guarantees the async path provides.

-   :material-format-list-bulleted:{ .lg .middle } **[Result shape](result-shape.md)**

    ---

    What `QueryResult.rows` actually contains, why it varies with the command, scoring, client decoding and the wire protocol.

-   :material-test-tube:{ .lg .middle } **[Testing philosophy](testing-philosophy.md)**

    ---

    TDD, 100% coverage, and why integration tests do not mock Redis.

</div>
