# Parameter Substitution

`Executor.execute(sql, params=...)` lets a caller inject runtime values into a SQL string. The implementation is deliberately simple. Understanding why is worth a page because two earlier approaches were tried and rejected.

## What goes wrong without care

A first-cut implementation (`for k, v in params: sql.replace(f":{k}", str(v))`) breaks in two ways that occur in real data, not contrived examples.

**Apostrophes break SQL parsing.** Names like `O'Brien`, words like `it's`, products like `McDonald's`: dropping these directly into a SQL string produces `name = 'O'Brien'`, which is a syntax error. The fix is the SQL-standard escape: `'` becomes `''`. So the substitution must wrap strings in quotes and escape internal quotes before inserting them.

**Similar parameter names overlap.** `:id` is a prefix of `:product_id`. A naive replace of `:id` matches inside `:product_id` and corrupts both. The fix is to recognise complete parameter tokens, not substrings.

These are not edge cases. The first hit any database with European names; the second hits anything with `:id` and a more specific identifier on the same query.

## The three approaches considered

| Approach | Lines | Deps | Fixes both bugs |
|---|---|---|---|
| `str.replace()` per param | 3 | none | no |
| Token-based regex | 30 | stdlib `re` | yes |
| sqlglot parse-and-rewrite | 60 | `sqlglot` | yes |

We chose token-based.

## Why token-based, not parser-based

A regex split on the parameter pattern, keeping each `:name` whole, fixes both bugs without invoking a SQL parser. The pattern is:

```
(:[a-zA-Z_][a-zA-Z0-9_]*)
```

Two properties of this pattern matter. First, the parenthesis means the matched delimiter is *kept* in the split output, so the substitution function can inspect it and either replace it (it's a known parameter) or leave it (it isn't). Second, the regex demands a complete identifier ending at a non-identifier character, so `:id` and `:product_id` come out as different tokens.

The sqlglot route would be more general, in particular for the theoretical case of a colon literal embedded in a SQL string (`'admin:test@example.com'`). It would also be slower, dependency-heavier, and would have to fall back somewhere when sqlglot fails to parse a query that the existing pipeline accepts. The colon-in-literal pattern has not appeared in any real query we have seen; users pass the troublesome string as a parameter, not a literal. The trade-off was clear.

## Why bytes are not stringified

A substitution function that handled string and numeric types but not bytes would force vector queries to either pre-encode their vectors as base64 strings (and have RediSearch reject them) or use a side-channel API. Neither is good.

The library's answer is the **two-stage substitution** described in {doc}`vector-substitution`. Briefly: bytes parameters are intentionally *skipped* at the string-substitution stage. The translator emits a `$vector` placeholder, and the executor injects the raw bytes into the Redis command list after translation, where Redis accepts them natively. From the caller's perspective, vector params look identical to other params.

## What this concept does not cover

The full table of which Python types substitute into what SQL form lives in the how-to ({doc}`/user_guide/how_to_guides/use-parameters`). The reference for the regex pattern itself lives in `sql_redis/executor.py::_substitute_params`. This page is the *why*, not the *how* or *what*.
