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

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "acessilia_toolbox"
#: The SDK and the core's top-level packages.
FORBIDDEN = frozenset({"acessilia_core", "acessilia_sdk", "backend", "frontend"})
ADR = (
    "https://github.com/A11yDevs/acessilia/blob/develop/docs/adr/0002-component-responsibilities.md"
)


def _dynamic_import_target(node: ast.Call, aliases: set[str]) -> str | None:
    """Recognized import calls with literal positional or ``name=`` arguments."""
    func = node.func
    name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if (
        isinstance(func, ast.Call)
        and isinstance(func.func, ast.Name)
        and func.func.id == "getattr"
        and len(func.args) == 2
        and isinstance(func.args[1], ast.Constant)
        and func.args[1].value in {"__import__", "import_module"}
    ):
        name = func.args[1].value
    if name not in {"__import__", "import_module"} | aliases:
        return None
    first = (
        node.args[0]
        if node.args
        else next((keyword.value for keyword in node.keywords if keyword.arg == "name"), None)
    )
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


def _imported_roots(path: Path) -> set[str]:
    roots: set[str] = set()
    # utf-8-sig: some modules start with a BOM, which Python accepts on import.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    aliases = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and not node.level and node.module == "importlib"
        for alias in node.names
        if alias.name == "import_module"
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            roots.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call) and (target := _dynamic_import_target(node, aliases)):
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


@pytest.mark.parametrize("root", ["acessilia_core", "acessilia_sdk", "backend", "frontend"])
@pytest.mark.parametrize(
    "source",
    [
        "import {root}.manifest.models",
        "from {root}.manifest import ProcessingManifest",
        "def load():\n    from {root}.manifest import ProcessingManifest",
        "__import__('{root}.manifest')",
        "import importlib\nimportlib.import_module('{root}.manifest')",
        "from importlib import import_module\nimport_module('{root}.manifest')",
        "from importlib import import_module as load\nload('{root}.manifest')",
        "import importlib as loader\nloader.import_module('{root}.manifest')",
        "from importlib import import_module\nimport_module(name='{root}.manifest')",
        "__import__(name='{root}.manifest')",
        "import importlib\ngetattr(importlib, 'import_module')('{root}.manifest')",
        "import importlib as loader\ngetattr(loader, 'import_module')(name='{root}.manifest')",
        "def load():\n    from importlib import import_module as find\n"
        "    return find(name='{root}.manifest')",
    ],
)
def test_forbidden_import_forms_are_detected(tmp_path: Path, root: str, source: str) -> None:
    module = tmp_path / "synthetic.py"
    module.write_text(source.format(root=root), encoding="utf-8")
    assert _imported_roots(module) & FORBIDDEN == {root}


@pytest.mark.parametrize(
    "source",
    [
        "from docstruct.text.norm_fix import norm_fix",
        "from .acessilia_core import local_name",
        "description = 'import acessilia_core'",
    ],
)
def test_allowed_code_is_not_a_forbidden_import(tmp_path: Path, source: str) -> None:
    module = tmp_path / "synthetic.py"
    module.write_text(source, encoding="utf-8")
    assert not (_imported_roots(module) & FORBIDDEN)
