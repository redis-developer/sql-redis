"""Sphinx configuration for sql-redis documentation."""

import os
import sys

sys.path.insert(0, os.path.abspath(".."))

from sql_redis import __version__

project = "sql-redis"
copyright = "2026, Redis Inc."
author = "Redis Applied AI"
version = __version__
release = version

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_design",
    "sphinx_copybutton",
    "myst_parser",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store", "SPEC.md"]

html_theme = "sphinx_book_theme"
html_title = "sql-redis"
html_static_path = ["_static"] if os.path.isdir("_static") else []

html_theme_options = {
    "repository_url": "https://github.com/redis-developer/sql-redis",
    "use_repository_button": True,
    "use_edit_page_button": True,
    "use_issues_button": True,
    "repository_branch": "main",
    "path_to_docs": "docs",
    "show_navbar_depth": 2,
    "navigation_depth": 4,
    "show_toc_level": 3,
    "home_page_in_toc": True,
}

myst_enable_extensions = ["colon_fence", "deflist"]
myst_heading_anchors = 3

autoclass_content = "both"
autodoc_member_order = "groupwise"
autodoc_typehints = "description"
add_module_names = False

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_include_init_with_doc = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "redis": ("https://redis-py.readthedocs.io/en/stable/", None),
    "redisvl": ("https://docs.redisvl.com/", None),
}
