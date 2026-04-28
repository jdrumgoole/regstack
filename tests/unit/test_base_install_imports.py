"""Regression test: ``import regstack`` must not require optional extras.

regstack 0.2.0 shipped to PyPI broken because several modules in the
hot import path (`routers/account.py`, `routers/login.py`,
`models/_objectid.py`, …) had unconditional ``from bson …`` /
``from pymongo …`` / ``from regstack.backends.mongo …`` statements.
On a base install (no ``mongo`` extra) ``import regstack`` raised
``ModuleNotFoundError: No module named 'bson'``.

This test runs ``import regstack`` in a subprocess with ``bson`` and
``pymongo`` blocked, so a future regression is caught even when the
test environment happens to have pymongo installed for other reasons.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_import_regstack_without_pymongo() -> None:
    """``import regstack`` and ``from regstack import RegStack, RegStackConfig``
    must work on a base install — no optional extras.

    We can't actually uninstall pymongo from the test venv, so we
    simulate "not installed" by inserting a finder that raises
    ImportError for ``bson`` and ``pymongo`` at the top of sys.meta_path.
    """
    program = textwrap.dedent(
        """
        import importlib.abc, importlib.machinery, sys

        BLOCKED = {"bson", "pymongo"}

        class BlockMongo(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                root = name.split(".", 1)[0]
                if root in BLOCKED:
                    raise ModuleNotFoundError(f"No module named {name!r}")
                return None

        sys.meta_path.insert(0, BlockMongo())
        # Drop anything pymongo-touching that may have been pre-imported
        # by pytest's collection of sibling test modules.
        for mod in list(sys.modules):
            root = mod.split(".", 1)[0]
            if root in BLOCKED or mod.startswith("regstack"):
                del sys.modules[mod]

        import regstack
        from regstack import RegStack, RegStackConfig

        assert RegStack.__module__ == "regstack.app"
        assert RegStackConfig.__module__ == "regstack.config.schema"
        print("ok", regstack.__version__)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"import regstack failed without pymongo:\nstdout={result.stdout!r}"
        f"\nstderr={result.stderr!r}"
    )
    assert result.stdout.startswith("ok "), result.stdout
