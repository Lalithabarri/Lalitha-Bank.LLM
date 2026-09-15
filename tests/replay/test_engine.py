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
from tests.replay.fake_clock import FakeClock
from tests.replay.recording_surface import RecordingSurface
from tests.replay.scripted_surface import ScriptedSurface, fixture_text
from tests.replay.support import ALL_ACTIONS, engine_for, flagship, policy_for

BASE = "http://fake.test"
FLAGSHIP = flagship()  # one object for the whole module


def run(surface, **kwargs) -> tuple[RunResult, RecordingSurface]:
    recording = RecordingSurface(surface)
    clock = kwargs.pop("clock", FakeClock())
    inputs = kwargs.pop("inputs", {"member_id": "M1001"})
    engine = engine_for(recording, clock, base_url=BASE, **kwargs)
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
    assert fields == {"surface", "action_gate", "clock"}
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
