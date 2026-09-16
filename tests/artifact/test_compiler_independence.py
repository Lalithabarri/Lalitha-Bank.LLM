"""The compiler's output is produced from the persisted E01 record alone — never by loading,
copying or consulting the handwritten Milestone 4 artifact, the filesystem, or a model."""

import ast
import builtins
import json
import shutil
import subprocess
import sys
from pathlib import Path

import cua
from cua.artifact import ArtifactStore, ProvenanceSource
from cua.artifact.compile_report import CompileSuccess
from cua.discovery.capabilities import READ_SAVINGS_BALANCE_DECLARATION as DECLARATION
from tests.artifact.compiler_support import (
    OFFICIAL_E01_RUN_ID,
    ROOT,
    compile_official,
    official_run,
)

CUA_ROOT = Path(cua.__file__).parent
COMPILER_FILES = ("artifact/compiler.py", "artifact/compile_report.py", "artifact/declaration.py")
HANDWRITTEN = ArtifactStore(ROOT / "capabilities").load_id("read_savings_balance@1.0.0")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _string_constants(tree: ast.AST) -> list[str]:
    """Every string literal in the code, docstrings excluded (prose may name evidence files)."""
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_compiler_sources_do_no_io_and_know_no_store_member_or_balance():
    for relative in COMPILER_FILES:
        path = CUA_ROOT / relative
        imported = _imports(path)
        for forbidden in ("cua.artifact.store", "pathlib", "json", "os", "io", "shutil"):
            assert forbidden not in imported, (relative, forbidden)
        assert not any(
            m.startswith(("cua.discovery", "cua.llm", "openai", "google", "playwright"))
            for m in imported
        ), relative
        tree = ast.parse(path.read_text())
        calls = {
            node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        assert not calls & {"open", "read_text", "read_bytes", "write_text", "load", "loads"}, (
            relative,
            calls,
        )
        for literal in ("capabilities", ".json", "M1001", "M1002", "M404", "15275", "4120"):
            assert not any(literal in s for s in _string_constants(tree)), (relative, literal)


def test_compiles_with_the_filesystem_and_the_handwritten_artifact_unavailable(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the compiler must not touch the filesystem")

    run = official_run()  # read before the patch
    monkeypatch.setattr(builtins, "open", refuse)
    monkeypatch.setattr(Path, "read_text", refuse)
    monkeypatch.setattr(Path, "read_bytes", refuse)
    monkeypatch.setattr(ArtifactStore, "load_id", refuse)
    monkeypatch.setattr(ArtifactStore, "load", refuse)
    result = compile_official(DECLARATION, run)
    assert isinstance(result, CompileSuccess)


def test_compiles_in_a_fresh_interpreter_from_the_evidence_file_alone(tmp_path):
    """Only the persisted record travels: a scratch cwd with the E01 events.jsonl, no
    ``capabilities/`` in reach, provider SDKs and the discovery agent forbidden."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    shutil.copy(
        ROOT / "evidence" / "discovery" / OFFICIAL_E01_RUN_ID / "events.jsonl",
        scratch / "events.jsonl",
    )
    code = """
import importlib.abc, sys, json
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(("openai", "google", "playwright", "cua.llm.openai_client")):
            raise ImportError(fullname)
sys.meta_path.insert(0, Guard())
from datetime import UTC, datetime
from pathlib import Path
from cua.evidence import EvidenceStore
from cua.artifact.compiler import ArtifactCompiler
from cua.discovery.capabilities import READ_SAVINGS_BALANCE_DECLARATION
events = EvidenceStore.read_events(Path("events.jsonl"))
result = ArtifactCompiler().compile(events[-1].payload, READ_SAVINGS_BALANCE_DECLARATION,
    capability_version="1.0.0", compiled_at=datetime(2026, 9, 16, tzinfo=UTC))
print(json.dumps({"ok": type(result).__name__, "run": result.artifact.provenance.discovery_run_id,
    "loaded": sorted(m for m in sys.modules if m.startswith(("openai", "google", "playwright")))}))
"""
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=scratch,
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
    )
    report = json.loads(out.stdout.strip().splitlines()[-1])
    assert report == {"ok": "CompileSuccess", "run": OFFICIAL_E01_RUN_ID, "loaded": []}


def test_generated_artifact_is_not_the_handwritten_one():
    result = compile_official(DECLARATION)
    assert isinstance(result, CompileSuccess)
    generated = result.artifact
    assert generated != HANDWRITTEN
    assert HANDWRITTEN.provenance.source is ProvenanceSource.HANDWRITTEN
    assert generated.provenance.source is ProvenanceSource.DISCOVERY
    assert generated.provenance.discovery_run_id == OFFICIAL_E01_RUN_ID
    # structural differences a reviewer can see: one verified strategy per target (the
    # handwritten FILL carries a fallback), scoped search controls, a route-only checkpoint
    assert len(HANDWRITTEN.steps[1].target.strategies) == 2
    assert len(generated.steps[1].target.strategies) == 1
    assert generated.steps[1].target.strategies[0].scope == "group: Look up member"
    assert HANDWRITTEN.steps[2].target.strategies[0].scope is None
    assert len(HANDWRITTEN.success_checkpoint) == 2 and len(generated.success_checkpoint) == 1
    assert [s.step_id for s in generated.steps] != [s.step_id for s in HANDWRITTEN.steps]
    # and what is legitimately shared is the declared contract and the observed UI semantics
    assert generated.inputs == HANDWRITTEN.inputs and generated.outputs == HANDWRITTEN.outputs
    assert generated.steps[3].target == HANDWRITTEN.steps[3].target
