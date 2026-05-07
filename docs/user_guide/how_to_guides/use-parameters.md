# Use parameters

You want to inject runtime values into a SQL string without manually building the query.

## Recipe

Use `:name` placeholders and pass values via `params`:

```python
result = executor.execute(
    "SELECT title FROM products WHERE category = :cat AND price < :max_price",
    params={"cat": "electronics", "max_price": 500},
)
```

## What gets substituted

| Python type | SQL substitution |
|---|---|
| `int`, `float` | unquoted literal: `123`, `12.5` |
| `str` | quoted with SQL standard escaping: `'O''Brien'` |
| `bytes` | kept; substituted later as the `$vector` for KNN |
| anything else | kept as `:name`; the translator handles it (or raises) |

## Quote escaping is automatic

```python
executor.execute(
    "SELECT * FROM users WHERE name = :name",
    params={"name": "O'Brien"},
)
# Produces: WHERE name = 'O''Brien'
```

## Similar parameter names are safe

The substitution is token-based, so `:id` will not match inside `:product_id`:

```python
executor.execute(
    "SELECT * FROM rows WHERE id = :id AND product_id = :product_id",
    params={"id": 1, "product_id": 100},
)
```

## Vectors

Pass a vector as `bytes` for use in a KNN query:

```python
import struct

vec = struct.pack(f"{len(embedding)}f", *embedding)

result = executor.execute(
    "SELECT title, vector_distance(embedding, :vec) AS score FROM products LIMIT 5",
    params={"vec": vec},
)
```

The `bytes` value is intentionally not stringified into the SQL. The executor injects it into the Redis command list as raw bytes after translation.

## See also

- {doc}`/concepts/parameter-substitution` for why the substitution is token-based.
