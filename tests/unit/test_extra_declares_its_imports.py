"""Regression test: an extra must declare everything its modules import.

regstack 0.8.6 shipped with ``regstack/oauth/providers/google.py``
importing ``httpx`` at module scope while the ``oauth`` extra declared
only ``pyjwt[crypto]`` and ``cryptography``. A host that ran ``pip
install regstack[oauth]`` and set ``enable_oauth = True`` got

    ModuleNotFoundError: No module named 'httpx'

from ``RegStack.__init__`` — at construction time, not import time, so
nothing caught it until the app tried to boot. It went unnoticed in
development because the ``dev`` extra happens to install httpx for the
test suite.

Rather than pin the one missing name, this walks the optional
subpackages and checks every third-party module they import at module
scope is declared somewhere reachable from the matching extra. A new
provider that reaches for a new library fails here until it is
declared.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "regstack"

# Subpackages that only load when an extra is installed, and the extra
# whose dependency list has to cover their module-scope imports.
GATED_PACKAGES = {
    "oauth": "oauth",
    "backends/mongo": "mongo",
}

# Import name -> distribution name, where they differ.
DISTRIBUTION_OF = {
    "jwt": "pyjwt",
    "bson": "pymongo",
    "yaml": "pyyaml",
}

# Modules that are always present: the standard library plus regstack's
# own base dependencies. Anything here needs no extra.
STDLIB = set(sys.stdlib_module_names)


def _base_and_extra_distributions(extra: str) -> set[str]:
    """Distribution names available with ``pip install regstack[<extra>]``."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    project = data["project"]
    requirements = list(project.get("dependencies", []))
    requirements += project.get("optional-dependencies", {}).get(extra, [])

    names = set()
    for requirement in requirements:
        # "pyjwt[crypto]>=2.13.0" -> "pyjwt"
        name = requirement.split(";")[0].strip()
        for separator in ("[", ">", "<", "=", "!", "~", " "):
            name = name.split(separator)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def _module_scope_imports(path: pathlib.Path) -> set[str]:
    """Top-level import names in one module, ignoring guarded ones.

    Imports nested inside functions or ``if TYPE_CHECKING:`` blocks are
    deliberately skipped — those are the legitimate way to reach an
    optional dependency without requiring it.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_gated_packages_declare_their_module_scope_imports() -> None:
    undeclared: list[str] = []

    for relative, extra in GATED_PACKAGES.items():
        package = SRC / relative
        if not package.is_dir():
            continue
        available = _base_and_extra_distributions(extra)

        for module in sorted(package.rglob("*.py")):
            for imported in sorted(_module_scope_imports(module)):
                if imported in STDLIB or imported == "regstack":
                    continue
                distribution = DISTRIBUTION_OF.get(imported, imported)
                distribution = distribution.lower().replace("_", "-")
                if distribution not in available:
                    undeclared.append(
                        f"{module.relative_to(REPO_ROOT)} imports {imported!r} "
                        f"(distribution {distribution!r}) but it is not in "
                        f"the {extra!r} extra or the base dependencies"
                    )

    assert not undeclared, "Undeclared imports in extra-gated packages:\n  " + "\n  ".join(
        undeclared
    )


def test_oauth_extra_declares_httpx() -> None:
    """The specific 0.8.6 regression, pinned by name."""
    assert "httpx" in _base_and_extra_distributions("oauth")
