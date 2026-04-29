"""Regression test: ``import regstack`` must not require optional extras.

regstack 0.2.0 shipped to PyPI broken because several modules in the
hot import path (`routers/account.py`, `routers/login.py`,
`models/_objectid.py`, …) had unconditional ``from bson …`` /
``from pymongo …`` / ``from regstack.backends.mongo …`` statements.
On a base install (no ``mongo`` extra) ``import regstack`` raised
``ModuleNotFoundError: No module named 'bson'``.

This test runs ``import regstack`` in a subprocess with the optional
extras' module roots blocked (``bson`` / ``pymongo`` from the
``mongo`` extra, ``cryptography`` from the ``oauth`` extra), so a
future regression is caught even when the test environment happens
to have those packages installed for other reasons.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap


def test_import_regstack_without_optional_extras() -> None:
    """``import regstack`` and ``from regstack import RegStack, RegStackConfig``
    must work on a base install — no optional extras.

    We can't actually uninstall the extras from the test venv, so we
    simulate "not installed" by inserting a finder that raises
    ImportError for the extras' top-level packages at the top of
    sys.meta_path.
    """
    program = textwrap.dedent(
        """
        import importlib.abc, importlib.machinery, sys

        BLOCKED = {
            # mongo extra
            "bson", "pymongo",
            # oauth extra — pyjwt's crypto path imports cryptography lazily
            # but a regression that pulls oauth/* into the hot path would
            # surface here.
            "cryptography",
        }

        class BlockExtras(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path, target=None):
                root = name.split(".", 1)[0]
                if root in BLOCKED:
                    raise ModuleNotFoundError(f"No module named {name!r}")
                return None

        sys.meta_path.insert(0, BlockExtras())
        # Drop anything that may have been pre-imported by pytest's
        # collection of sibling test modules.
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
        f"import regstack failed without optional extras:\nstdout={result.stdout!r}"
        f"\nstderr={result.stderr!r}"
    )
    assert result.stdout.startswith("ok "), result.stdout
