---
myst:
  html_meta:
    "description lang=en": |
      Concepts behind sql-redis. Architecture and design decisions.
---

# Concepts

Foundational reading for sql-redis. Each page explains a single design choice or sub-system, with enough context to make informed extensions or contributions.

::::{grid} 2
:gutter: 3

:::{grid-item-card} 🏗️ Architecture
:link: architecture
:link-type: doc

The two top-level objects (Executor, SchemaRegistry) and the layered translator they contain.
:::

:::{grid-item-card} 🤔 Why SQL?
:link: why-sql
:link-type: doc

The interface choice. SQL versus a pandas-style DSL versus a builder API.
:::

:::{grid-item-card} 🪛 Why sqlglot?
:link: why-sqlglot
:link-type: doc

The parser choice. sqlglot versus a hand-rolled recursive-descent parser.
:::

:::{grid-item-card} 🗂️ Schema-aware translation
:link: schema-aware-translation
:link-type: doc

Why field types matter, how the schema registry caches them, lazy versus eager loading.
:::

:::{grid-item-card} 🔀 FT.SEARCH vs FT.AGGREGATE
:link: search-vs-aggregate
:link-type: doc

Which Redis command runs for a given SQL, why the choice is forced, and which feature combinations are illegal.
:::

:::{grid-item-card} 🔣 Parameter substitution
:link: parameter-substitution
:link-type: doc

The token-based substitution algorithm and the bugs it fixes.
:::

:::{grid-item-card} 🧬 Vector substitution
:link: vector-substitution
:link-type: doc

Why bytes parameters take a different path: two-stage substitution that keeps vectors out of the SQL string.
:::

:::{grid-item-card} 🔁 Async invariants
:link: async-invariants
:link-type: doc

Coalesced FT.INFO loads, shielded reads, invalidate-cancels-in-flight. The three guarantees the async path provides.
:::

:::{grid-item-card} 📋 Result shape
:link: result-shape
:link-type: doc

What QueryResult.rows actually contains, why it varies with the command, scoring, and client decoding.
:::

:::{grid-item-card} 🧪 Testing philosophy
:link: testing-philosophy
:link-type: doc

TDD, 100% coverage, and why integration tests do not mock Redis.
:::

::::

```{toctree}
:maxdepth: 2
:hidden:

architecture
why-sql
why-sqlglot
schema-aware-translation
search-vs-aggregate
parameter-substitution
vector-substitution
async-invariants
result-shape
testing-philosophy
```
