"""Milestone 4 end-to-end proofs: the checked-in artifact, a real Chromium, the real Legacy Bank.

One artifact object for the whole module (never cloned or mutated per member). Every claim is
asserted from the RunResult and from what the surface actually received.

Milestone 5: every run is recorded through the production evidence wiring — the surface's
``DispatchListener``, the gate's ``GateObserver`` and the engine's ``ReplayDeps.evidence`` are one
``EvidenceRecorder`` writing real JSONL under ``tmp_path`` (``CUA_EVIDENCE_ROOT=<dir>`` writes it
there instead, the way ``CUA_FEASIBILITY_DUMP`` did for Milestone 2).
"""

import json
import os
import re
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from cua.artifact import ArtifactStore
from cua.domain import ActionType
from cua.evidence import EventType, EvidenceRecorder, EvidenceStore, RunKind
from cua.policy import DenyReason, GateDecision, PolicyConfig, RiskTier
from cua.replay import CompletedBy, FailureCode, MonotonicClock, StepStatus, TerminalStatus
from tests.replay.recording_surface import RecordingSurface
from tests.replay.support import ALL_ACTIONS, engine_for, flagship, policy_for

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = flagship()
ARTIFACT_BEFORE = ARTIFACT.model_dump()


@pytest.fixture
def evidence_store(tmp_path):
    """Override: honour ``CUA_EVIDENCE_ROOT`` so a run's JSONL can be kept as committed evidence."""
    root = os.environ.get("CUA_EVIDENCE_ROOT")
    return EvidenceStore(Path(root) if root else tmp_path / "evidence")


@pytest.fixture
def surface(recorded_surface):
    """Every live replay in this module goes through the production evidence wiring."""
    return recorded_surface


def replay(
    surface,
    live,
    member_id: str,
    policy: PolicyConfig | None = None,
    *,
    recorder: EvidenceRecorder,
):
    recording = RecordingSurface(surface)
    engine = engine_for(
        recording, MonotonicClock(), base_url=live.base_url, policy=policy, recorder=recorder
    )
    result = engine.run(ARTIFACT, {"member_id": member_id})
    assert ARTIFACT.model_dump() == ARTIFACT_BEFORE  # L7: the artifact is never mutated
    assert result.session_id == surface.session_id
    return result, recording


def events_for(store: EvidenceStore, result):
    return store.read_events(store.events_path(RunKind.REPLAY, result.run_id))


# --- L1 / L2: SUCCESS, same artifact object ---------------------------------------------------


@pytest.mark.parametrize(
    "member_id, balance", [("M1001", Decimal("15275.00")), ("M1002", Decimal("4120.75"))]
)
def test_success_reads_the_savings_balance(
    surface, live_bank, evidence_recorder, evidence_store, member_id, balance
):
    result, recording = replay(surface, live_bank, member_id, recorder=evidence_recorder)
    assert result.status is TerminalStatus.SUCCESS
    assert result.outputs == {"savings_balance": balance}
    assert type(result.outputs["savings_balance"]) is Decimal
    assert str(result.outputs["savings_balance"]) == str(balance)
    assert result.outcome is None and result.failure is None
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert surface.dispatched_actions == 4
    assert [s.status for s in result.steps] == [StepStatus.COMPLETED] * 4
    assert all(s.dispatched for s in result.steps)
    # M5: the run left a coherent, redacted chronology on disk.
    path = evidence_store.events_path(RunKind.REPLAY, result.run_id)
    assert path.exists() and path.parent.parent.name == "replay"
    events = evidence_store.read_events(path)
    step = [EventType.GATE_DECISION, EventType.ACTION_DISPATCHED, EventType.ACTION_COMPLETED]
    assert [e.event_type for e in events] == [
        EventType.RUN_STARTED,
        *step * 4,
        EventType.RUN_COMPLETED,
    ]
    assert [e.seq for e in events] == list(range(1, 15))
    assert {e.session_id for e in events} == {surface.session_id}
    assert events[-1].payload.outputs == {"savings_balance": str(balance)}
    assert events[-1].payload.dispatched_actions == 4
    raw = path.read_text(encoding="utf-8")
    assert member_id not in raw and "<input:member_id>" in raw
    assert not re.search(r'"ref"', raw)
    assert all(json.loads(line)["redaction_applied"] is True for line in raw.splitlines())


# --- L3: BUSINESS_OUTCOME ---------------------------------------------------------------------


def test_m404_is_member_not_found_business_outcome(
    surface, live_bank, evidence_recorder, evidence_store
):
    result, recording = replay(surface, live_bank, "M404", recorder=evidence_recorder)
    assert result.status is TerminalStatus.BUSINESS_OUTCOME
    assert result.outcome is not None
    assert result.outcome.code == "MEMBER_NOT_FOUND"
    assert result.outcome.step_id == "s3_search"
    assert result.failure is None and result.outputs == {}
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert surface.dispatched_actions == 3
    assert result.steps[3].status is StepStatus.NOT_REACHED
    events = events_for(evidence_store, result)
    types = [e.event_type for e in events]
    assert types[-2:] == [EventType.BUSINESS_OUTCOME, EventType.RUN_COMPLETED]
    assert types.count(EventType.ACTION_DISPATCHED) == 3
    assert events[-2].payload.code == "MEMBER_NOT_FOUND" and events[-2].step_id == "s3_search"
    assert events[-1].payload.status == "BUSINESS_OUTCOME"
    assert "M404" not in evidence_store.events_path(RunKind.REPLAY, result.run_id).read_text()


# --- L4: AMBIGUOUS_TARGET fails closed ----------------------------------------------------------


def test_ambiguous_savings_fails_closed_and_reads_neither_candidate(
    surface, live_bank_ambiguous, evidence_recorder, evidence_store
):
    result, recording = replay(surface, live_bank_ambiguous, "M1001", recorder=evidence_recorder)
    assert result.status is TerminalStatus.FAILURE
    failure = result.failure
    assert failure is not None and failure.code is FailureCode.AMBIGUOUS_TARGET
    assert failure.step_id == "s4_read_savings"
    assert {c.value for c in failure.candidates} == {"$15,275.00", "$250.00"}
    assert all("ref" not in c.model_dump() for c in failure.candidates)
    assert "READ" not in recording.act_types
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert surface.dispatched_actions == 3
    assert result.outputs == {}
    events = events_for(evidence_store, result)
    dispatched = [e for e in events if e.event_type is EventType.ACTION_DISPATCHED]
    assert [d.payload.action_type for d in dispatched] == ["NAVIGATE", "FILL", "CLICK"]
    assert events[-1].event_type is EventType.RUN_FAILED
    assert events[-1].payload.failure.code == "AMBIGUOUS_TARGET"
    assert {c.value for c in events[-1].payload.failure.candidates} == {"$15,275.00", "$250.00"}


# --- L5: POLICY_DENIED with zero dispatch --------------------------------------------------------


def test_empty_policy_denies_before_any_dispatch(
    surface, live_bank, evidence_recorder, evidence_store
):
    result, recording = replay(
        surface, live_bank, "M1001", policy=PolicyConfig(), recorder=evidence_recorder
    )
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert result.failure.step_id == "s1_open_search"
    assert result.failure.deny_reason is DenyReason.ACTION_TYPE_NOT_ALLOWED
    assert recording.acts == []
    assert surface.dispatched_actions == 0
    events = events_for(evidence_store, result)
    assert [e.event_type for e in events] == [
        EventType.RUN_STARTED,
        EventType.GATE_DECISION,
        EventType.RUN_FAILED,
    ]
    assert events[1].payload.decision == "DENY"
    assert events[1].payload.deny_reason == "ACTION_TYPE_NOT_ALLOWED"


def test_read_denied_by_policy_after_three_dispatches(surface, live_bank, evidence_recorder):
    policy = policy_for(live_bank.base_url, allowed_action_types=ALL_ACTIONS - {ActionType.READ})
    result, recording = replay(
        surface, live_bank, "M1001", policy=policy, recorder=evidence_recorder
    )
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert result.failure.step_id == "s4_read_savings"
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert surface.dispatched_actions == 3
    assert result.outputs == {}


# --- L8: SURFACE_ERROR from a real driver failure ----------------------------------------------


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_failed_load_is_a_surface_error_failure_not_an_exception(
    surface, evidence_recorder, evidence_store
):
    dead = f"http://127.0.0.1:{closed_port()}"
    recording = RecordingSurface(surface)
    engine = engine_for(
        recording,
        MonotonicClock(),
        base_url=dead,
        policy=policy_for(dead),
        recorder=evidence_recorder,
    )
    result = engine.run(ARTIFACT, {"member_id": "M1001"})
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.SURFACE_ERROR
    assert result.failure.step_id == "s1_open_search"
    assert result.failure.message.startswith("SurfaceDriverError")
    assert recording.act_types == ["NAVIGATE"] and surface.dispatched_actions == 1
    events = events_for(evidence_store, result)
    assert [e.event_type for e in events] == [
        EventType.RUN_STARTED,
        EventType.GATE_DECISION,
        EventType.ACTION_DISPATCHED,
        EventType.ACTION_FAILED,
        EventType.RUN_FAILED,
    ]
    assert events[3].payload.error_type == "SurfaceDriverError"
    assert result.steps[0].dispatched is True


# --- L6: zero-model proof in a fresh interpreter --------------------------------------------------


def run_zero_model(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.replay.zero_model_replay", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_zero_model_live_replay_in_a_fresh_interpreter():
    report = run_zero_model("--member", "M1001", "--live")
    assert report["status"] == "SUCCESS"
    assert report["outputs"] == {"savings_balance": "15275.00"}
    assert report["output_types"] == {"savings_balance": "Decimal"}
    assert report["forbidden_loaded"] == [] and report["forbidden_attempted"] == []
    assert report["success_is_decimal"] is True
    assert report["events_written"] == 14 and report["member_in_evidence"] is False


# --- Milestone 8: the transfer capability reaches the irreversible step in a real browser --------


def test_transfer_reaches_confirm_and_the_gate_requires_intervention_with_zero_dispatch(
    surface, live_bank, evidence_recorder, evidence_store
):
    """s1–s3 of ``transfer_funds@1.0.0`` against the real UI (number-like amount value, review
    page rendered at the same URL), then s4: the committed IRREVERSIBLE rule stops automation
    before any driver call. No handler here — the handoff itself is the manual E08/E09."""
    artifact = ArtifactStore(ROOT / "capabilities").load_id("transfer_funds@1.0.0")
    recording = RecordingSurface(surface)
    engine = engine_for(
        recording, MonotonicClock(), base_url=live_bank.base_url, recorder=evidence_recorder
    )
    result = engine.run(artifact, {"member_id": "M1001", "amount": "500.00"})
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.INTERVENTION_REQUIRED
    assert result.failure.step_id == "s4_confirm"
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert [s.status for s in result.steps[:3]] == [StepStatus.COMPLETED] * 3
    s4 = result.steps[3]
    assert s4.gate_decision is GateDecision.REQUIRE_INTERVENTION
    assert s4.effective_risk is RiskTier.IRREVERSIBLE
    assert not s4.dispatched and s4.completed_by is not CompletedBy.HUMAN
    events = events_for(evidence_store, result)
    assert [e.step_id for e in events if e.event_type is EventType.ACTION_DISPATCHED] == [
        "s1_open_transfer",
        "s2_enter_amount",
        "s3_review",
    ]
    assert surface.dispatched_actions == 3
    raw = evidence_store.events_path(RunKind.REPLAY, result.run_id).read_text()
    assert "M1001" not in raw and "500.00" not in raw
