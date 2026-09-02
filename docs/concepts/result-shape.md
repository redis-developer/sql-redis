# Result Shape

Every `Executor.execute()` call returns a `QueryResult` with two attributes: `rows` (a list of dicts) and `count` (an integer). Both look simple but have a few corners that are worth understanding once so you do not re-derive them from test failures.

## What `count` means

`count` is whatever Redis reported for the query: position 0 of a RESP2 array reply, or `total_results` in a RESP3 map reply. Its meaning depends on which command ran (see [FT.SEARCH vs FT.AGGREGATE](search-vs-aggregate.md)):

- **`FT.SEARCH`** (no aggregation): `count` is the **total number of matching documents**, regardless of `LIMIT`. So `count` can be much larger than `len(rows)`, which is what makes it useful for pagination. Both protocols report the same figure.
- **`FT.AGGREGATE`** (any aggregation, GROUP BY, computed field, date function): `count` is **not the number of rows you got back**, and it is the one value that differs by protocol. With a `GROUP BY`, both protocols report the number of groups before `LIMIT`. Without one, a computed-field projection leaves RESP2 reporting `1` (a placeholder the [Redis docs](https://redis.io/docs/latest/commands/ft.aggregate/) call "not a valid value") while RESP3 reports the real figure. Use `len(rows)` if you want the rows you received.

Neither aggregate value can be derived from the other without changing what `protocol=2` callers already get, so the library reports what Redis sent.

## What `rows` looks like

A row is always a `dict`, but the *keys* and *values* depend on four orthogonal factors.

### Factor 1: client decoding

A `Redis()` client without `decode_responses=True` returns bytes. A row therefore looks like:

```python
{b"title": b"gaming laptop", b"price": b"1499"}
```

`Redis(decode_responses=True)` returns strings:

```python
{"title": "gaming laptop", "price": "1499"}
```

The library does not normalise this. If you want strings, configure the client. If you want native types (e.g., `1499` as `int`), parse them yourself.

### Factor 2: which command ran

`FT.SEARCH` rows contain only the document fields you asked for in `SELECT`. `FT.AGGREGATE` rows can include computed columns, group keys, and reduced values; original document fields appear only if you asked for them.

```python
# FT.SEARCH: SELECT title, price FROM products WHERE ...
{b"title": b"gaming laptop", b"price": b"1499"}

# FT.AGGREGATE: SELECT category, COUNT(*) AS n FROM products GROUP BY category
{b"category": b"electronics", b"n": b"2"}

# FT.AGGREGATE: SELECT name, geo_distance(loc, POINT(...)) AS d FROM stores
{b"name": b"Downtown", b"d": b"4823.5"}
```

### Factor 3: scoring

If your SELECT contains `score()`, the underlying `FT.SEARCH` runs with `WITHSCORES` and an extra column appears in every row:

```python
# SELECT title, score() AS relevance FROM products WHERE fulltext(...)
{b"title": b"gaming laptop", b"relevance": b"0.5"}   # RESP2
{b"title": b"gaming laptop", b"relevance": 0.5}      # RESP3
```

The score column name is whatever alias you wrote (`score() AS relevance` produces `relevance`). If you used `score()` without `AS`, the library falls back to a stable internal name.

The score's value type depends on the wire protocol: `str` (or `bytes`, depending on decoding) on RESP2, and `float` on RESP3, where Redis sends it as a double. `float()` is the protocol-safe conversion at the call site and works on both.

### Factor 4: `RETURN 0`

If your SELECT is just `score()` with no document fields, the underlying command uses `RETURN 0` (no document fields returned). Each row contains only the score column:

```python
# SELECT score() AS s FROM products WHERE fulltext(title, 'laptop')
{b"s": b"0.5"}
```

This is mostly useful when you want a relevance-only ranking without paying to ship the document body back.

## A note on the wire protocol

Redis replies to `FT.SEARCH` and `FT.AGGREGATE` with a flat array on RESP2 and a map on RESP3, and `sql_redis` reads the raw reply, so the protocol your client negotiated decides which shape arrives. The library parses both and the rows come out the same, but two values differ: the score's type (Factor 3) and the aggregate `count` described above.

Worth knowing because redis-py 8 defaults to RESP3 where earlier versions defaulted to RESP2, so an upgrade can change both without you passing `protocol` at all.

## Score-column collision avoidance

What happens if your document has a field literally named `__score`? The library detects the collision and renames the score column. The deterministic fallback is `__score_<alias>`. So:

- `score() AS __score` against documents with no `__score` field: row key is `__score`.
- `score() AS __score` against documents with a field named `__score`: row key is `__score___score`.

You will not normally see this; it is a defensive measure for the unusual case.

## Why the library does not "fix" this

It would be tempting to normalise everything: always strings, always `float` scores, always uniform shape. The library does not, for three reasons.

1. **Bytes is the right default for Redis.** Some fields legitimately contain binary data. Force-decoding to UTF-8 would corrupt those. The same argument covers the RESP3 score: stringifying a double cannot reproduce the text Redis would have sent on RESP2, so normalising would invent a third form rather than deliver parity.
2. **The shape difference between `FT.SEARCH` and `FT.AGGREGATE` is intrinsic to the Redis commands.** Hiding it would lie about what is happening.
3. **One-call-site logic is cheap; library-wide policy is expensive.** A user who wants a uniform shape can write a 5-line decoder once, customised to their data. The library shipping that decoder for everyone is the wrong place for the policy.

## When in doubt

```python
result = executor.execute(sql)
print(repr(result.rows[0]) if result.rows else "(no rows)")
```

The repr tells you the keys, values, and types in front of you. Build your downstream code against what you actually see.
