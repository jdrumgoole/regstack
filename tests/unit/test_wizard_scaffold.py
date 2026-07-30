"""Keep the wizard scaffold shared.

The three pywebview wizards used to carry their own copy of the same
server/window/thread machinery. That wasn't only repetition: the
`asyncio.Event` shutdown bug fixed in 0.9.1 was a single defect that
shipped three times, once per copy. `_scaffold.py` now owns the
mechanics.

Nothing stops a fourth wizard — or a well-meaning edit to an existing one
— from quietly reintroducing a local copy, and the duplication would look
harmless right up until the next shared bug. These tests fail when a
wizard re-implements machinery that belongs to the scaffold.

They're AST-based because the point is *where the code lives*, which is a
structural property. `window.py` is excluded from coverage (no headless
path through a native GUI), so structure is also the only thing testable
about it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

WIZARDS = ["oauth_google", "theme_designer", "ses"]
_WIZARD_DIR = pathlib.Path(__file__).resolve().parents[2] / "src" / "regstack" / "wizard"

# Machinery that must have exactly one definition, in the scaffold.
_SCAFFOLD_ONLY_FUNCTIONS = frozenset(
    {
        "find_free_port",
        "serve",
        "assemble_server",
        "open_window",
        "run_windowed",
        "_watch_shutdown",
    }
)


def _defined_functions(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _calls(path: pathlib.Path) -> list[str]:
    """Dotted names of every call in the module, best-effort."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name):
            out.append(f.id)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
            out.append(f"{f.value.id}.{f.attr}")
    return out


@pytest.mark.parametrize("wizard", WIZARDS)
@pytest.mark.parametrize("module", ["server.py", "window.py", "cli.py"])
def test_wizard_does_not_redefine_scaffold_machinery(wizard: str, module: str) -> None:
    """A wizard must import the shared mechanics, not re-implement them."""
    path = _WIZARD_DIR / wizard / module
    redefined = _defined_functions(path) & _SCAFFOLD_ONLY_FUNCTIONS
    assert not redefined, (
        f"{wizard}/{module} defines {sorted(redefined)}, which belongs to "
        "regstack.wizard._scaffold. Import it instead — a local copy is how "
        "one shutdown bug became three before 0.9.1."
    )


@pytest.mark.parametrize("wizard", WIZARDS)
def test_wizard_cli_does_not_spawn_its_own_server_thread(wizard: str) -> None:
    """The thread/window/teardown dance belongs to `run_windowed`.

    Each CLI used to build its own `threading.Thread`, and each got the
    teardown subtly wrong in its own way — one carried a vestigial
    `asyncio.Event` that was set but never awaited.
    """
    path = _WIZARD_DIR / wizard / "cli.py"
    calls = _calls(path)
    assert "threading.Thread" not in calls, (
        f"{wizard}/cli.py builds its own server thread; call "
        "regstack.wizard._scaffold.run_windowed instead"
    )
    assert "run_windowed" in calls, f"{wizard}/cli.py should drive the GUI via run_windowed"


@pytest.mark.parametrize("wizard", WIZARDS)
def test_wizard_window_error_derives_from_the_shared_base(wizard: str) -> None:
    """Each wizard keeps its own error type — deliberately, so its CLI can
    catch a specific one — but it must derive from the shared base so
    callers can catch either."""
    from regstack.wizard._scaffold import WizardWindowError as Base

    mod = __import__(f"regstack.wizard.{wizard}.window", fromlist=["*"])
    errors = [
        obj
        for name, obj in vars(mod).items()
        if isinstance(obj, type) and issubclass(obj, BaseException) and not name.startswith("_")
    ]
    own = [e for e in errors if e is not Base]
    assert own, f"{wizard}/window.py defines no window error type"
    for err in own:
        assert issubclass(err, Base), f"{err.__name__} does not derive from the shared base"


def test_scaffold_owns_the_machinery() -> None:
    """The flip side: the scaffold must actually define what the wizards
    are forbidden from defining, so this suite can't pass vacuously by
    everyone having deleted it."""
    defined = _defined_functions(_WIZARD_DIR / "_scaffold.py")
    missing = _SCAFFOLD_ONLY_FUNCTIONS - defined - {"_watch_shutdown"}
    assert not missing, f"_scaffold.py is missing {sorted(missing)}"
    # _watch_shutdown is nested inside open_window, so check the source.
    assert "_watch_shutdown" in (_WIZARD_DIR / "_scaffold.py").read_text(encoding="utf-8")


def test_scaffold_watcher_does_not_touch_asyncio() -> None:
    """The 0.9.1 crash, guarded at its new home.

    `run_windowed` legitimately calls `asyncio.run` — it owns the server
    loop in its own thread. What must never happen again is the *watcher*
    reaching for a loop: it has to block on the `threading.Event`
    directly, or it recreates `RuntimeError: ... is bound to a different
    event loop`.

    Inspected via AST rather than by scanning the source text, so the
    comment in that function explaining the original bug doesn't trip it.
    """
    tree = ast.parse((_WIZARD_DIR / "_scaffold.py").read_text(encoding="utf-8"))
    watchers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_watch_shutdown"
    ]
    assert watchers, "_scaffold.py no longer defines the shutdown watcher"
    for watcher in watchers:
        touches = [
            f"line {n.lineno}"
            for n in ast.walk(watcher)
            if isinstance(n, ast.Name) and n.id == "asyncio"
        ]
        assert not touches, (
            f"the shutdown watcher references asyncio at {touches} — it must "
            "block on the threading.Event directly"
        )
