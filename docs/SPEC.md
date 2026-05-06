# Documentation Spec

## Goal

Replace the single 456-line README with a Diátaxis-aligned Sphinx documentation site, modeled on `redis-vl-python`'s `docs/` layout, so that:

1. Each piece of content lives in the quadrant that matches its purpose (learning, task, reference, understanding).
2. The full public API surface (currently 11 exported symbols) is discoverable.
3. Docstrings in `sql_redis/` are the single source of truth for API reference (via Sphinx `autoclass`).
4. The site can be built locally and published to Read the Docs.

## Diátaxis Recap

| Quadrant | User need | Voice | Folder |
|---|---|---|---|
| Tutorial | "Teach me" (learning) | Hand-holding, end-to-end | `user_guide/getting-started.md` |
| How-to | "Help me do X" (task) | Recipe, presumes knowledge | `user_guide/how_to_guides/` |
| Reference | "Tell me what" (info) | Dry, complete, accurate | `api/`, `api/sql-syntax.md` |
| Explanation | "Help me understand" (theory) | Discursive, context, history | `concepts/` |

Tutorials and how-to are both in `user_guide/` (matching the redis-vl-python layout) but kept structurally distinct.

## Folder Layout

```
docs/
  index.md                          landing page with grid cards
  conf.py                           Sphinx config (myst, autoclass, sphinx_book_theme, sphinx_design)
  Makefile                          standard sphinx makefile

  concepts/                         EXPLANATION
    index.md                          grid landing
    architecture.md                   layered pipeline + diagram
    why-sql.md                        SQL vs pandas-like vs builder
    why-sqlglot.md                   sqlglot vs custom parser
    schema-aware-translation.md       why FT.INFO matters, lazy vs eager
    parameter-substitution.md         token-based substitution rationale (from PARAMETER_SUBSTITUTION.md)
    testing-philosophy.md             TDD, 100% coverage

  user_guide/                       TUTORIAL + HOW-TO
    index.md                          grid landing
    installation.md                   pip install, Redis setup
    getting-started.md                first end-to-end query
    how_to_guides/
      index.md
      use-parameters.md               token substitution, vector params
      vector-search.md                KNN, hybrid filter+vector
      text-search.md                  exact phrase, fuzzy, proximity, BM25
      geo-queries.md                  POINT, units, operators
      date-queries.md                 ISO literals, YEAR/MONTH/DAY
      missing-fields.md               IS NULL, exists()
      lazy-vs-eager-schemas.md        SchemaCacheStrategy
      async-usage.md                  AsyncExecutor, AsyncSchemaRegistry

  api/                              REFERENCE
    index.md                          TOC
    translator.rst                    Translator, TranslatedQuery (autoclass)
    schema.rst                        SchemaRegistry, AsyncSchemaRegistry (autoclass)
    executor.rst                      Executor, AsyncExecutor, QueryResult, factories (autoclass)
    sql-syntax.md                     reference tables (TEXT, GEO, dates) extracted from README

  examples/
    index.md                          placeholder pointing back to user_guide
```

## API Reference Generation

Use `sphinx.ext.autodoc` + `sphinx.ext.napoleon` (Google-style docstrings, which the codebase already uses).

Each `.rst` file declares the symbols with `autoclass :members:` so the docstrings already in `executor.py`, `schema.py`, `translator.py` become the rendered reference. No duplication, no drift.

## README

Trim to about 80 lines: tagline, one-screen quick example, install, link to docs site, status note. The reference tables and design discussions move to docs.

## Root Files

- `PR_NOTES.md`: delete (transient PR description, has no place at repo root).
- `PARAMETER_SUBSTITUTION.md`: content migrated to `concepts/parameter-substitution.md`, root file deleted.

## Build Targets

Root `Makefile` gains, mirroring redis-vl-python:

```make
docs-build:    uv run make -C docs html
docs-serve:    uv run python -m http.server --directory docs/_build/html
```

`docs/Makefile` is the standard Sphinx-generated catch-all that delegates to `sphinx-build`.

## Dependencies

Add a `docs` dependency group to `pyproject.toml`:

```toml
[dependency-groups]
docs = [
    "sphinx>=7.3",
    "sphinx-book-theme>=1.1",
    "sphinx-design>=0.6",
    "sphinx-copybutton>=0.5",
    "myst-parser>=3.0",
]
```

## Read the Docs

Add `.readthedocs.yaml` so the site can be published. Build uses uv with `--group docs`.

## Out of Scope

- No Jupyter notebook tutorials (per user instruction).
- No CONTRIBUTING.md (separate concern).
- No MCP / connector pages (those don't exist for sql-redis).
