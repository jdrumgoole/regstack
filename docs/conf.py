"""Sphinx configuration for regstack.

Markdown sources via myst-parser; theme is Furo. The autodoc / autosummary
build pulls public API straight off the package, so adding a new public
class is a one-line edit in `api.md`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the in-tree package importable for autodoc without an editable install.
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root / "src"))

from regstack.version import __version__ as _regstack_version  # noqa: E402

# -- Project information -----------------------------------------------------
project = "regstack"
copyright = "2026, Joe Drumgoole"
author = "Joe Drumgoole"
release = _regstack_version
version = ".".join(_regstack_version.split(".")[:2])

# -- General configuration ---------------------------------------------------
extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinx_autodoc_typehints",
    "sphinx_copybutton",
]

source_suffix = {".md": "markdown", ".rst": "restructuredtext"}
master_doc = "index"
templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# Suppress noisy autodoc warnings on dynamically-typed pydantic helpers.
suppress_warnings = ["autodoc.import_object"]

# -- Markdown / MyST ---------------------------------------------------------
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "fieldlist",
    "tasklist",
    "substitution",
]
myst_heading_anchors = 3
myst_substitutions = {"version": _regstack_version}

# -- Autodoc -----------------------------------------------------------------
autosummary_generate = True
autodoc_default_options = {
    "members": True,
    "show-inheritance": True,
    "undoc-members": False,
    "exclude-members": "model_config,model_fields,model_computed_fields",
}
autodoc_typehints = "description"
autodoc_member_order = "bysource"
typehints_fully_qualified = False
always_document_param_types = True

# -- Intersphinx -------------------------------------------------------------
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "fastapi": ("https://fastapi.tiangolo.com/", None),
    "pydantic": ("https://docs.pydantic.dev/latest/", None),
}

# -- HTML output -------------------------------------------------------------
html_theme = "furo"
html_title = f"regstack {release}"
html_static_path = ["_static"]
html_show_sourcelink = False
html_theme_options = {
    "source_repository": "https://github.com/jdrumgoole/regstack",
    "source_branch": "main",
    "source_directory": "docs/",
    "footer_icons": [],
}

# Build cleanly under -W (warnings-as-errors) — keep this list short and
# justified. Right now nothing is suppressed.
nitpicky = False
