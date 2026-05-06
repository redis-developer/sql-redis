# Vector Substitution

Vectors are the only parameter type that does not get inlined into the SQL string. Understanding why explains a small surprise users hit the first time they pass an embedding: the placeholder is preserved, then replaced later, in a different stage.

## The problem with stringifying a vector

A query embedding is a `bytes` object: a packed `float32` array, perhaps 1536 entries long for an OpenAI-style embedding. That is roughly 6 KB of binary data per query. Two consequences make string substitution infeasible.

**Encoding.** RediSearch expects the vector as raw bytes in the command list, not as a quoted SQL literal. There is no `'\x00\x01\x02...'` syntax we could substitute that would round-trip through the SQL parser and survive into the Redis command intact.

**Sanity.** Even if it were syntactically possible, splicing 6 KB of binary bytes into a SQL string and re-parsing it would be wasteful at every layer: the parameter substitutor, the SQL parser, the analyzer, and the query builder would all carry around bytes-as-strings until the executor finally peeled them off again.

## The two-stage answer

sql-redis splits parameter substitution into two stages:

```
SQL with :params       Redis command list
        │                       │
        ▼                       ▼
┌──────────────────┐   ┌──────────────────┐
│ Stage 1:         │   │ Stage 2:         │
│ String params    │   │ Bytes params     │
│ inlined into SQL │   │ injected into    │
│ (escaping, quoting)│  │ command args     │
└─────────┬────────┘   └─────────┬────────┘
          │                      │
          ▼                      │
   parse → translate             │
          │                      │
          ▼                      │
   command list with             │
   "$vector" placeholder ────────┘
          │
          ▼
   execute_command(*cmd)
```

**Stage 1 (in `_substitute_params`).** Every parameter that is `int`, `float`, or `str` is inlined into the SQL string with appropriate quoting and escaping. Parameters whose value is `bytes` are deliberately *skipped*: the `:vec` placeholder is left in place. Other types (`None`, `bool`, `list`) are also skipped, on the assumption that they are handled elsewhere in the pipeline.

**Translation.** The SQL is parsed as usual. The translator emits a query that uses `$vector` (a literal four-character placeholder) wherever a vector argument is expected. From the parser's perspective there is no difference between "a parameter is missing" and "the user wrote `$vector`"; the placeholder survives translation.

**Stage 2 (in `Executor.execute`).** The executor scans `params` for any `bytes` value, finds the `$vector` token in the command list, and replaces it with the bytes. The replacement happens after translation, on the command list, not on the SQL string. The bytes never participate in parsing.

## Why one vector per query is enough

The current implementation supports a single bytes parameter per query. This matches the underlying RediSearch capability: a vector query has one query vector, used by the `KNN` clause. A query with two `vector_distance(...)` calls is not a thing.

If multi-vector queries become a thing later, the same scheme generalises: emit `$vector_a`, `$vector_b`; the executor matches each placeholder to the named bytes parameter.

## Implications for callers

- The same `params` dict can mix `str`, `int`, `float`, and `bytes` freely. Each type takes its own path.
- A `bytes` value never appears in the SQL string, so debugging tools that print the substituted SQL will still show `:vec`.
- A `bytes` value does *not* go through `_substitute_params` quoting, so it cannot accidentally produce malformed SQL. The downside is that a misnamed `:vec` placeholder is not detected at substitution time; it produces a Redis error at execution.

The user-facing recipe is in {doc}`/user_guide/how_to_guides/use-parameters` ("Vectors") and {doc}`/user_guide/how_to_guides/vector-search`.
