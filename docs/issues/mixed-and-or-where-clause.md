# Parser flattens mixed AND/OR WHERE clauses, losing parenthesization (e.g. `A AND (B OR C)` becomes `A|B|C`)

## Summary
When a SQL `WHERE` clause mixes `AND` and `OR` (with or without explicit parentheses), `sql-redis` collapses the boolean expression into a flat list of conditions joined by a single operator. The chosen operator is whichever of `AND`/`OR` appears **last** during recursive AST traversal, so the resulting RediSearch query has the wrong logical semantics.

## Reproduction
The input SQL → produced RediSearch query string mappings:

| SQL `WHERE` clause              | Current output     | Expected output     |
|---------------------------------|--------------------|---------------------|
| `A AND (B OR C)`                | `(A\|B\|C)`         | `A (B\|C)`           |
| `A OR (B AND C)`                | `A B C`            | `(A\|(B C))`         |
| `(B OR C) AND A`                | `(B\|C\|A)`         | `(B\|C) A`           |
| `A AND B AND C AND (D OR E)`    | `(A\|B\|C\|D\|E)`    | `A B C (D\|E)`        |

(Where `A`, `B`, … stand for any rendered conditions like `@title:hello`, `@price:[(50 +inf]`, `@category:{books}`, etc.)

A minimal example:

```python
from sql_redis.translator import Translator
t = Translator(...)  # with a schema where title/category are TAG/TEXT and price is NUMERIC
t.translate(
    "SELECT * FROM idx WHERE category = 'books' AND (price < 10 OR price > 100)"
).query_string
# → '(@category:{books}|@price:[-inf (10]|@price:[(100 +inf])'
# expected: '@category:{books} (@price:[-inf (10]|@price:[(100 +inf])'
```

## Root cause
In `sql_redis/parser.py`, `_process_where_clause` walks `exp.And` / `exp.Or` recursively but appends every leaf condition to a single flat `result.conditions` list and stores only one `result.boolean_operator`:

```python
elif isinstance(expression, exp.And):
    result.boolean_operator = "AND"
    self._process_where_clause(expression.this, result, negated)
    self._process_where_clause(expression.expression, result, negated)
elif isinstance(expression, exp.Or):
    result.boolean_operator = "OR"
    self._process_where_clause(expression.this, result, negated)
    self._process_where_clause(expression.expression, result, negated)
elif isinstance(expression, exp.Paren):
    self._process_where_clause(expression.this, result, negated=negated)
```

`exp.Paren` is unwrapped without preserving any grouping marker, and each visit to `exp.And`/`exp.Or` overwrites `boolean_operator`. Whichever node is visited last wins.

`Translator._build_query_string` then hands the flat list to `QueryBuilder.combine_conditions(conditions, parsed.boolean_operator)`, which joins everything with either `|` (for OR) or space (for AND). There is no representation of nested groups.

## Impact
- Silently incorrect results for any query mixing `AND` and `OR`, including with explicit parentheses.
- Affects all field types (TEXT, TAG, NUMERIC) and any features built on top of the WHERE clause (vector prefilters, FT.SEARCH, FT.AGGREGATE).
- Particularly dangerous because no error is raised — queries succeed and return the wrong rows.

## Suggested fix
Replace the flat `conditions: list[Condition]` + single `boolean_operator: str` model with a tree (e.g. `BooleanNode = And(list[Node]) | Or(list[Node]) | Leaf(Condition)`):

1. In the parser, build the tree by returning a node from `_process_where_clause` instead of mutating a flat list. `exp.Paren` should preserve the wrapped subtree as-is so it stays a single subexpression.
2. In `Translator._build_query_string` / `QueryBuilder.combine_conditions`, render the tree recursively, wrapping `Or` groups in `(...)` and joining `And` children with spaces, e.g.:
   - `And(A, Or(B, C))` → `A (B|C)`
   - `Or(A, And(B, C))` → `(A|(B C))`
3. Keep the existing special handling (geo predicates, date FILTERs, `ismissing`, vector prefilter wrapping) but apply it per-node rather than across the whole flat list. Note that the existing rule "geo_distance cannot be combined with OR" should be re-evaluated against the tree (i.e. the geo leaf must not appear underneath any `Or` node).

## Tests to add
Suggested coverage in `tests/test_translator.py` (unit) and `tests/test_sql_queries.py` (integration):

- `A AND (B OR C)` produces `A (B|C)` and returns rows matching `A AND (B OR C)`.
- `(B OR C) AND A` produces `(B|C) A`.
- `A OR (B AND C)` produces `(A|(B C))`.
- `A AND B AND C AND (D OR E)` produces `A B C (D|E)`.
- Nested groups: `(A OR B) AND (C OR D)` → `(A|B) (C|D)`.
- `NOT (A OR B)` and `NOT (A AND B)` continue to apply negation correctly per leaf.
- Existing behavior for pure-AND and pure-OR clauses is unchanged.

## Workaround
Until this is fixed, rewrite mixed boolean clauses into an equivalent form without grouping, e.g. distribute manually:
- `A AND (B OR C)` → run two queries (`A AND B`, `A AND C`) and union client-side, or
- restructure data/index so the predicate can be expressed with a single operator (e.g. use `IN (...)` for tag unions: `A AND tag IN (B, C)`).
