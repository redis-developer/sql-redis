---
myst:
  html_meta:
    "description lang=en": |
      sql-redis user guide. Installation, getting started, and task-oriented recipes.
---

# User Guide

::::{grid} 2
:gutter: 3

:::{grid-item-card} 📦 Installation
:link: installation
:link-type: doc

**Set up sql-redis.** pip install, Redis container, optional extras.
:::

:::{grid-item-card} 🚀 Getting Started
:link: getting-started
:link-type: doc

**Your first query.** Schema setup, executor construction, end-to-end SELECT.
:::

:::{grid-item-card} 🛠️ How-To Guides
:link: how_to_guides/index
:link-type: doc

**Solve specific problems.** Recipes for parameters, vectors, text search, GEO, dates, async, and schema strategy.
:::

::::

```{toctree}
:maxdepth: 2
:hidden:

installation
getting-started
how_to_guides/index
```
