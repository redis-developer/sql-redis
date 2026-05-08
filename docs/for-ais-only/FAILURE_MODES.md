# Failure Modes

Things that look like bugs but are intentional. Read this before "fixing" any
of them.

## Result row keys are bytes

`Executor.execute(...).rows` returns dicts whose keys are `bytes`, not
`str`, when the underlying `Redis` client uses default
`decode_responses=False`. This is consistent with raw `redis-py` behavior.
The fix at the call site is to construct `Redis(decode_responses=True)`. Do
not "fix" this in `executor.py` by force-decoding; that breaks users who
deliberately want bytes (binary fields, vectors). See
[Result shape](../concepts/result-shape.md) for the full story.

## Stopwords are silently stripped

`WHERE title = 'bank of america'` becomes `@title:"bank america"` because
RediSearch does not index default stopwords. A `UserWarning` is emitted, but
the query proceeds. To preserve stopwords the user must create the index
with `STOPWORDS 0`. This is intentional: failing the query would be worse
than warning and proceeding.

## `=` on TEXT fields is exact phrase, not tokenized AND

`title = 'gaming laptop'` translates to `@title:"gaming laptop"` (phrase
match), not `@title:(gaming laptop)` (tokenized AND). For tokenized search
the user must call `fulltext(title, 'gaming laptop')`. The two are
semantically different; do not collapse them.

## `OR` inside `fulltext()` is case-sensitive

`fulltext(title, 'a OR b')` triggers a union (`@title:(a|b)`).
`fulltext(title, 'a or b')` is treated as a literal three-word AND search
(`@title:(a or b)`). This matches RediSearch's own grammar and is documented;
do not silently normalize the case.

## `IS NULL` requires Redis 7.4+ AND `INDEXMISSING`

`WHERE email IS NULL` translates to `ismissing(@email)`. If the field was
not declared with `INDEXMISSING` at index creation time, Redis returns a
syntax error. The executor catches this case and rewraps the
`redis.ResponseError` with a hint about Redis 7.4 + `INDEXMISSING`. If the
catch is breaking, check that the wrap heuristic still matches new
RediSearch error messages.

## `Translator.translate(sql)` re-parses even if you already parsed

`AsyncExecutor` deliberately calls `parse(sql)` first to extract the index
name, then calls `translate_parsed(parsed)` to avoid double-parsing. If you
add a new code path, prefer `translate_parsed` when you already have a
`ParsedQuery`. Calling `translate(sql)` from a code path that already parsed
is wasteful but not incorrect.

## `AsyncSchemaRegistry.invalidate()` cancels in-flight loads

Cancelling the in-flight `FT.INFO` is intentional: it prevents a
post-invalidate stale write into the cache. The shielded `await` in
`ensure_schema()` returns the current cache state when the underlying task
is cancelled, so other awaiters do not propagate `CancelledError`. If you
"simplify" by removing the shield, you reintroduce a race. See
[Async invariants](../concepts/async-invariants.md).

## Lazy schema-load failures are deferred

The default `schema_cache_strategy="lazy"` means a missing index does not
fail at `create_executor()` time. It fails at the first `execute()` call
that touches the index. This is intentional. If you need fail-fast at
startup, pass `schema_cache_strategy="load_all"`. Do not change the default.

## `score()` plus aggregation raises ValueError

This is not a bug. `score()` requires `WITHSCORES`, which is `FT.SEARCH`
only. Anything that forces `FT.AGGREGATE` (aggregations, GROUP BY, computed
fields, date functions, geo > / >= / BETWEEN, HAVING) cannot coexist with
`score()`. The translator surfaces the conflict explicitly rather than
silently dropping one side. See [FT.SEARCH vs FT.AGGREGATE](../concepts/search-vs-aggregate.md).

## `OR` plus geo > / >= / BETWEEN raises ValueError

Same family as above. Greater-than-distance is implemented as a top-level
`FILTER` clause, which is ANDed with the rest of the query. Combining with
SQL-level `OR` would silently change semantics, so it is rejected. Same for
date-function predicates combined with `OR`.

## Coverage gate failures are not flakes

If CI reports coverage below 100%, do not retry. The failure is real. Either
add a test or delete the unreachable branch. The project explicitly forbids
`# pragma: no cover` (see [Testing philosophy](../concepts/testing-philosophy.md)).
