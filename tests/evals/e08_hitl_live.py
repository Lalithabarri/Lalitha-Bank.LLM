"""E08/E09 — official same-session human-in-the-loop run (Milestone 8). MANUAL, headed, no model.

Run as ``CUA_LIVE_HITL=1 uv run python -m tests.evals.e08_hitl_live --live [--evidence-root DIR]``
from a real terminal. What happens:

    real Legacy Bank on :8000 (policy/legacy_bank.json verbatim) · headed Chromium
    ReplayEngine runs transfer_funds@1.0.0 (M1001, 500.00)
    NAVIGATE form -> FILL amount -> CLICK "Review transfer"          (automation)
    CLICK "Confirm transfer" -> ActionGate: REQUIRE_INTERVENTION     (zero driver dispatch)
    PRE screenshot + digest -> AUTOMATION -> PENDING_HUMAN -> HUMAN
    >>> YOU click "Confirm transfer" in the visible browser window, then type `done` <<<
    HUMAN -> RETURNING -> fresh observation -> the step's postcondition (status "posted")
    POST screenshot + digest -> INTERVENTION_VERIFIED completed_by=HUMAN
    RETURNING -> AUTOMATION -> READ transaction reference -> SUCCESS

The audit is computed from the persisted events file (plus a few in-process facts the file
cannot carry: the surface object never changed, no model module was loaded). E09 (no repeat) is
the same run: zero ``ACTION_DISPATCHED`` for ``s4_confirm``. If you type ``done`` without
clicking, the expected result is ``FAILURE / UNKNOWN_COMMIT_STATE`` — the report says so.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from werkzeug.serving import make_server

from cua.artifact import ArtifactStore
from cua.evidence import EventType, EvidenceRecorder, EvidenceStore, RunKind
from cua.hitl import ControlOwner, StdinInterventionHandler
from cua.policy import ActionGate, PolicyConfig
from cua.replay import (
    CompletedBy,
    MonotonicClock,
    ReplayConfig,
    ReplayDeps,
    ReplayEngine,
    RunResult,
    TerminalStatus,
)
from cua.surface.playwright_surface import PlaywrightSurface
from legacy_bank import create_app
from tests.replay.support import policy_for

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ID = "transfer_funds@1.0.0"
MEMBER = "M1001"
AMOUNT = "500.00"
DEFAULT_PORT = 8000
STEP = [EventType.GATE_DECISION, EventType.ACTION_DISPATCHED, EventType.ACTION_COMPLETED]
EXPECTED_CHRONOLOGY = [
    EventType.RUN_STARTED,
    *STEP * 3,
    EventType.GATE_DECISION,
    EventType.INTERVENTION_REQUESTED,
    EventType.CONTROL_TRANSFERRED,
    EventType.CONTROL_TRANSFERRED,
    EventType.HAND_BACK,
    EventType.CONTROL_TRANSFERRED,
    EventType.INTERVENTION_VERIFIED,
    EventType.CONTROL_TRANSFERRED,
    *STEP,
    EventType.RUN_COMPLETED,
]
_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _opted_in() -> bool:
    return os.environ.get("CUA_LIVE_HITL") == "1"


@dataclass
class LiveRun:
    result: RunResult
    events_path: Path
    session_id: str
    surface_open_count: int
    headless: bool
    policy_source: str
    forbidden_loaded: list[str]


def run_live(evidence_root: Path, *, port: int = DEFAULT_PORT) -> LiveRun:
    app = create_app(fault_mode=None)
    try:
        server = make_server("127.0.0.1", port, app, threaded=True)
        policy = PolicyConfig.from_json_file(ROOT / "policy" / "legacy_bank.json")
        policy_source = "policy/legacy_bank.json (verbatim)"
    except OSError:
        server = make_server("127.0.0.1", 0, app, threaded=True)
        policy = policy_for(f"http://127.0.0.1:{server.server_port}")
        policy_source = "policy/legacy_bank.json rules re-homed to an ephemeral port"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    store = EvidenceStore(evidence_root)
    recorder = EvidenceRecorder(store.open_run)
    owner = ControlOwner()
    gate = ActionGate(policy, owner, observer=recorder)
    surface = PlaywrightSurface(headless=False, listener=recorder).open()
    session_id = surface.session_id
    engine = ReplayEngine(
        ReplayDeps(
            surface=surface,
            action_gate=gate,
            clock=MonotonicClock(),
            evidence=recorder,
            control_owner=owner,
            intervention=StdinInterventionHandler(),
        ),
        ReplayConfig(base_url=base_url, condition_timeout_s=15.0),
    )
    artifact = ArtifactStore(ROOT / "capabilities").load_id(ARTIFACT_ID)
    try:
        result = engine.run(artifact, {"member_id": MEMBER, "amount": AMOUNT})
        same_session = surface.session_id == session_id
    finally:
        surface.close()
        server.shutdown()
    forbidden = sorted(
        m for m in sys.modules if m.startswith(("openai", "google", "cua.llm", "cua.discovery"))
    )
    return LiveRun(
        result=result,
        events_path=store.events_path(RunKind.REPLAY, result.run_id),
        session_id=session_id if same_session else f"CHANGED:{surface.session_id}",
        surface_open_count=1,
        headless=False,
        policy_source=policy_source,
        forbidden_loaded=forbidden,
    )


def audit_events(events_path: Path) -> dict:
    """Everything derivable from the committed run directory alone (re-run offline by pytest)."""
    store_root = events_path.parents[2]
    run_dir = events_path.parent
    events = EvidenceStore(store_root).read_events(events_path)
    raw = events_path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw.splitlines()]
    types = [e.event_type for e in events]
    by_type = {t: [e for e in events if e.event_type is t] for t in EventType}
    verified = by_type[EventType.INTERVENTION_VERIFIED]
    requested = by_type[EventType.INTERVENTION_REQUESTED]
    transfers = [
        (e.payload.from_owner, e.payload.to_owner) for e in by_type[EventType.CONTROL_TRANSFERRED]
    ]
    terminal = events[-1].payload
    s4_gate = [e for e in by_type[EventType.GATE_DECISION] if e.step_id == "s4_confirm"]

    def shot_ok(ref) -> bool:
        if ref is None:
            return False
        path = run_dir / ref.path
        if not path.exists() or ref.path.startswith("/") or ".." in ref.path:
            return False
        data = path.read_bytes()
        return (
            data.startswith(_PNG_MAGIC)
            and hashlib.sha256(data).hexdigest() == ref.sha256
            and len(data) == ref.size_bytes
            and ref.media_type == "image/png"
        )

    v = verified[0].payload if verified else None
    must_pass = {
        "single_run_and_session": len({e.run_id for e in events}) == 1
        and len({e.session_id for e in events}) == 1,
        "chronology_exact": types == EXPECTED_CHRONOLOGY,
        "seq_contiguous": [e.seq for e in events] == list(range(1, len(events) + 1)),
        "s4_gate_require_intervention_once": [e.payload.decision for e in s4_gate]
        == ["REQUIRE_INTERVENTION"],
        "zero_dispatch_for_confirm": not any(
            e.step_id == "s4_confirm" for e in by_type[EventType.ACTION_DISPATCHED]
        ),
        "dispatched_steps_skip_confirm": [e.step_id for e in by_type[EventType.ACTION_DISPATCHED]]
        == ["s1_open_transfer", "s2_enter_amount", "s3_review", "s5_read_reference"],
        "ownership_transitions_in_order": transfers
        == [
            ("AUTOMATION", "PENDING_HUMAN"),
            ("PENDING_HUMAN", "HUMAN"),
            ("HUMAN", "RETURNING"),
            ("RETURNING", "AUTOMATION"),
        ],
        "restore_only_after_verification": bool(verified)
        and by_type[EventType.CONTROL_TRANSFERRED][-1].seq > verified[0].seq,
        "verified_completed_by_human": v is not None
        and v.outcome == "VERIFIED_COMPLETED"
        and v.completed_by == "HUMAN"
        and v.observations >= 1,
        "hand_back_was_done": bool(by_type[EventType.HAND_BACK])
        and by_type[EventType.HAND_BACK][0].payload.hand_back == "DONE",
        "step_record_confirm_human_not_dispatched": any(
            s.step_id == "s4_confirm"
            and s.completed_by == "HUMAN"
            and s.dispatched is False
            and s.status == "COMPLETED"
            for s in getattr(terminal, "steps", [])
        ),
        "run_completed_success_with_reference": terminal.kind == EventType.RUN_COMPLETED
        and terminal.status == "SUCCESS"
        and re.fullmatch(r"TXN-\d{6}", str(terminal.outputs.get("transfer_reference", "")))
        is not None,
        "pre_and_post_screenshots_stored_and_hashed": bool(requested)
        and shot_ok(requested[0].payload.pre_screenshot)
        and v is not None
        and shot_ok(v.pre_screenshot)
        and shot_ok(v.post_screenshot)
        and v.pre_screenshot.sha256 != v.post_screenshot.sha256,
        "post_digest_differs_from_pre": v is not None
        and v.pre_observation_digest != v.post_observation_digest,
        "no_bytes_or_absolute_paths_in_jsonl": "\\u0089" not in raw
        and "/Users/" not in raw
        and "/home/" not in raw
        and "/tmp/" not in raw,
        "runtime_inputs_absent": MEMBER not in raw
        and AMOUNT not in raw
        and "<input:member_id>" in raw,
        "no_ref_token_in_evidence": '"ref"' not in raw and not _REF_TOKEN.search(raw),
        "every_line_redacted_schema_1_2": all(
            ln["redaction_applied"] is True and ln["schema_version"] == "1.2" for ln in lines
        ),
        "no_model_events": not by_type[EventType.MODEL_CALL] and not by_type[EventType.OBSERVATION],
    }
    return {"must_pass": must_pass, "all_must_pass": all(must_pass.values())}


def audit(run: LiveRun) -> dict:
    report = audit_events(run.events_path)
    result = run.result
    in_process = {
        "same_surface_session_throughout": not run.session_id.startswith("CHANGED"),
        "surface_opened_once": run.surface_open_count == 1,
        "headed_browser": run.headless is False,
        "no_model_or_provider_module_loaded": run.forbidden_loaded == [],
        "result_success_decimal_free_reference": result.status is TerminalStatus.SUCCESS
        and set(result.outputs) == {"transfer_reference"},
        "result_confirm_completed_by_human": any(
            s.step_id == "s4_confirm" and s.completed_by is CompletedBy.HUMAN and not s.dispatched
            for s in result.steps
        ),
    }
    report["must_pass"].update(in_process)
    report["all_must_pass"] = all(report["must_pass"].values())
    report["recorded"] = {
        "run_id": result.run_id,
        "session_id": run.session_id,
        "status": result.status.value,
        "failure_code": result.failure.code.value if result.failure else None,
        "safe_to_retry": result.failure.safe_to_retry if result.failure else None,
        "outputs": {k: str(v) for k, v in result.outputs.items()},
        "evidence_path": str(run.events_path),
        "policy": run.policy_source,
    }
    if result.failure and result.failure.code.value == "UNKNOWN_COMMIT_STATE":
        report["note"] = (
            "the completion signal was not observed after hand-back: expected behaviour when "
            "the human did not click Confirm — never retried"
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--evidence-root", default=None)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if not _opted_in():
        print("refusing: set CUA_LIVE_HITL=1 to run the headed human-in-the-loop eval")
        return 2
    if not args.live:
        parser.error("pass --live")
    if not sys.stdin.isatty():
        print("refusing: the hand-back needs a real terminal (stdin is not a TTY)")
        return 2
    root = Path(args.evidence_root) if args.evidence_root else Path(tempfile.mkdtemp())
    report = audit(run_live(root, port=args.port))
    print("E08/E09 REPORT", json.dumps(report, indent=2, default=str))
    return 0 if report["all_must_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
