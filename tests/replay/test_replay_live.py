"""Milestone 4 end-to-end proofs: the checked-in artifact, a real Chromium, the real Legacy Bank.

One artifact object for the whole module (never cloned or mutated per member). Every claim is
asserted from the RunResult and from what the surface actually received.
"""

import json
import socket
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from cua.domain import ActionType
from cua.policy import DenyReason, PolicyConfig
from cua.replay import FailureCode, MonotonicClock, StepStatus, TerminalStatus
from tests.replay.recording_surface import RecordingSurface
from tests.replay.support import ALL_ACTIONS, engine_for, flagship, policy_for

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = flagship()
ARTIFACT_BEFORE = ARTIFACT.model_dump()


def replay(surface, live, member_id: str, policy: PolicyConfig | None = None):
    recording = RecordingSurface(surface)
    engine = engine_for(recording, MonotonicClock(), base_url=live.base_url, policy=policy)
    result = engine.run(ARTIFACT, {"member_id": member_id})
    assert ARTIFACT.model_dump() == ARTIFACT_BEFORE  # L7: the artifact is never mutated
    assert result.session_id == surface.session_id
    return result, recording


# --- L1 / L2: SUCCESS, same artifact object ---------------------------------------------------


@pytest.mark.parametrize(
    "member_id, balance", [("M1001", Decimal("15275.00")), ("M1002", Decimal("4120.75"))]
)
def test_success_reads_the_savings_balance(surface, live_bank, member_id, balance):
    result, recording = replay(surface, live_bank, member_id)
    assert result.status is TerminalStatus.SUCCESS
    assert result.outputs == {"savings_balance": balance}
    assert type(result.outputs["savings_balance"]) is Decimal
    assert str(result.outputs["savings_balance"]) == str(balance)
    assert result.outcome is None and result.failure is None
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert surface.dispatched_actions == 4
    assert [s.status for s in result.steps] == [StepStatus.COMPLETED] * 4
    assert all(s.dispatched for s in result.steps)


# --- L3: BUSINESS_OUTCOME ---------------------------------------------------------------------


def test_m404_is_member_not_found_business_outcome(surface, live_bank):
    result, recording = replay(surface, live_bank, "M404")
    assert result.status is TerminalStatus.BUSINESS_OUTCOME
    assert result.outcome is not None
    assert result.outcome.code == "MEMBER_NOT_FOUND"
    assert result.outcome.step_id == "s3_search"
    assert result.failure is None and result.outputs == {}
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert surface.dispatched_actions == 3
    assert result.steps[3].status is StepStatus.NOT_REACHED


# --- L4: AMBIGUOUS_TARGET fails closed ----------------------------------------------------------


def test_ambiguous_savings_fails_closed_and_reads_neither_candidate(surface, live_bank_ambiguous):
    result, recording = replay(surface, live_bank_ambiguous, "M1001")
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


# --- L5: POLICY_DENIED with zero dispatch --------------------------------------------------------


def test_empty_policy_denies_before_any_dispatch(surface, live_bank):
    result, recording = replay(surface, live_bank, "M1001", policy=PolicyConfig())
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert result.failure.step_id == "s1_open_search"
    assert result.failure.deny_reason is DenyReason.ACTION_TYPE_NOT_ALLOWED
    assert recording.acts == []
    assert surface.dispatched_actions == 0


def test_read_denied_by_policy_after_three_dispatches(surface, live_bank):
    policy = policy_for(live_bank.base_url, allowed_action_types=ALL_ACTIONS - {ActionType.READ})
    result, recording = replay(surface, live_bank, "M1001", policy=policy)
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


def test_failed_load_is_a_surface_error_failure_not_an_exception(surface):
    dead = f"http://127.0.0.1:{closed_port()}"
    recording = RecordingSurface(surface)
    engine = engine_for(recording, MonotonicClock(), base_url=dead, policy=policy_for(dead))
    result = engine.run(ARTIFACT, {"member_id": "M1001"})
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.SURFACE_ERROR
    assert result.failure.step_id == "s1_open_search"
    assert result.failure.message.startswith("SurfaceDriverError")
    assert recording.act_types == ["NAVIGATE"] and surface.dispatched_actions == 1


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
