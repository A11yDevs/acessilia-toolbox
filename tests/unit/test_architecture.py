"""The Toolbox depends on docstruct only, never on the SDK or the core.

ADR-0002 (A11yDevs/acessilia) fixes the dependency direction: the core and
the SDK reach the Toolbox through its published HTTP contract, and the
Toolbox's only ecosystem dependency is docstruct. Importing ``acessilia_sdk``
or the core's packages here would create a cycle, so this test reads every
module of ``acessilia_toolbox`` and fails on such an import, static or a
literal ``__import__``/``importlib.import_module`` call.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "acessilia_toolbox"
#: The SDK and the core's top-level packages.
FORBIDDEN = frozenset({"acessilia_sdk", "backend", "frontend"})
ADR = (
    "https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/0002-component-responsibilities.md"
)


def _dynamic_import_target(node: ast.Call) -> str | None:
    """The literal module name of ``__import__(...)`` or ``import_module(...)``."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if name not in {"__import__", "import_module"} or not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    # utf-8-sig: some modules start with a BOM, which Python accepts on import.
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and (target := _dynamic_import_target(node)):
            roots.add(target.split(".")[0])
    return roots


def test_the_toolbox_imports_neither_the_sdk_nor_the_core() -> None:
    modules = sorted(SRC.rglob("*.py"))
    assert modules, f"no module found under {SRC}"
    offenders = [
        f"{path.relative_to(SRC.parent)}: {', '.join(sorted(found))}"
        for path in modules
        if (found := _imported_roots(path) & FORBIDDEN)
    ]
    assert not offenders, (
        f"the Toolbox must depend on docstruct only (ADR-0002, see {ADR}): " + "; ".join(offenders)
    )
