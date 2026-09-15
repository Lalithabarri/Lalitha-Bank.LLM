"""Structural boundary checks (the fresh-clone suite lands in scripts/verify.sh, step 18).

Milestone 1: every ARCHITECTURE §3 package exists; cua never imports the target app.
Milestone 2: Playwright is confined to one file; the public contract carries no driver type.
"""

import ast
import importlib
from pathlib import Path

import pytest

import cua

CUA_ROOT = Path(cua.__file__).parent

ARCHITECTURE_MODULES = [
    "surface",
    "llm",
    "discovery",
    "artifact",
    "replay",
    "policy",
    "hitl",
    "evidence",
    "console",
    "runner",
    "cli",
    "domain",
]

# Never importable from anywhere in cua. ``google`` covers the Gemini SDK until step 10 gives
# llm/ its own, narrower rule.
FORBIDDEN_ANYWHERE_IN_CUA = {"legacy_bank", "flask", "google"}

# The one file allowed to import the browser driver (ARCHITECTURE §4, D05).
PLAYWRIGHT_ALLOWED = {"surface/playwright_surface.py"}

# Driver-neutral modules whose *annotations* must not name a Playwright type either.
DRIVER_NEUTRAL_MODULES = [
    "surface/contract.py",
    "surface/query.py",
    "surface/aria.py",
    "domain/__init__.py",
    "domain/base.py",
    "domain/actions.py",
    "domain/surface.py",
    "domain/ids.py",
]

PLAYWRIGHT_TYPE_NAMES = {
    "Page",
    "Locator",
    "ElementHandle",
    "Frame",
    "Browser",
    "BrowserContext",
    "Playwright",
    "playwright",
}


@pytest.mark.parametrize("name", ARCHITECTURE_MODULES)
def test_architecture_package_exists_and_imports(name):
    module = importlib.import_module(f"cua.{name}")
    assert module.__doc__, f"cua.{name} must carry its ownership docstring"


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _cua_files() -> list[Path]:
    return sorted(CUA_ROOT.rglob("*.py"))


def test_cua_never_imports_target_app_or_forbidden_sdks():
    offenders = {}
    for path in _cua_files():
        hit = _imported_roots(path) & FORBIDDEN_ANYWHERE_IN_CUA
        if hit:
            offenders[str(path.relative_to(CUA_ROOT))] = hit
    assert not offenders, offenders


def test_playwright_is_imported_only_by_the_surface_implementation():
    importers = {
        str(path.relative_to(CUA_ROOT))
        for path in _cua_files()
        if "playwright" in _imported_roots(path)
    }
    assert importers == PLAYWRIGHT_ALLOWED, importers


def _annotation_names(path: Path) -> set[str]:
    """Every dotted-name / name appearing inside a type annotation or a string annotation."""
    tree = ast.parse(path.read_text())
    names: set[str] = set()

    def collect(node):
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                collect(ast.parse(node.value, mode="eval").body)
            except SyntaxError:
                pass
            return
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                names.add(sub.attr)

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            collect(node.annotation)
        elif isinstance(node, ast.arg):
            collect(node.annotation)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            collect(node.returns)
    return names


@pytest.mark.parametrize("relative", DRIVER_NEUTRAL_MODULES)
def test_driver_neutral_modules_have_no_playwright_types_in_annotations(relative):
    path = CUA_ROOT / relative
    assert path.exists(), relative
    assert "playwright" not in _imported_roots(path)
    leaked = _annotation_names(path) & PLAYWRIGHT_TYPE_NAMES
    assert not leaked, (relative, leaked)


def test_surface_package_public_api_is_the_contract_only():
    import cua.surface

    assert "PlaywrightSurface" not in cua.surface.__all__
    assert not hasattr(cua.surface, "PlaywrightSurface")
    assert {"Surface", "SurfaceAction", "ActResult", "find"} <= set(cua.surface.__all__)


def test_legacy_bank_never_imports_cua():
    import legacy_bank

    root = Path(legacy_bank.__file__).parent
    offenders = [
        str(path.relative_to(root)) for path in root.rglob("*.py") if "cua" in _imported_roots(path)
    ]
    assert not offenders, offenders
