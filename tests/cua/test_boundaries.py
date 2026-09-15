"""Structural boundary checks (the fresh-clone suite lands in scripts/verify.sh, step 18).

Milestone 1: every ARCHITECTURE §3 package exists; cua never imports the target app.
Milestone 2: Playwright is confined to one file; the public contract carries no driver type.
Milestone 3: policy/artifact/hitl are driver-free; ActionGate is the only caller of Surface.act.
Milestone 4: replay/ is driver-free and LLM-free; ReplayDeps has no slot for a model.
Milestone 5: evidence/ is driver-free and LLM-free; disk writes happen only in the artifact store
and the evidence writer; no evidence payload can hold a ref.
Milestone 6: the OpenAI SDK is confined to one adapter under llm/; discovery/ never calls
Surface.act and never catches broadly; importing any deterministic layer — or cua.discovery /
cua.llm themselves — loads no provider SDK; DiscoveryDeps is the only deps type with a model slot.
"""

import ast
import importlib
import importlib.util
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

# Never importable from anywhere in cua. ``google`` (the Gemini SDK, D06) stays forbidden: the
# implemented V1 provider is OpenAI (D24), confined to one adapter by OPENAI_ALLOWED below.
FORBIDDEN_ANYWHERE_IN_CUA = {"legacy_bank", "flask", "google"}

# The one file allowed to import the browser driver (ARCHITECTURE §4, D05).
PLAYWRIGHT_ALLOWED = {"surface/playwright_surface.py"}

# The one file allowed to import the OpenAI SDK (ARCHITECTURE §4, D24).
OPENAI_ALLOWED = {"llm/openai_client.py"}

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
    "policy/__init__.py",
    "policy/routes.py",
    "policy/risk.py",
    "policy/config.py",
    "policy/action_gate.py",
    "hitl/__init__.py",
    "hitl/control.py",
    "artifact/__init__.py",
    "artifact/schema.py",
    "artifact/store.py",
    "replay/__init__.py",
    "replay/binding.py",
    "replay/clock.py",
    "replay/conditions.py",
    "replay/engine.py",
    "replay/matching.py",
    "replay/resolver.py",
    "replay/result.py",
    "replay/transforms.py",
    "replay/summaries.py",
    "evidence/__init__.py",
    "evidence/events.py",
    "evidence/redaction.py",
    "evidence/writer.py",
    "evidence/recorder.py",
    "llm/__init__.py",
    "llm/contract.py",
    "llm/prompt.py",
    "llm/openai_client.py",
    "discovery/__init__.py",
    "discovery/agent.py",
    "discovery/goal.py",
    "discovery/observation.py",
    "discovery/result.py",
    "discovery/summaries.py",
    "discovery/templating.py",
    "discovery/trace.py",
    "discovery/validator.py",
]

# The only files in cua that may write to disk (ARCHITECTURE §10: no ad-hoc writes; D19).
DISK_WRITERS_ALLOWED = {"artifact/store.py", "evidence/writer.py"}

# The only production call site of Surface.act() in cua (ARCHITECTURE §8, D09). The surface
# package defines act(); everything else must go through the gate.
ACT_CALL_SITES_ALLOWED = {"policy/action_gate.py"}

# Modules that must stay free of the LLM layer (ARCHITECTURE §4: llm/ reachable only from
# discovery/). Checked transitively over cua-internal imports.
LLM_FREE_ROOTS = [
    "cua.policy",
    "cua.artifact",
    "cua.hitl",
    "cua.surface",
    "cua.domain",
    "cua.replay",
    "cua.evidence",
]
LLM_MODULE_PREFIXES = ("cua.llm", "google", "openai", "cua.discovery")
SDK_PREFIXES = ("google", "openai")

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


# --- Milestone 3 -------------------------------------------------------------------------------


def _act_call_sites(path: Path) -> list[int]:
    """Line numbers of every ``<something>.act(...)`` call in the file."""
    tree = ast.parse(path.read_text())
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "act"
    ]


def test_surface_act_is_called_only_from_the_action_gate():
    callers = {}
    for path in _cua_files():
        relative = str(path.relative_to(CUA_ROOT))
        if relative.startswith("surface/"):
            continue  # the surface package defines act(); it does not call it on itself
        lines = _act_call_sites(path)
        if lines:
            callers[relative] = lines
    assert set(callers) == ACT_CALL_SITES_ALLOWED, callers
    assert len(callers["policy/action_gate.py"]) == 1


def _cua_imports_of(module_name: str) -> set[str]:
    """Fully qualified cua-internal (and provider SDK) modules imported by ``module_name``."""
    spec = importlib.util.find_spec(module_name)
    assert spec and spec.origin, module_name
    tree = ast.parse(Path(spec.origin).read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return {m for m in found if m.startswith("cua") or m.startswith(SDK_PREFIXES)}


def _transitive_cua_imports(root: str) -> set[str]:
    seen: set[str] = set()
    stack = [root]
    while stack:
        module = stack.pop()
        if module in seen:
            continue
        seen.add(module)
        if module.startswith(SDK_PREFIXES):
            continue
        stack.extend(_cua_imports_of(module))
    return seen


@pytest.mark.parametrize("root", LLM_FREE_ROOTS)
def test_deterministic_layers_never_reach_the_llm_layer(root):
    reached = _transitive_cua_imports(root)
    leaked = {m for m in reached if m.startswith(LLM_MODULE_PREFIXES)}
    assert not leaked, (root, sorted(leaked))


def test_test_fakes_are_not_importable_from_src():
    for path in _cua_files():
        roots = _imported_roots(path)
        assert "tests" not in roots, str(path.relative_to(CUA_ROOT))


# --- Milestone 4 -------------------------------------------------------------------------------


def test_replay_deps_has_no_field_that_could_hold_a_model():
    from cua.replay import ReplayDeps

    fields = set(ReplayDeps.__dataclass_fields__)
    assert fields == {"surface", "action_gate", "clock", "evidence"}
    for name in fields:
        assert not any(k in name.lower() for k in ("llm", "model", "gemini", "client")), name


def test_replay_never_reaches_playwright_or_the_llm_layer_transitively():
    reached = _transitive_cua_imports("cua.replay")
    assert not {m for m in reached if m.startswith(LLM_MODULE_PREFIXES)}
    assert "cua.surface.playwright_surface" not in reached
    for module in reached:
        if module.startswith("cua"):
            spec = importlib.util.find_spec(module)
            assert spec and spec.origin
            assert "playwright" not in _imported_roots(Path(spec.origin)), module


def test_replay_engine_contains_no_act_call_and_no_broad_except():
    engine = CUA_ROOT / "replay" / "engine.py"
    assert _act_call_sites(engine) == []
    tree = ast.parse(engine.read_text())
    broad = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and (
            node.type is None
            or (isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"})
        )
    ]
    assert broad == [], f"broad except at lines {broad}"


def test_importing_replay_does_not_load_playwright_or_an_llm_sdk():
    import subprocess
    import sys

    code = (
        "import sys, cua.replay; "
        "print(sorted(m for m in sys.modules "
        "if m.startswith(('playwright', 'google', 'openai', 'cua.llm', 'cua.discovery'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --- Milestone 5 -------------------------------------------------------------------------------


def _disk_write_sites(path: Path) -> list[int]:
    """Lines calling ``open(...)``, ``.write_text(...)``, ``.write_bytes(...)`` or ``os.fsync``."""
    tree = ast.parse(path.read_text())
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "open":
            lines.append(node.lineno)
        elif isinstance(func, ast.Attribute) and func.attr in {
            "write_text",
            "write_bytes",
            "fsync",
        }:
            lines.append(node.lineno)
    return lines


def test_disk_writes_happen_only_in_the_artifact_store_and_the_evidence_writer():
    writers = {}
    for path in _cua_files():
        lines = _disk_write_sites(path)
        if lines:
            writers[str(path.relative_to(CUA_ROOT))] = lines
    assert set(writers) == DISK_WRITERS_ALLOWED, writers


def test_evidence_never_reaches_playwright_or_the_llm_layer_transitively():
    reached = _transitive_cua_imports("cua.evidence")
    assert not {m for m in reached if m.startswith(LLM_MODULE_PREFIXES)}
    assert "cua.surface.playwright_surface" not in reached
    assert "cua.replay" not in {
        m.split(".engine")[0] for m in reached
    }  # no cycle: replay -> evidence only
    for module in reached:
        if module.startswith("cua"):
            spec = importlib.util.find_spec(module)
            assert spec and spec.origin
            assert "playwright" not in _imported_roots(Path(spec.origin)), module


def test_importing_evidence_does_not_load_playwright_or_an_llm_sdk():
    import subprocess
    import sys

    code = (
        "import sys, cua.evidence; "
        "print(sorted(m for m in sys.modules "
        "if m.startswith(('playwright', 'google', 'openai', 'cua.llm', 'cua.replay'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_no_evidence_payload_model_can_hold_a_ref_or_a_snapshot():
    from cua.evidence import PAYLOAD_MODELS, EvidenceEvent

    forbidden = {"ref", "refs", "element", "elements", "snapshot", "locator", "page"}
    for model in (*PAYLOAD_MODELS, EvidenceEvent):
        assert not set(model.model_fields) & forbidden, model.__name__
    # and no evidence module even imports the snapshot type
    for name in ("events", "redaction", "writer"):
        tree = ast.parse((CUA_ROOT / "evidence" / f"{name}.py").read_text())
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert "SurfaceSnapshot" not in imported and "SurfaceElement" not in imported, name


def test_the_recorder_is_the_only_evidence_object_the_engine_gate_and_surface_share():
    """The Surface listener protocol and the gate observer protocol live below evidence
    (surface.contract / policy), so neither package imports evidence: no cycle."""
    for relative in (
        "surface/contract.py",
        "surface/playwright_surface.py",
        "policy/action_gate.py",
    ):
        imported = _imported_roots(CUA_ROOT / relative)
        tree = ast.parse((CUA_ROOT / relative).read_text())
        modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any(m.startswith("cua.evidence") for m in modules), (relative, modules)
        assert "cua" in imported


# --- Milestone 6 -------------------------------------------------------------------------------


def test_openai_is_imported_only_by_the_provider_adapter():
    importers = {
        str(path.relative_to(CUA_ROOT))
        for path in _cua_files()
        if "openai" in _imported_roots(path)
    }
    assert importers == OPENAI_ALLOWED, importers


def test_llm_package_init_never_imports_the_adapter():
    """``import cua.llm`` must stay SDK-free; a composition root imports the adapter explicitly."""
    modules = {
        node.module
        for node in ast.walk(ast.parse((CUA_ROOT / "llm" / "__init__.py").read_text()))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "cua.llm.openai_client" not in modules


def test_discovery_contains_no_act_call_and_no_broad_except():
    for name in ("agent", "validator", "observation", "goal", "summaries", "trace", "result"):
        path = CUA_ROOT / "discovery" / f"{name}.py"
        assert _act_call_sites(path) == [], name
        tree = ast.parse(path.read_text())
        broad = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler)
            and (
                node.type is None
                or (
                    isinstance(node.type, ast.Name)
                    and node.type.id in {"Exception", "BaseException"}
                )
            )
        ]
        assert broad == [], (name, broad)


def test_discovery_reaches_llm_but_only_pure_replay_modules():
    """discovery -> llm is the one permitted edge to the model layer; discovery may use replay's
    pure binding/transform helpers but never its engine, resolver or results."""
    reached = _transitive_cua_imports("cua.discovery")
    assert "cua.llm" in reached or "cua.llm.contract" in reached
    assert "cua.replay.engine" not in reached
    assert "cua.replay.resolver" not in reached
    assert "cua.replay.result" not in reached
    assert "cua.surface.playwright_surface" not in reached
    assert not {m for m in reached if m.startswith(SDK_PREFIXES)}


def test_llm_contract_and_prompt_reach_no_sdk_no_surface_no_discovery():
    """The contract reuses artifact value-binding types (D12) — and through them the policy risk
    vocabulary — but never the surface, the discovery loop, or a provider SDK."""
    for module in ("cua.llm", "cua.llm.contract", "cua.llm.prompt"):
        reached = _transitive_cua_imports(module)
        assert not {m for m in reached if m.startswith(("cua.discovery", "cua.surface"))}, module
        assert not {m for m in reached if m.startswith(SDK_PREFIXES)}, module


@pytest.mark.parametrize(
    "module",
    [
        "cua.replay",
        "cua.artifact",
        "cua.policy",
        "cua.surface.contract",
        "cua.evidence",
        "cua.discovery",
        "cua.llm",
    ],
)
def test_importing_a_layer_does_not_load_a_provider_sdk(module):
    import subprocess
    import sys

    code = (
        f"import sys, {module}; "
        "print(sorted(m for m in sys.modules if m.startswith(('openai', 'google'))))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]", (module, out.stdout)


def test_discovery_deps_is_the_only_deps_type_with_a_model_slot():
    from cua.discovery import DiscoveryDeps
    from cua.replay import ReplayDeps

    assert set(DiscoveryDeps.__dataclass_fields__) == {
        "surface",
        "action_gate",
        "clock",
        "evidence",
        "llm",
    }
    assert set(ReplayDeps.__dataclass_fields__) == {"surface", "action_gate", "clock", "evidence"}


def test_no_discovery_evidence_payload_can_hold_a_ref_or_a_snapshot():
    from cua.evidence import (
        DecisionSummary,
        DiscoveryEndedPayload,
        DiscoveryStartedPayload,
        ModelCallPayload,
        ObservationPayload,
        TraceStepSummary,
        TraceSummary,
    )

    forbidden = {
        "ref",
        "refs",
        "element",
        "elements",
        "snapshot",
        "locator",
        "page",
        "prompt",
        "completion",
        "response",
        "messages",
        "reasoning",
    }
    for model in (
        DiscoveryStartedPayload,
        ObservationPayload,
        ModelCallPayload,
        DecisionSummary,
        DiscoveryEndedPayload,
        TraceStepSummary,
        TraceSummary,
    ):
        assert not set(model.model_fields) & forbidden, model.__name__


def test_the_action_gate_is_still_the_only_production_caller_of_act_after_discovery():
    """Explicit restatement for the safety gate: discovery/ appears in no caller set."""
    callers = {
        str(path.relative_to(CUA_ROOT))
        for path in _cua_files()
        if not str(path.relative_to(CUA_ROOT)).startswith("surface/") and _act_call_sites(path)
    }
    assert callers == {"policy/action_gate.py"}
