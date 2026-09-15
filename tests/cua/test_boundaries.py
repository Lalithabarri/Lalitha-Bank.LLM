"""Milestone 1 slice of the structural boundary checks (full suite: scripts/verify.sh, step 18)."""

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

FORBIDDEN_ANYWHERE_IN_CUA = {"legacy_bank", "flask", "playwright", "google"}


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


def test_cua_never_imports_target_app_or_drivers():
    offenders = {}
    for path in CUA_ROOT.rglob("*.py"):
        hit = _imported_roots(path) & FORBIDDEN_ANYWHERE_IN_CUA
        if hit:
            offenders[str(path.relative_to(CUA_ROOT))] = hit
    assert not offenders, offenders
