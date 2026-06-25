# Vector and hybrid search

You want to find the K most similar items to a query embedding, optionally filtered by metadata.

## Prerequisites

- An index with a `VECTOR` field, e.g.:
  ```
  FT.CREATE products ON HASH PREFIX 1 product:
    SCHEMA
    title TEXT
    category TAG
    embedding VECTOR FLAT 6 TYPE FLOAT32 DIM 1536 DISTANCE_METRIC COSINE
  ```
- Embeddings stored as `FLOAT32` byte arrays in the `embedding` field.

## Pure KNN

```python
import struct

query_vec = struct.pack(f"{len(embedding)}f", *embedding)

result = executor.execute(
    """
    SELECT title, vector_distance(embedding, :vec) AS score
    FROM products
    LIMIT 5
    """,
    params={"vec": query_vec},
)
```

`vector_distance(field, :param)` is the function that triggers a KNN search. The `LIMIT` becomes the K value.

## Pre-filter hybrid search (filter then KNN)

Combine a `WHERE` clause with `vector_distance`. Here text and tags act only as a
hard filter and the ranking comes from the vector leg alone. For true text plus
vector fusion, where both legs are ranked independently and combined, see
[Hybrid fusion (FT.HYBRID)](#hybrid-fusion-fthybrid) below.

```python
result = executor.execute(
    """
    SELECT title, vector_distance(embedding, :vec) AS score
    FROM products
    WHERE category = 'electronics' AND price < 1000
    LIMIT 5
    """,
    params={"vec": query_vec},
)
```

The filter narrows the candidate set; the KNN runs over what survives.

## Hybrid fusion (FT.HYBRID)

`hybrid_vector_search()` fuses a full-text query and a vector query into a single
ranking server-side using Redis `FT.HYBRID` (Redis 8.4+, redis-py >= 7.1.0). Unlike
pre-filter hybrid search above, both legs are ranked independently and combined with
reciprocal rank fusion (RRF) or a linear weighting, so strong text matches and strong
vector matches both surface.

It composes the vector function (`cosine_distance` or `vector_distance`) and the text
function (`fulltext`), with `rrf()` or `linear()` selecting the fusion method:

```python
result = executor.execute(
    """
    SELECT title,
           hybrid_vector_search(
               cosine_distance(embedding, :vec),
               fulltext(title, 'gaming laptop'),
               rrf()
           ) AS hybrid_score
    FROM products
    WHERE category = 'electronics'
    ORDER BY hybrid_score DESC
    LIMIT 5
    """,
    params={"vec": query_vec},
)
```

- The vector leg (`cosine_distance(field, :vec)`) and the text leg
  (`fulltext(field, 'query')`) are ranked separately and then fused.
- A `WHERE` clause is applied to both legs as a filter.
- `AS hybrid_score` returns the fused score as a column; `ORDER BY hybrid_score DESC`
  sorts by it.

### Fusion methods and knobs

`rrf()` (the default) uses reciprocal rank fusion; `linear()` uses a weighted sum
where `alpha` weights the text leg and `beta` is derived as `1 - alpha`:

```python
# RRF with explicit knobs
hybrid_vector_search(
    cosine_distance(embedding, :vec),
    fulltext(title, 'laptop'),
    rrf(constant => 60, window => 20)
)

# LINEAR weighting
hybrid_vector_search(
    cosine_distance(embedding, :vec),
    fulltext(title, 'laptop'),
    linear(alpha => 0.3)
)
```

A custom text scorer can be set on the text leg
(`fulltext(title, 'laptop', scorer => 'BM25STD')`). Vector-leg tuning rides on the
`vector_distance` / `vector_range` forms rather than `cosine_distance`:

```python
# KNN exploration factor
hybrid_vector_search(
    vector_distance(embedding, :vec, ef_runtime => 20),
    fulltext(title, 'laptop'),
    rrf()
)

# Vector range instead of KNN
hybrid_vector_search(
    vector_range(embedding, :vec, radius => 0.2),
    fulltext(title, 'laptop'),
    rrf()
)
```

## Returning the score

`vector_distance(...) AS alias` is required for the score to come back as a column. The result rows include the alias as a key.
