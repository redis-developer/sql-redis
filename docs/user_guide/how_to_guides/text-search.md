# Full-text search

You want to search a `TEXT` field beyond simple equality. Each section below answers one task.

For the complete catalog of supported modes and their RediSearch translation, see the [SQL Syntax reference](../../api/sql-syntax.md).

## Match an exact phrase

```python
executor.execute(
    "SELECT * FROM products WHERE title = :phrase",
    params={"phrase": "gaming laptop"},
)
```

`=` on a `TEXT` field is **exact phrase match**, not tokenized AND. Redis sees `@title:"gaming laptop"`. Stopwords like `of` are stripped automatically with a `UserWarning`; see "Keep stopwords in matches" below.

## Match all of several words, in any order

Use `fulltext()` for tokenized AND search:

```python
executor.execute(
    "SELECT * FROM products WHERE fulltext(title, :terms)",
    params={"terms": "gaming laptop"},
)
```

Redis sees `@title:(gaming laptop)`. A title containing both words in either order matches.

## Match either of several words

Use uppercase `OR` inside `fulltext()`:

```python
executor.execute(
    "SELECT * FROM products WHERE fulltext(title, 'laptop OR tablet OR phone')",
)
```

The `OR` must be uppercase. Lowercase `or` is treated as a literal third word.

## Match across multiple fields

Use `OR` between two `fulltext()` calls:

```python
executor.execute("""
    SELECT * FROM products
    WHERE fulltext(title, 'laptop') OR fulltext(description, 'laptop')
""")
```

## Match with typos

Use `fuzzy()` and pass an optional Levenshtein distance (1, 2, or 3):

```python
executor.execute(
    "SELECT * FROM products WHERE fuzzy(title, :term, 2)",
    params={"term": "laptap"},
)
```

Distance 1 catches one-character typos; 2 catches most common misspellings; 3 is permissive and slow.

## Match a prefix, suffix, or substring

Use `LIKE` with `%`:

```python
executor.execute("SELECT * FROM products WHERE title LIKE 'lap%'")    # prefix
executor.execute("SELECT * FROM products WHERE title LIKE '%top'")    # suffix
executor.execute("SELECT * FROM products WHERE title LIKE '%apt%'")   # contains
```

Underscore (`_`) wildcards from standard SQL are not supported.

## Require words to be near each other

Use `fulltext()` with a slop value (max words allowed between the terms):

```python
executor.execute(
    "SELECT * FROM products WHERE fulltext(title, :phrase, 2)",
    params={"phrase": "gaming laptop"},
)
```

To require the words appear in the given order, pass `true` as the fourth argument:

```python
executor.execute(
    "SELECT * FROM products WHERE fulltext(title, 'gaming laptop', 2, true)",
)
```

## Mark a term as optional but boosting

Prefix a term with `~` inside `fulltext()`:

```python
executor.execute(
    "SELECT * FROM products WHERE fulltext(title, 'laptop ~gaming')",
)
```

Documents matching `laptop` rank higher when `gaming` is also present. Documents without `gaming` still match.

## Exclude documents with a term

Use `NOT`:

```python
executor.execute(
    "SELECT * FROM products WHERE fulltext(title, 'laptop') AND NOT fulltext(title, 'refurbished')",
)
```

## Get a relevance score back

Add `score()` to the `SELECT` list:

```python
result = executor.execute("""
    SELECT title, score() AS relevance
    FROM products
    WHERE fulltext(title, 'laptop')
""")

for row in result.rows:
    print(row[b"title"], row[b"relevance"])
```

`score()` triggers `WITHSCORES` in the underlying `FT.SEARCH`. The score is BM25 by default. The result-row shape changes when scoring is enabled; see [Result shape](../../concepts/result-shape.md).

## Keep stopwords in matches

By default, both `=` and `fulltext()` strip Redis's default stopwords (about 300 common words like `the`, `a`, `of`) before sending the query. RediSearch does not index these by default, so an unstripped query would silently match nothing.

If your data needs stopwords to match, create the index with `STOPWORDS 0`:

```
FT.CREATE myindex ON HASH PREFIX 1 doc: STOPWORDS 0 SCHEMA title TEXT
```

This is an index-creation choice, not a per-query choice. Once the index has been built without stopword filtering, all queries against it preserve them.

## Common gotchas

- `fuzzy()` and `fulltext()` only work on `TEXT` fields. Calling them on `TAG` or `NUMERIC` raises `ValueError`.
- Special characters in search terms (`@`, `|`, `-`, `*`, `+`) are escaped automatically by sql-redis.
- A `UserWarning` is emitted when stopwords are stripped, so you can audit which terms are dropping out.
