# Why sqlglot?

Once we picked SQL as the surface, we needed a parser.

## Options considered

- **Custom parser** (regex, hand-rolled recursive descent)
- **PLY or Lark** (parser generators)
- **sqlglot** (production SQL parser)
- **sqlparse** (a tokenizer, not a full parser)

## We chose sqlglot because:

1. **Battle-tested.** Used in production by SQLMesh and many other projects. Handles edge cases we would miss.
2. **Full AST.** Provides a complete abstract syntax tree, not just tokens. We can traverse and analyze queries properly.
3. **Dialect support.** Handles SQL variations. Users can write MySQL-style or PostgreSQL-style queries.
4. **Active maintenance.** Regular releases, responsive maintainers, good documentation.

The alternative was writing a custom parser, which would be error-prone and time-consuming. sqlglot lets us focus on the translation logic rather than parsing edge cases.
