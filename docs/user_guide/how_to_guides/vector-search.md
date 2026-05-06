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

## Hybrid: filter then KNN

Combine a `WHERE` clause with `vector_distance`:

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

## Returning the score

`vector_distance(...) AS alias` is required for the score to come back as a column. The result rows include the alias as a key.
