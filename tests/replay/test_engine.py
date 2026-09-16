"""ReplayEngine against the scripted surface: every terminal path, zero dispatch where promised."""

from decimal import Decimal

import pytest

from cua.domain import ActionType
from cua.hitl import ControlOwner, ControlOwnerState
from cua.policy import DenyReason, GateDecision, PolicyConfig, RiskRule, RiskTier
from cua.replay import (
    FailureCode,
    ReplayConfig,
    ReplayDeps,
    RunResult,
    StepStatus,
    TerminalStatus,
)
from cua.surface import StaleObservationError, SurfaceDriverError, UnknownRefError
from tests.evidence.memory_sink import MemoryEvidence
from tests.replay.fake_clock import FakeClock
from tests.replay.recording_surface import RecordingSurface
from tests.replay.scripted_surface import ScriptedSurface, fixture_text
from tests.replay.support import ALL_ACTIONS, engine_for, flagship, policy_for, recorder_for

BASE = "http://fake.test"
FLAGSHIP = flagship()  # one object for the whole module


def run(surface, **kwargs) -> tuple[RunResult, RecordingSurface]:
    """Compose surface, gate, engine and one evidence recorder exactly as production would.

    ``memory=MemoryEvidence(...)`` lets a test inspect (or sabotage) the persisted evidence;
    ``wire_listener=False`` deliberately leaves the surface unwired.
    """
    recording = RecordingSurface(surface)
    clock = kwargs.pop("clock", FakeClock())
    inputs = kwargs.pop("inputs", {"member_id": "M1001"})
    recorder = recorder_for(kwargs.pop("memory", None) or MemoryEvidence())
    if kwargs.pop("wire_listener", True):
        surface.listener = recorder
    engine = engine_for(recording, clock, base_url=BASE, recorder=recorder, **kwargs)
    return engine.run(FLAGSHIP, inputs), recording


# --- the three terminal statuses -------------------------------------------------------------


def test_success_m1001_reads_the_savings_balance():
    result, recording = run(ScriptedSurface())
    assert result.status is TerminalStatus.SUCCESS
    assert result.outputs == {"savings_balance": Decimal("15275.00")}
    assert type(result.outputs["savings_balance"]) is Decimal
    assert result.outcome is None and result.failure is None
    assert result.artifact_id == "read_savings_balance@1.0.0"
    assert result.session_id == "sess_scripted" and result.run_id.startswith("run_")
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert [s.status for s in result.steps] == [StepStatus.COMPLETED] * 4
    assert all(s.dispatched and s.gate_decision is GateDecision.ALLOW for s in result.steps)
    assert [s.effective_risk for s in result.steps] == [
        RiskTier.SAFE_READ,
        RiskTier.REVERSIBLE_WRITE,
        RiskTier.SAFE_READ,
        RiskTier.SAFE_READ,
    ]


def test_same_artifact_object_m1002_and_the_artifact_is_never_mutated():
    before = FLAGSHIP.model_dump()
    result, _ = run(ScriptedSurface(), inputs={"member_id": "M1002"})
    assert result.status is TerminalStatus.SUCCESS
    assert result.outputs == {"savings_balance": Decimal("4120.75")}
    assert FLAGSHIP.model_dump() == before


def test_m404_is_a_business_outcome_not_a_failure():
    result, recording = run(ScriptedSurface(), inputs={"member_id": "M404"})
    assert result.status is TerminalStatus.BUSINESS_OUTCOME
    assert result.outcome is not None and result.outcome.code == "MEMBER_NOT_FOUND"
    assert result.outcome.step_id == "s3_search"
    assert result.failure is None and result.outputs == {}
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]  # no READ
    assert [s.status for s in result.steps] == [
        StepStatus.COMPLETED,
        StepStatus.COMPLETED,
        StepStatus.OUTCOME,
        StepStatus.NOT_REACHED,
    ]


def test_ambiguous_savings_fails_closed_with_zero_read_dispatch():
    result, recording = run(ScriptedSurface(ambiguous=True))
    assert result.status is TerminalStatus.FAILURE
    failure = result.failure
    assert failure is not None and failure.code is FailureCode.AMBIGUOUS_TARGET
    assert failure.step_id == "s4_read_savings"
    assert {c.value for c in failure.candidates} == {"$15,275.00", "$250.00"}
    assert all("ref" not in c.model_dump() for c in failure.candidates)
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert result.outputs == {}
    assert result.steps[3].status is StepStatus.FAILED and not result.steps[3].dispatched


# --- policy ----------------------------------------------------------------------------------


def test_empty_policy_denies_the_first_navigate_with_zero_dispatches():
    result, recording = run(ScriptedSurface(), policy=PolicyConfig())
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert result.failure.step_id == "s1_open_search"
    assert result.failure.deny_reason is DenyReason.ACTION_TYPE_NOT_ALLOWED
    assert recording.acts == [] and recording.observes == 0
    assert result.steps[0].gate_decision is GateDecision.DENY and not result.steps[0].dispatched


def test_read_not_allowed_denies_at_s4_after_three_dispatches():
    policy = policy_for(BASE, allowed_action_types=ALL_ACTIONS - {ActionType.READ})
    result, recording = run(ScriptedSurface(), policy=policy)
    assert result.status is TerminalStatus.FAILURE
    assert (result.failure.code, result.failure.step_id) == (
        FailureCode.POLICY_DENIED,
        "s4_read_savings",
    )
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert result.outputs == {}


def test_route_not_allowed_is_denied_on_the_observed_page():
    policy = policy_for(BASE, allowed_routes=("/members/search",))  # detail page not allowed
    result, recording = run(ScriptedSurface(), policy=policy)
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert result.failure.deny_reason is DenyReason.ROUTE_NOT_ALLOWED
    assert result.failure.step_id == "s4_read_savings"  # READ on /members/M1001
    assert "READ" not in recording.act_types


@pytest.mark.parametrize(
    "state", [ControlOwnerState.PENDING_HUMAN, ControlOwnerState.HUMAN, ControlOwnerState.RETURNING]
)
def test_control_not_owned_denies_with_zero_dispatch(state):
    result, recording = run(ScriptedSurface(), owner=ControlOwner(state))
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert result.failure.deny_reason is DenyReason.CONTROL_NOT_OWNED
    assert recording.acts == []


def test_irreversible_classification_requires_intervention_and_is_never_dispatched():
    rule = RiskRule(
        action_type=ActionType.CLICK, route_pattern="/members/search", risk=RiskTier.IRREVERSIBLE
    )
    result, recording = run(ScriptedSurface(), policy=policy_for(BASE, risk_rules=(rule,)))
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.INTERVENTION_REQUIRED
    assert result.failure.step_id == "s3_search" and result.failure.deny_reason is None
    assert recording.act_types == ["NAVIGATE", "FILL"]
    assert result.steps[2].gate_decision is GateDecision.REQUIRE_INTERVENTION
    assert result.steps[2].effective_risk is RiskTier.IRREVERSIBLE
    assert not result.steps[2].dispatched


# --- resolution / conditions / transforms ----------------------------------------------------


def test_target_not_found_after_the_deadline_with_the_observed_page():
    search_without_button = fixture_text("A_search").replace('button "Search"', 'button "Go"')
    surface = ScriptedSurface(pages={"/members/search": search_without_button})
    clock = FakeClock()
    result, recording = run(surface, clock=clock, resolve_timeout_s=1.0, poll_interval_s=0.25)
    assert result.failure.code is FailureCode.TARGET_NOT_FOUND
    assert result.failure.step_id == "s3_search"
    assert result.failure.observed is not None
    assert result.failure.observed.url == f"{BASE}/members/search"
    assert "Member Search" in result.failure.observed.outline_excerpt
    assert recording.act_types == ["NAVIGATE", "FILL"]
    assert result.steps[2].observations == 5  # 0, .25, .5, .75, 1.0
    assert clock.now() - 1000.0 == pytest.approx(1.0)


def test_postcondition_failed_when_the_click_does_not_navigate():
    surface = ScriptedSurface()
    surface.click_navigates = False
    result, recording = run(surface, condition_timeout_s=0.5, poll_interval_s=0.25)
    assert result.failure.code is FailureCode.POSTCONDITION_FAILED
    assert result.failure.step_id == "s3_search"
    assert result.failure.expected == "route_matches /members/M1001"
    assert "observed route /members/search" in result.failure.message
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]


def test_checkpoint_failure_discards_the_partial_output():
    """The READ succeeds, then the final checkpoint cannot hold: no partial output leaks."""
    detail = fixture_text("B_detail_M1001").replace("Member M1001 — Alice Morgan", "Member")
    surface = ScriptedSurface(pages={"/members/M1001": detail})
    result, recording = run(surface, condition_timeout_s=0.0)
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.POSTCONDITION_FAILED
    assert result.failure.step_id is None
    assert result.failure.message.startswith("success_checkpoint[1]")
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]  # the READ happened
    assert result.outputs == {}  # ...but is not exposed
    assert [s.status for s in result.steps] == [StepStatus.COMPLETED] * 4


def test_transform_error_on_non_numeric_cell():
    detail = fixture_text("B_detail_M1001").replace('cell "$15,275.00"', 'cell "n/a"')
    surface = ScriptedSurface(pages={"/members/M1001": detail})
    result, _ = run(surface)
    assert result.failure.code is FailureCode.TRANSFORM_ERROR
    assert result.failure.step_id == "s4_read_savings"
    assert "n/a" in result.failure.message and "DECIMAL" in result.failure.expected
    assert result.outputs == {}


def test_invalid_input_fails_before_any_observation():
    for inputs in ({}, {"member_id": "M1001", "extra": "x"}, {"member_id": "M 1001"}):
        result, recording = run(ScriptedSurface(), inputs=inputs)
        assert result.failure.code is FailureCode.INVALID_INPUT, inputs
        assert result.failure.step_id is None
        assert recording.observes == 0 and recording.acts == []
        assert result.steps == []


# --- surface error boundary --------------------------------------------------------------------


def test_surface_driver_error_on_act_is_a_surface_error_failure():
    surface = ScriptedSurface()
    surface.raise_on_act = SurfaceDriverError("Error: net::ERR_CONNECTION_REFUSED")
    result, _ = run(surface)
    assert result.failure.code is FailureCode.SURFACE_ERROR
    assert result.failure.step_id == "s1_open_search"
    assert result.failure.message.startswith("SurfaceDriverError")
    assert result.steps[0].dispatched and result.steps[0].gate_decision is GateDecision.ALLOW


def test_unknown_ref_on_act_is_a_surface_error_failure():
    surface = ScriptedSurface()
    surface.raise_on_act = UnknownRefError("e10 no longer resolves to an element")
    result, _ = run(surface)
    assert result.failure.code is FailureCode.SURFACE_ERROR


def test_surface_driver_error_on_observe_is_a_surface_error_failure():
    surface = ScriptedSurface()
    surface.raise_on_observe = SurfaceDriverError("TimeoutError: aria snapshot")
    result, recording = run(surface)
    assert result.failure.code is FailureCode.SURFACE_ERROR
    assert result.failure.step_id == "s1_open_search"  # first observation: s1's postcondition
    assert recording.act_types == ["NAVIGATE"]


@pytest.mark.parametrize(
    "exc", [RuntimeError("bug"), StaleObservationError("misuse"), ValueError("bug")]
)
def test_unexpected_exceptions_propagate_and_are_never_relabelled(exc):
    surface = ScriptedSurface()
    surface.raise_on_observe = exc
    with pytest.raises(type(exc)):
        run(surface)
    surface = ScriptedSurface()
    surface.raise_on_act = exc
    with pytest.raises(type(exc)):
        run(surface)


# --- structure ---------------------------------------------------------------------------------


def test_replay_deps_has_no_slot_for_a_model():
    fields = set(ReplayDeps.__dataclass_fields__)
    assert fields == {
        "surface",
        "action_gate",
        "clock",
        "evidence",
        "control_owner",
        "intervention",
    }
    assert not any(k in name for name in fields for k in ("llm", "model", "gemini", "client"))


def test_config_defaults_and_validation():
    config = ReplayConfig(base_url=BASE)
    assert (config.resolve_timeout_s, config.condition_timeout_s, config.poll_interval_s) == (
        5.0,
        5.0,
        0.1,
    )
    with pytest.raises(ValueError):
        ReplayConfig(base_url=BASE, poll_interval_s=0)
    with pytest.raises(ValueError):
        ReplayConfig(base_url=BASE, resolve_timeout_s=-1)


def test_dispatch_uses_the_ref_from_the_resolving_snapshot():
    """ScriptedSurface raises on any ref not minted by the latest observe(); a green run proves
    the engine hands the gate the ref together with the very snapshot it came from."""
    result, recording = run(ScriptedSurface())
    assert result.status is TerminalStatus.SUCCESS
    assert all(a.ref for a in recording.acts if a.action_type is not ActionType.NAVIGATE)


# --- Milestone 5: evidence chronology (scripted surface, memory sink) ---------------------------

from cua.evidence import EventType  # noqa: E402

RS, GD, AD, AC, AF, BO, RC, RF = (
    EventType.RUN_STARTED,
    EventType.GATE_DECISION,
    EventType.ACTION_DISPATCHED,
    EventType.ACTION_COMPLETED,
    EventType.ACTION_FAILED,
    EventType.BUSINESS_OUTCOME,
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
)
STEP = [GD, AD, AC]


def test_m1001_produces_a_coherent_evidence_chronology():
    memory = MemoryEvidence()
    result, _ = run(ScriptedSurface(), memory=memory)
    assert result.status is TerminalStatus.SUCCESS
    events = memory.events
    assert memory.types == [RS, *STEP, *STEP, *STEP, *STEP, RC]
    assert [e.seq for e in events] == list(range(1, 15))
    assert {e.run_id for e in events} == {result.run_id}
    assert {e.session_id for e in events} == {"sess_scripted"}
    assert {e.artifact_id for e in events} == {"read_savings_balance@1.0.0"}
    assert [e.step_id for e in events[1:13]] == [
        s
        for s in ("s1_open_search", "s2_enter_member", "s3_search", "s4_read_savings")
        for _ in range(3)
    ]
    assert [e.step_index for e in events[1:13]] == [i for i in (1, 2, 3, 4) for _ in range(3)]
    assert events[0].step_id is None and events[-1].step_id is None
    dispatched = memory.last.of(AD)
    assert [d.payload.dispatch_seq for d in dispatched] == [1, 2, 3, 4]
    assert [d.payload.action_type for d in dispatched] == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert all(g.payload.decision == "ALLOW" for g in memory.last.of(GD))
    terminal = events[-1].payload
    assert terminal.status == "SUCCESS" and terminal.outputs == {"savings_balance": "15275.00"}
    assert terminal.dispatched_actions == 4
    assert [s.step_id for s in terminal.steps] == [
        "s1_open_search",
        "s2_enter_member",
        "s3_search",
        "s4_read_savings",
    ]
    assert all(s.dispatched and s.status == "COMPLETED" for s in terminal.steps)
    assert events[0].payload.inputs_declared == ["member_id"]
    assert events[0].payload.provenance_source == "handwritten"


def test_m404_produces_business_outcome_evidence_and_a_completed_run():
    memory = MemoryEvidence()
    result, _ = run(ScriptedSurface(), inputs={"member_id": "M404"}, memory=memory)
    assert result.status is TerminalStatus.BUSINESS_OUTCOME
    assert memory.types == [RS, *STEP, *STEP, *STEP, BO, RC]
    outcome = memory.last.of(BO)[0]
    assert outcome.payload.code == "MEMBER_NOT_FOUND" and outcome.step_id == "s3_search"
    terminal = memory.events[-1].payload
    assert terminal.status == "BUSINESS_OUTCOME" and terminal.outputs == {}
    assert terminal.outcome.code == "MEMBER_NOT_FOUND" and terminal.failure is None
    assert not [e for e in memory.events if e.step_id == "s4_read_savings"]


def test_policy_denial_evidence_is_a_decision_without_any_dispatch():
    memory = MemoryEvidence()
    result, recording = run(ScriptedSurface(), policy=PolicyConfig(), memory=memory)
    assert result.failure.code is FailureCode.POLICY_DENIED
    assert memory.types == [RS, GD, RF]
    decision = memory.events[1].payload
    assert decision.decision == "DENY" and decision.deny_reason == "ACTION_TYPE_NOT_ALLOWED"
    assert memory.last.of(AD) == [] and recording.acts == []
    terminal = memory.events[-1].payload
    assert terminal.status == "FAILURE" and terminal.failure.code == "POLICY_DENIED"
    assert terminal.failure.deny_reason == "ACTION_TYPE_NOT_ALLOWED"
    assert terminal.dispatched_actions == 0 and not any(s.dispatched for s in terminal.steps)


def test_require_intervention_evidence_is_a_decision_without_any_dispatch():
    memory = MemoryEvidence()
    policy = policy_for(
        BASE,
        risk_rules=(
            RiskRule(
                action_type=ActionType.NAVIGATE,
                route_pattern="/members/search",
                risk=RiskTier.IRREVERSIBLE,
            ),
        ),
    )
    result, recording = run(ScriptedSurface(), policy=policy, memory=memory)
    assert result.failure.code is FailureCode.INTERVENTION_REQUIRED
    assert memory.types == [RS, GD, RF]
    assert memory.events[1].payload.decision == "REQUIRE_INTERVENTION"
    assert recording.acts == [] and memory.events[-1].payload.dispatched_actions == 0


def test_ambiguity_evidence_has_no_read_dispatch_and_candidates_without_refs():
    memory = MemoryEvidence()
    result, _ = run(ScriptedSurface(ambiguous=True), memory=memory)
    assert result.failure.code is FailureCode.AMBIGUOUS_TARGET
    assert memory.types == [RS, *STEP, *STEP, *STEP, RF]
    assert not [e for e in memory.events if e.step_id == "s4_read_savings"]
    failure = memory.events[-1].payload.failure
    assert failure.code == "AMBIGUOUS_TARGET" and failure.step_id == "s4_read_savings"
    assert {c.value for c in failure.candidates} == {"$15,275.00", "$250.00"}
    assert failure.observed.url == "http://fake.test/members/<input:member_id>"


def test_driver_failure_evidence_is_dispatched_then_failed():
    memory = MemoryEvidence()
    surface = ScriptedSurface()
    surface.raise_on_act = SurfaceDriverError("Error: net::ERR_CONNECTION_REFUSED")
    result, _ = run(surface, memory=memory)
    assert result.failure.code is FailureCode.SURFACE_ERROR
    assert memory.types == [RS, GD, AD, AF, RF]
    failed = memory.events[3].payload
    assert failed.error_type == "SurfaceDriverError" and "REFUSED" in failed.error_message
    assert result.steps[0].dispatched is True
    assert memory.events[-1].payload.steps[0].dispatched is True


def test_unknown_ref_inside_act_is_not_a_dispatch():
    surface = ScriptedSurface()
    surface.raise_on_act = UnknownRefError("e10 no longer resolves to an element")
    memory = MemoryEvidence()
    result, _ = run(surface, memory=memory)
    assert result.failure.code is FailureCode.SURFACE_ERROR
    assert result.steps[0].dispatched is False  # nothing crossed the driver boundary
    assert memory.types == [RS, GD, RF]


def test_no_ref_and_no_member_id_survive_in_persisted_evidence():
    memory = MemoryEvidence()
    result, _ = run(ScriptedSurface(), memory=memory)
    assert result.status is TerminalStatus.SUCCESS
    text = memory.text
    assert "M1001" not in text
    assert "<input:member_id>" in text
    import re

    assert not re.search(r'"ref"', text)
    assert not re.search(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])", text)


# --- Milestone 5: evidence failure semantics — fail closed, never repeat -----------------------


def test_evidence_failure_before_the_driver_call_means_the_action_was_not_attempted():
    surface = ScriptedSurface()
    memory = MemoryEvidence(fail_on={AD})
    result, recording = run(surface, memory=memory)
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert result.failure.step_id == "s1_open_search"
    assert "NOT attempted" in result.failure.message
    assert "ACTION_DISPATCHED" in result.failure.message
    assert result.steps[0].dispatched is False and result.steps[0].status is StepStatus.FAILED
    assert recording.act_types == ["NAVIGATE"]  # the gate asked once; the surface refused
    assert surface.dispatched_actions == 0  # the driver-attempt counter never moved
    assert surface.path == "about:blank"  # and the page never changed
    assert result.outputs == {}
    assert memory.types == [RS, GD]  # nothing after the failure, no RUN_FAILED
    assert [s.status for s in result.steps[1:]] == [StepStatus.NOT_REACHED] * 3


def test_gate_decision_write_failure_stops_before_the_surface_is_touched():
    surface = ScriptedSurface()
    memory = MemoryEvidence(fail_on={GD})
    result, recording = run(surface, memory=memory)
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert "GATE_DECISION" in result.failure.message and "NOT attempted" in result.failure.message
    assert result.steps[0].dispatched is False and result.steps[0].gate_decision is None
    assert recording.acts == [] and surface.dispatched_actions == 0
    assert memory.types == [RS]


def test_evidence_failure_after_the_driver_call_means_attempted_and_never_repeated():
    # Fail the ACTION_COMPLETED write of the CLICK (s3): three driver attempts, exactly one CLICK.
    surface = ScriptedSurface()
    memory = MemoryEvidence(
        fail_when=lambda e: e.event_type is AC and e.payload.action_type == "CLICK"
    )
    result, recording = run(surface, memory=memory)
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert result.failure.step_id == "s3_search"
    assert "WAS attempted" in result.failure.message
    assert "ACTION_COMPLETED" in result.failure.message
    assert result.steps[2].dispatched is True
    assert result.steps[2].gate_decision is GateDecision.ALLOW
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]  # exactly one CLICK, no repeat
    assert surface.dispatched_actions == 3
    assert surface.path == "/members/M1001"  # the click did navigate
    assert result.steps[3].status is StepStatus.NOT_REACHED and result.outputs == {}
    assert memory.types == [RS, *STEP, *STEP, GD, AD]  # nothing after the failed write


def test_evidence_failure_after_a_driver_failure_names_both():
    surface = ScriptedSurface()
    surface.raise_on_act = SurfaceDriverError("Error: net::ERR_CONNECTION_REFUSED")
    memory = MemoryEvidence(fail_on={AF})
    result, recording = run(surface, memory=memory)
    assert result.failure.code is FailureCode.EVIDENCE_ERROR  # evidence, not SURFACE_ERROR
    assert "ACTION_FAILED" in result.failure.message and "WAS attempted" in result.failure.message
    assert result.steps[0].dispatched is True
    assert recording.act_types == ["NAVIGATE"] and surface.dispatched_actions == 1
    assert memory.types == [RS, GD, AD]


def test_evidence_failure_on_begin_run_fails_before_any_observation():
    memory = MemoryEvidence(fail_open=True)
    result, recording = run(ScriptedSurface(), memory=memory)
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert result.failure.step_id is None and "RUN_STARTED" in result.failure.message
    assert recording.observes == 0 and recording.acts == [] and result.steps == []
    assert memory.sinks == []


def test_terminal_write_failure_turns_success_into_evidence_error_with_outputs_dropped():
    memory = MemoryEvidence(fail_on={RC})
    result, recording = run(ScriptedSurface(), memory=memory)
    assert result.status is TerminalStatus.FAILURE
    assert result.failure.code is FailureCode.EVIDENCE_ERROR
    assert "in-memory status was SUCCESS" in result.failure.message
    assert result.outputs == {}
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]  # nothing redispatched
    assert [s.status for s in result.steps] == [StepStatus.COMPLETED] * 4  # the steps are kept
    assert memory.types == [RS, *STEP, *STEP, *STEP, *STEP]  # no second terminal attempt


def test_evidence_error_is_never_relabelled_as_a_surface_or_policy_failure():
    for fail_on in ({GD}, {AD}, {AC}, {RC}):
        result, _ = run(ScriptedSurface(), memory=MemoryEvidence(fail_on=fail_on))
        assert result.failure.code is FailureCode.EVIDENCE_ERROR, fail_on
        assert result.failure.deny_reason is None
        assert not result.failure.message.startswith("SurfaceDriverError")


def test_a_surface_not_wired_to_the_recorder_fails_loudly_on_the_first_action():
    with pytest.raises(RuntimeError, match="not wired"):
        run(ScriptedSurface(), wire_listener=False)


def test_invalid_input_still_leaves_run_started_and_run_failed_evidence():
    memory = MemoryEvidence()
    result, _ = run(ScriptedSurface(), inputs={"member_id": "M 1001"}, memory=memory)
    assert result.failure.code is FailureCode.INVALID_INPUT
    assert memory.types == [RS, RF]
    assert "M 1001" not in memory.text


# --- Milestone 5: E10 shape — a known runtime value never survives in evidence -----------------


SENTINEL = "SENTINEL-7Q9Z-DO-NOT-PERSIST"


def test_a_sentinel_runtime_input_is_redacted_from_urls_alerts_and_error_text():
    # The sentinel is a *known runtime input* (the V1 model), then made to appear in observed
    # alert text (not found), in the FILL echo, and in a driver error message.
    memory = MemoryEvidence()
    result, _ = run(ScriptedSurface(), inputs={"member_id": SENTINEL}, memory=memory)
    assert result.status is TerminalStatus.BUSINESS_OUTCOME
    assert SENTINEL not in memory.text and "<input:member_id>" in memory.text

    surface = ScriptedSurface()
    surface.raise_on_act = SurfaceDriverError(
        f"Error: navigation to http://fake.test/x?token={SENTINEL} failed for {SENTINEL} (e12)"
    )
    memory = MemoryEvidence()
    result, _ = run(surface, inputs={"member_id": SENTINEL}, memory=memory)
    assert result.failure.code is FailureCode.SURFACE_ERROR
    failed = memory.last.of(AF)[0].payload
    assert failed.error_message == (
        "Error: navigation to http://fake.test/x?token=[REDACTED] failed for "
        "<input:member_id> (<ref>)"
    )
    assert SENTINEL not in memory.text and "e12" not in memory.text
    # The in-memory result keeps the unredacted text: redaction is the persistence boundary.
    assert SENTINEL in result.failure.message
