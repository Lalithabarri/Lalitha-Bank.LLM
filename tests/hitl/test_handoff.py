"""Same-session HITL, SIMULATED (Milestone 8, H2–H11).

The handwritten ``transfer_funds@1.0.0`` runs against the scripted surface; the gate's real
IRREVERSIBLE rule stops automation at "Confirm transfer"; a scripted handler stands in for the
terminal operator. **The human's click is simulated as a page-state change underneath the
surface** (``ScriptedSurface.simulate_human_confirm``) — never through ``Surface.act`` — so these
tests prove the state machine, the verifier, the no-repeat invariant and the evidence, not the
headed browser. The official E08/E09 proof is the manual headed run in ``tests/evals``.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path

import pytest

import cua
from cua.artifact import ArtifactStore
from cua.domain import ActionType
from cua.evidence import EventType
from cua.hitl import (
    ControlOwner,
    ControlOwnerState,
    HandBack,
    HandBackKind,
    InterventionHandler,
    InterventionRequest,
    StdinInterventionHandler,
)
from cua.policy import ActionGate, DenyReason, GateDecision, GateRequest, ResolvedTarget
from cua.replay import (
    CompletedBy,
    FailureCode,
    ReplayConfig,
    ReplayDeps,
    ReplayEngine,
    StepStatus,
    TerminalStatus,
)
from cua.surface import ScreenshotCapable
from tests.evidence.memory_sink import MemoryEvidence
from tests.replay.fake_clock import FakeClock
from tests.replay.recording_surface import RecordingSurface
from tests.replay.scripted_surface import ScriptedSurface
from tests.replay.support import ROOT, engine_for, policy_for, recorder_for

BASE = "http://fake.test"
TRANSFER = ArtifactStore(ROOT / "capabilities").load_id("transfer_funds@1.0.0")
INPUTS = {"member_id": "M1001", "amount": "500.00"}
CUA_ROOT = Path(cua.__file__).parent

UP_TO_CONFIRM = [
    EventType.RUN_STARTED,
    *[EventType.GATE_DECISION, EventType.ACTION_DISPATCHED, EventType.ACTION_COMPLETED] * 3,
    EventType.GATE_DECISION,  # REQUIRE_INTERVENTION for s4_confirm
    EventType.INTERVENTION_REQUESTED,
    EventType.CONTROL_TRANSFERRED,  # AUTOMATION -> PENDING_HUMAN
    EventType.CONTROL_TRANSFERRED,  # PENDING_HUMAN -> HUMAN
    EventType.HAND_BACK,
    EventType.CONTROL_TRANSFERRED,  # HUMAN -> RETURNING
    EventType.INTERVENTION_VERIFIED,
]


class ScriptedHandler:
    """A stand-in operator: optionally 'acts' (via a callback the test owns), then hands back.
    Holds no surface, gate or engine — exactly like the terminal handler."""

    def __init__(self, kind: HandBackKind, human_acts=None) -> None:
        self.kind = kind
        self.human_acts = human_acts
        self.requests: list[InterventionRequest] = []

    def intervene(self, request: InterventionRequest) -> HandBack:
        self.requests.append(request)
        if self.human_acts is not None:
            self.human_acts()
        return HandBack(kind=self.kind, note="simulated operator")


def run_transfer(
    *,
    kind: HandBackKind,
    human_confirms: bool,
    memory: MemoryEvidence | None = None,
    probe=None,
    **overrides,
):
    surface = ScriptedSurface()
    recording = RecordingSurface(surface)
    memory = memory or MemoryEvidence()
    recorder = recorder_for(memory)
    surface.listener = recorder
    owner = ControlOwner()
    gate = ActionGate(policy_for(BASE), owner, observer=recorder)

    def human_acts():
        if probe is not None:
            probe(owner, gate)
        if human_confirms:
            surface.simulate_human_confirm()

    handler = ScriptedHandler(kind, human_acts)
    engine = ReplayEngine(
        ReplayDeps(
            surface=recording,
            action_gate=gate,
            clock=FakeClock(),
            evidence=recorder,
            control_owner=owner,
            intervention=handler,
        ),
        ReplayConfig(base_url=BASE, condition_timeout_s=1.0, poll_interval_s=0.5, **overrides),
    )
    result = engine.run(TRANSFER, INPUTS)
    return result, recording, memory.last, owner, handler


def _events(sink, event_type):
    return [e for e in sink.events if e.event_type is event_type]


# --- H2: one shared ControlOwner, and the gate sees every transition immediately -------------


def test_two_owner_objects_are_refused_at_run_start():
    recorder = recorder_for()
    gate = ActionGate(policy_for(BASE), ControlOwner(), observer=recorder)
    deps = ReplayDeps(
        surface=ScriptedSurface(listener=recorder),
        action_gate=gate,
        clock=FakeClock(),
        evidence=recorder,
        control_owner=ControlOwner(),  # equivalent state, different object
    )
    with pytest.raises(RuntimeError, match="same ControlOwner object"):
        ReplayEngine(deps, ReplayConfig(base_url=BASE)).run(TRANSFER, INPUTS)
    assert not recorder.active


def test_the_gate_denies_automation_while_the_shared_owner_is_human_and_returning():
    seen: list = []

    def probe(owner: ControlOwner, gate: ActionGate) -> None:
        seen.append(owner.state)
        request = GateRequest(
            action_type=ActionType.CLICK,
            origin=BASE,
            route="/members/M1001/transfer",
            target=ResolvedTarget(role="button", accessible_name="Confirm transfer"),
        )
        seen.append(gate.authorize(request))

    result, _, _, owner, _ = run_transfer(kind=HandBackKind.DONE, human_confirms=True, probe=probe)
    state, decision = seen
    assert state is ControlOwnerState.HUMAN
    assert decision.decision is GateDecision.DENY
    assert decision.deny_reason is DenyReason.CONTROL_NOT_OWNED
    assert result.status is TerminalStatus.SUCCESS
    assert owner.is_automation  # restored on the same object only after verification


def test_returning_still_denies_automation():
    owner = ControlOwner(ControlOwnerState.RETURNING)
    gate = ActionGate(policy_for(BASE), owner)
    request = GateRequest(action_type=ActionType.NAVIGATE, origin=BASE, route="/members/search")
    assert gate.authorize(request).deny_reason is DenyReason.CONTROL_NOT_OWNED
    assert gate.control_owner is owner


# --- H4: the handler cannot reach the browser, structurally -----------------------------------


def test_intervention_handler_receives_only_the_request():
    params = list(inspect.signature(InterventionHandler.intervene).parameters)
    assert params == ["self", "request"]
    assert inspect.signature(InterventionHandler.intervene).parameters["request"].annotation in (
        InterventionRequest,
        "InterventionRequest",
    )
    init = inspect.signature(StdinInterventionHandler.__init__).parameters
    assert set(init) == {"self", "input", "output"}
    for name in ("cli", "intervention", "control"):
        tree = ast.parse((CUA_ROOT / "hitl" / f"{name}.py").read_text())
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        assert not any(
            m.startswith(("cua.surface", "cua.replay", "cua.policy", "cua.llm", "playwright"))
            for m in imported
        ), (name, imported)
    forbidden = {"ref", "refs", "element", "snapshot", "page", "locator", "surface", "prompt"}
    assert not set(InterventionRequest.model_fields) & forbidden


def test_stdin_handler_accepts_only_done_or_abort_and_treats_eof_as_abort():
    from io import StringIO

    request = InterventionRequest(
        request_id="ivr_x",
        run_id="run_x",
        session_id="sess_x",
        artifact_id="transfer_funds@1.0.0",
        step_id="s4_confirm",
        step_index=4,
        reason="IRREVERSIBLE",
        risk="IRREVERSIBLE",
        requested_action_summary="CLICK button 'Confirm transfer'",
        verification_requirement="element_present role='status' ...",
        created_at="2026-09-16T00:00:00Z",
        owner_before="AUTOMATION",
        owner_after="HUMAN",
    )
    lines = iter(["what\n", "DONE clicked it\n", ""])
    out = StringIO()
    handler = StdinInterventionHandler(input=lambda: next(lines), output=out)
    assert handler.intervene(request) == HandBack(kind=HandBackKind.DONE, note="clicked it")
    assert "Confirm transfer" in out.getvalue() and "type 'done' or 'abort'" in out.getvalue()
    assert handler.intervene(request).kind is HandBackKind.ABORT  # EOF is never "done"


# --- H5–H8, H10, H11: verified human completion, done -----------------------------------------


def test_verified_completion_with_done_advances_past_the_step_and_never_redispatches_it():
    result, recording, sink, owner, handler = run_transfer(
        kind=HandBackKind.DONE, human_confirms=True
    )
    assert result.status is TerminalStatus.SUCCESS
    assert result.outputs == {"transfer_reference": "TXN-000001"}
    # H8: the human completed s4; automation never dispatched it and went straight to s5
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    s4 = result.steps[3]
    assert s4.step_id == "s4_confirm"
    assert s4.status is StepStatus.COMPLETED
    assert s4.completed_by is CompletedBy.HUMAN
    assert s4.dispatched is False
    assert s4.gate_decision is GateDecision.REQUIRE_INTERVENTION
    assert [e.step_id for e in _events(sink, EventType.ACTION_DISPATCHED)] == [
        "s1_open_transfer",
        "s2_enter_amount",
        "s3_review",
        "s5_read_reference",
    ]
    gate_for_s4 = [e for e in _events(sink, EventType.GATE_DECISION) if e.step_id == "s4_confirm"]
    assert [e.payload.decision for e in gate_for_s4] == ["REQUIRE_INTERVENTION"]
    # H5: one run, one session, the same surface throughout
    assert {e.session_id for e in sink.events} == {"sess_scripted"} == {result.session_id}
    assert {e.run_id for e in sink.events} == {result.run_id}
    assert len(_events(sink, EventType.RUN_STARTED)) == 1
    assert owner.is_automation
    # H11: the chronology, in order, then s5 and the terminal event
    types = [e.event_type for e in sink.events]
    assert types == [
        *UP_TO_CONFIRM,
        EventType.CONTROL_TRANSFERRED,  # RETURNING -> AUTOMATION
        EventType.GATE_DECISION,
        EventType.ACTION_DISPATCHED,
        EventType.ACTION_COMPLETED,
        EventType.RUN_COMPLETED,
    ]
    transfers = [
        (e.payload.from_owner, e.payload.to_owner)
        for e in _events(sink, EventType.CONTROL_TRANSFERRED)
    ]
    assert transfers == [
        ("AUTOMATION", "PENDING_HUMAN"),
        ("PENDING_HUMAN", "HUMAN"),
        ("HUMAN", "RETURNING"),
        ("RETURNING", "AUTOMATION"),
    ]
    verified = _events(sink, EventType.INTERVENTION_VERIFIED)[0].payload
    assert verified.outcome == "VERIFIED_COMPLETED" and verified.completed_by == "HUMAN"
    assert verified.hand_back == "DONE" and verified.observations >= 1  # H6: re-observed
    assert verified.pre_observation_digest != verified.post_observation_digest
    # the request the handler saw is the request in evidence, and carries no ref/selector
    (request,) = handler.requests
    requested = _events(sink, EventType.INTERVENTION_REQUESTED)[0].payload
    assert request.request_id == requested.request_id == verified.request_id
    assert request.requested_action_summary == "CLICK button 'Confirm transfer'"
    assert "status" in request.verification_requirement
    assert request.risk == "IRREVERSIBLE" and request.owner_after == "HUMAN"
    # redaction: runtime inputs never persist; nothing binary or absolute in the JSONL
    text = "\n".join(sink.lines)
    assert "M1001" not in text and "500.00" not in text
    assert "<input:member_id>" in text
    assert "\\u0089" not in text and "PNG scripted" not in text and "/Users" not in text
    assert all(e.redaction_applied for e in sink.events)


def test_pre_and_post_screenshots_are_stored_beside_the_run_and_match_the_verifier_state():
    _, recording, sink, _, handler = run_transfer(kind=HandBackKind.DONE, human_confirms=True)
    (request,) = handler.requests
    pre = f"artifacts/intervention_{request.request_id}_pre.png"
    post = f"artifacts/intervention_{request.request_id}_post.png"
    assert set(sink.artifacts) == {pre, post}
    assert recording.screenshots == 2  # H10: none on ordinary steps
    verified = _events(sink, EventType.INTERVENTION_VERIFIED)[0].payload
    requested = _events(sink, EventType.INTERVENTION_REQUESTED)[0].payload
    for ref, path in (
        (requested.pre_screenshot, pre),
        (verified.pre_screenshot, pre),
        (verified.post_screenshot, post),
    ):
        assert ref is not None
        assert ref.path == path and ref.media_type == "image/png"
        assert ref.sha256 == hashlib.sha256(sink.artifacts[path]).hexdigest()
        assert ref.size_bytes == len(sink.artifacts[path])
        assert not ref.path.startswith("/")
    # the PRE capture is the review page (automation still owned control); the POST capture is
    # the state the verdict was made on — the completion page
    assert sink.artifacts[pre].endswith(b"/members/M1001/transfer")
    assert sink.artifacts[post].endswith(b"/members/M1001/transfer/complete")
    for line in sink.lines:
        json.loads(line)  # events remain plain JSON; bytes never enter the JSONL


# --- verified completion, abort: committed but not resumed --------------------------------------


def test_verified_completion_with_abort_is_intervention_abandoned_not_a_cancellation():
    result, recording, sink, owner, _ = run_transfer(kind=HandBackKind.ABORT, human_confirms=True)
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.INTERVENTION_ABANDONED
    assert result.failure.safe_to_retry is False
    assert "verified as committed" in result.failure.message
    assert result.outputs == {}
    assert result.steps[3].completed_by is CompletedBy.HUMAN and not result.steps[3].dispatched
    assert result.steps[4].status is StepStatus.NOT_REACHED
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert owner.state is ControlOwnerState.RETURNING  # never restored
    verified = _events(sink, EventType.INTERVENTION_VERIFIED)[0].payload
    assert verified.outcome == "VERIFIED_COMPLETED" and verified.hand_back == "ABORT"
    assert verified.completed_by == "HUMAN"
    assert [e.event_type for e in sink.events] == [*UP_TO_CONFIRM, EventType.RUN_FAILED]


# --- H9: unverifiable state, done or abort -> UNKNOWN_COMMIT_STATE ---------------------------


@pytest.mark.parametrize("kind", [HandBackKind.DONE, HandBackKind.ABORT])
def test_unverifiable_state_is_unknown_commit_state_whatever_the_command_said(kind):
    """The human handed back without the completion signal being observable (still on the
    review page). Remaining on the review page proves nothing; the command text proves
    nothing. Never retried, never restored."""
    result, recording, sink, owner, _ = run_transfer(kind=kind, human_confirms=False)
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.UNKNOWN_COMMIT_STATE
    assert result.failure.safe_to_retry is False
    assert result.failure.step_id == "s4_confirm"
    assert result.failure.observed is not None  # the post-hand-back page, for reconciliation
    assert result.outputs == {}
    assert result.steps[3].completed_by is None and not result.steps[3].dispatched
    assert result.steps[3].status is StepStatus.FAILED
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert owner.state is ControlOwnerState.RETURNING
    verified = _events(sink, EventType.INTERVENTION_VERIFIED)[0].payload
    assert verified.outcome == "UNKNOWN_COMMIT_STATE" and verified.completed_by is None
    assert verified.hand_back == kind.value
    assert verified.observations >= 2  # bounded verification polled, did not decide on one look
    assert verified.pre_observation_digest == verified.post_observation_digest  # same page
    assert sink.artifacts[verified.post_screenshot.path].endswith(b"/members/M1001/transfer")
    assert [e.event_type for e in sink.events] == [*UP_TO_CONFIRM, EventType.RUN_FAILED]
    assert not any(
        e.payload.to_owner == "AUTOMATION" for e in _events(sink, EventType.CONTROL_TRANSFERRED)
    )


# --- evidence failure around the human action ---------------------------------------------------


def test_pre_evidence_failure_fails_closed_before_any_human_control():
    memory = MemoryEvidence(fail_artifacts=["_pre"])
    result, recording, sink, owner, handler = run_transfer(
        kind=HandBackKind.DONE, human_confirms=True, memory=memory
    )
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert result.failure.safe_to_retry is None  # nothing irreversible could have happened
    assert handler.requests == []  # the human was never prompted
    assert owner.is_automation  # ownership was never granted
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"] and recording.screenshots == 1
    assert not any(e.event_type is EventType.INTERVENTION_REQUESTED for e in sink.events)


def test_post_evidence_failure_after_verified_completion_never_repeats_or_resumes():
    memory = MemoryEvidence(fail_artifacts=["_post"])
    result, recording, sink, owner, _ = run_transfer(
        kind=HandBackKind.DONE, human_confirms=True, memory=memory
    )
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert result.failure.safe_to_retry is False
    assert "verified as committed" in result.failure.message
    assert result.steps[3].completed_by is CompletedBy.HUMAN and not result.steps[3].dispatched
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]  # s5 never ran
    assert owner.state is ControlOwnerState.RETURNING
    # nothing was written after the failed capture (no INTERVENTION_VERIFIED, no RUN_FAILED)
    assert [e.event_type for e in sink.events] == UP_TO_CONFIRM[:-1]


def test_post_evidence_failure_with_unverifiable_state_keeps_unknown_commit_state():
    memory = MemoryEvidence(fail_artifacts=["_post"])
    result, _, sink, owner, _ = run_transfer(
        kind=HandBackKind.DONE, human_confirms=False, memory=memory
    )
    assert result.failure.code is FailureCode.UNKNOWN_COMMIT_STATE
    assert result.failure.safe_to_retry is False
    assert "evidence" in result.failure.message
    assert owner.state is ControlOwnerState.RETURNING
    assert [e.event_type for e in sink.events] == UP_TO_CONFIRM[:-1]


def test_a_surface_without_screenshots_cannot_be_used_for_interventions():
    class NoShots:
        session_id = "sess_x"

        def observe(self):
            raise AssertionError

        def act(self, action):
            raise AssertionError

        def close(self):
            pass

    assert not isinstance(NoShots(), ScreenshotCapable)
    owner = ControlOwner()
    recorder = recorder_for()
    engine = ReplayEngine(
        ReplayDeps(
            surface=NoShots(),
            action_gate=ActionGate(policy_for(BASE), owner, observer=recorder),
            clock=FakeClock(),
            evidence=recorder,
            control_owner=owner,
            intervention=ScriptedHandler(HandBackKind.DONE),
        ),
        ReplayConfig(base_url=BASE),
    )
    with pytest.raises(RuntimeError, match="ScreenshotCapable"):
        engine.run(TRANSFER, INPUTS)


def test_without_a_handler_require_intervention_is_still_a_terminal_failure_with_zero_dispatch():
    surface = ScriptedSurface()
    recording = RecordingSurface(surface)
    recorder = recorder_for()
    surface.listener = recorder
    result = engine_for(recording, FakeClock(), base_url=BASE, recorder=recorder).run(
        TRANSFER, INPUTS
    )
    assert result.failure.code is FailureCode.INTERVENTION_REQUIRED
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert not result.steps[3].dispatched and result.steps[3].completed_by is None
