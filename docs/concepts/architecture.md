# Architecture

sql-redis sits between an application and Redis, turning a SQL `SELECT` string into a `FT.SEARCH` or `FT.AGGREGATE` command and parsing the reply.

## The two top-level objects

A user's program touches two classes:

```
                  ┌──────────────────────────────────────────┐
                  │              Executor                    │
                  │  ┌────────────────────────────────────┐  │
                  │  │            Translator              │  │
                  │  │  ┌──────────┐  ┌────────────────┐  │  │
                  │  │  │ SQLParser│→ │   Analyzer     │  │  │
                  │  │  └──────────┘  └───────┬────────┘  │  │
                  │  │                        ▼           │  │
                  │  │                ┌────────────────┐  │  │
                  │  │                │ QueryBuilder   │  │  │
                  │  │                └────────────────┘  │  │
                  │  └────────────────────────────────────┘  │
                  │                                          │
                  │       ┌──────────────────────────┐       │
                  │       │     SchemaRegistry       │       │
                  │       │   (consulted by Analyzer │       │
                  │       │    and Translator)       │       │
                  │       └──────────────────────────┘       │
                  └──────────────────────────────────────────┘
```

`Executor` runs the query end to end. Internally it owns a `Translator` (which owns a parser, an analyzer, and a query builder), plus a `SchemaRegistry`. Both top-level classes have async siblings: `AsyncExecutor` and `AsyncSchemaRegistry`.

## What each layer does

```
SQL string
    │
    ▼  parse
ParsedQuery (sqlglot AST plus extracted index, fields, conditions)
    │
    ▼  analyze, consulting SchemaRegistry for field types
AnalyzedQuery (each WHERE condition tagged with its field's Redis type)
    │
    ▼  build per-type RediSearch syntax
TranslatedQuery (command + index + query string + args + score alias)
    │
    ▼  execute against Redis, parse the reply
QueryResult (rows, count)
```

- **`SQLParser`** wraps sqlglot. Pure: no Redis dependency. Output is a `ParsedQuery`, the library's own dataclass.
- **`Analyzer`** decides how each `WHERE` condition will translate, based on the underlying field's type. This is the only place the schema registry is consulted during translation. See {doc}`schema-aware-translation` for why this lookup is necessary.
- **`QueryBuilder`** is stateless. Given a tagged condition, it knows how to emit `@field:term`, `@field:[min max]`, `@field:{value}`, and so on.
- **`Translator`** is the orchestrator. It calls parse, analyze, build in order and packages the result into a `TranslatedQuery`. It also decides whether the final command is `FT.SEARCH` or `FT.AGGREGATE` (see {doc}`search-vs-aggregate`).
- **`Executor`** is the only layer that talks to Redis at query time. It substitutes parameters ({doc}`parameter-substitution`), sends the command, parses the reply into rows ({doc}`result-shape`).

## Why this layering exists

Each concern is genuinely independent.

**Testability.** Each layer can be unit-tested in isolation. A `SQLParser` test does not need Redis. A `QueryBuilder` test does not need a parsed AST it cannot construct by hand. 100% coverage is achievable because no class has a dependency it cannot fake.

**Single responsibility.** The parser does not know about Redis. The query builder does not know about SQL. A change to one rarely cascades.

**Extensibility.** Adding a new field type, say a future GEO variant, means updating the analyzer and query builder. The parser, schema registry, and executor are unaffected.

## Why not a single monolithic translator

Early prototypes combined parsing and translation. That led to:

- Tests that needed Redis connections to verify pure SQL parsing.
- Difficulty isolating edge cases: a single failure could be in any of three responsibilities.
- Tangled code that resisted modification.

The layered split emerged from TDD. Writing the test first repeatedly forced a question of "what does this class actually need?" and the boundaries fell out naturally.
