"""DiscoveryAgent over the scripted surface with fake model clients: every stop reason, the
model-safe request boundary, INPUT_REF normalization, policy authority, and the act-authority
invariant. Zero network, zero SDK."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cua.artifact import InputRef, LiteralValue
from cua.discovery import (
    READ_SAVINGS_BALANCE_GOAL,
    DiscoveryConfig,
    StopCode,
    StopReason,
    ValidationCode,
    assert_navigation_routes_within_policy,
)
from cua.domain import ActionType
from cua.hitl import ControlOwner, ControlOwnerState
from cua.llm import (
    ActDecision,
    BlockedReason,
    FinishDecision,
    ModelOutputError,
    ProviderError,
    ProviderErrorKind,
)
from cua.policy import DenyReason, GateDecision, PolicyConfig, RiskRule, RiskTier
from cua.surface import SurfaceDriverError
from tests.discovery.fake_llm import (
    FLAGSHIP_INTENTS,
    Blocked,
    Click,
    Fill,
    Finish,
    Nav,
    Read,
    ScriptedLLM,
    SemanticLLM,
    decision_for,
)
from tests.discovery.support import BASE_URL, ENTRY_ROUTES, agent_for, scripted_surface
from tests.replay.fake_clock import FakeClock
from tests.replay.recording_surface import RecordingSurface
from tests.replay.support import ALL_ACTIONS, LEGACY_BANK_ROUTES, policy_for, recorder_for

GOAL = READ_SAVINGS_BALANCE_GOAL
MEMBER = "M1001"


def run_flagship(llm=None, *, member: str = MEMBER, ambiguous: bool = False, **overrides):
    recorder = recorder_for()
    inner = scripted_surface(recorder, ambiguous=ambiguous)
    surface = RecordingSurface(inner)
    agent, recorder, clock = agent_for(
        surface, llm or SemanticLLM(), recorder=recorder, **overrides
    )
    result = agent.run(GOAL, {"member_id": member})
    return result, surface, inner, recorder, agent


# --- GOAL_REACHED ---------------------------------------------------------------------------------


def test_semantic_fake_discovers_the_flagship_and_reaches_the_goal():
    llm = SemanticLLM()
    result, surface, inner, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert result.detail.code is StopCode.GOAL_VERIFIED
    assert result.outputs == {"savings_balance": Decimal("15275.00")}
    assert type(result.outputs["savings_balance"]) is Decimal
    assert result.verdict is not None and result.verdict.satisfied
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert inner.dispatched_actions == 4
    assert result.model_calls == 5 and result.corrective_retries == 0
    assert result.provider == "fake" and result.model_id == llm.model_id
    assert [s.action_type for s in result.trace.steps] == [
        ActionType.NAVIGATE,
        ActionType.FILL,
        ActionType.CLICK,
        ActionType.READ,
    ]


def test_member_id_is_recorded_as_input_ref_never_as_a_literal():
    result, surface, _, _, _ = run_flagship()
    fill = result.trace.steps[1]
    assert fill.action_type is ActionType.FILL
    assert fill.value_binding_kind == "INPUT_REF" and fill.input_name == "member_id"
    assert fill.literal_value is None
    # The surface received the bound text — binding happened in application code, not the model.
    assert surface.acts[1].value == MEMBER
    dumped = result.trace.model_dump_json()
    assert MEMBER not in dumped
    assert "{member_id}" in dumped  # templated observed routes/titles


def test_no_transient_ref_survives_in_the_normalized_trace():
    result, _, _, _, _ = run_flagship()
    dumped = result.trace.model_dump(mode="json")

    def walk(node):
        if isinstance(node, dict):
            assert "ref" not in node and "refs" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(dumped)
    read = result.trace.steps[3]
    assert read.target is not None
    assert read.target.role == "cell" and read.target.accessible_name is None
    assert read.target.context_hint == "table: Accounts > row: Savings"
    assert read.identity_matches == 1 and read.read_text == "$15,275.00"
    assert read.input_evidence == {"member_id": "ROUTE"}


# --- model-safe request boundary (A1) -------------------------------------------------------------


def test_every_model_request_is_model_safe_across_the_whole_run():
    llm = SemanticLLM()
    result, _, inner, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert len(llm.requests) == 5
    echoed = 0
    for request in llm.requests:
        serialised = request.model_dump_json()
        assert MEMBER not in serialised, request.step_index
        assert request.inputs_masked is True and request.input_values == {}
        if "<input:member_id>" in serialised:
            echoed += 1
    # After the FILL (textbox value) and after the search (URL, heading, cell) the live UI echoes
    # the id; those requests carry the placeholder instead.
    assert echoed >= 3
    detail_request = llm.requests[3]
    assert detail_request.observation.url == f"{BASE_URL}/members/<input:member_id>"
    assert any(
        "Member <input:member_id>" in e.accessible_name for e in detail_request.observation.elements
    )
    assert "M1001" not in detail_request.observation.visible_text_outline
    # while the history the model sees is model-safe too
    assert detail_request.history[1].value_binding == "INPUT_REF(member_id)"
    assert detail_request.history[2].url_after == f"{BASE_URL}/members/<input:member_id>"
    # and refs are kept for the current turn (the model needs them)
    assert all(e.ref for e in detail_request.observation.elements)


def test_the_raw_snapshot_keeps_the_value_locally_for_deterministic_verification():
    """The verifier saw the raw route (input_evidence ROUTE) although the model never saw M1001."""
    llm = SemanticLLM()
    result, _, _, _, _ = run_flagship(llm)
    assert result.trace.steps[3].input_evidence == {"member_id": "ROUTE"}
    assert all(MEMBER not in r.model_dump_json() for r in llm.requests)


def test_a_fresh_observation_precedes_every_decision():
    llm = SemanticLLM()
    result, surface, _, _, _ = run_flagship(llm)
    indices = [r.observation.observation_index for r in llm.requests]
    assert indices == sorted(set(indices)) == [1, 2, 3, 4, 5]
    assert surface.observes == 5  # one observation per decision, none reused
    for request in llm.requests:
        assert request.step_index == request.observation.observation_index


def test_diagnostic_mode_is_explicit_and_unmasks_only_when_asked():
    llm = SemanticLLM()
    result, _, _, _, _ = run_flagship(llm, mask_bound_inputs=False)
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert all(r.inputs_masked is False for r in llm.requests)
    assert llm.requests[0].input_values == {"member_id": MEMBER}
    assert any(MEMBER in r.model_dump_json() for r in llm.requests)
    assert DiscoveryConfig(base_url=BASE_URL, navigation_routes=ENTRY_ROUTES).mask_bound_inputs


# --- corrective retry, MODEL_ERROR ----------------------------------------------------------------


def test_one_corrective_retry_then_success():
    def bad_then_good(request):
        return ActDecision(
            observation_index=request.observation.observation_index,
            action_type="NAVIGATE",
            route="/members/{member_id}",  # not an entry route
            intent_summary="jump straight to the member",
        )

    llm = ScriptedLLM(
        [
            bad_then_good,
            lambda r: decision_for(Nav("/members/search"), r),
            lambda r: decision_for(FLAGSHIP_INTENTS[1], r),
            lambda r: decision_for(FLAGSHIP_INTENTS[2], r),
            lambda r: decision_for(FLAGSHIP_INTENTS[3], r),
            lambda r: decision_for(Finish(), r),
        ]
    )
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert result.corrective_retries == 1 and result.model_calls == 6
    assert llm.requests[1].feedback is not None
    assert llm.requests[1].feedback.startswith("INVALID(NAVIGATION_NOT_ALLOWED)")
    assert (
        llm.requests[1].observation == llm.requests[0].observation
    )  # same observation, no re-observe
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]


def test_second_invalid_decision_is_model_error_with_zero_dispatch():
    def unknown_ref(request):
        return ActDecision(
            observation_index=request.observation.observation_index,
            action_type="CLICK",
            ref="e999",
            intent_summary="click something",
        )

    llm = ScriptedLLM([unknown_ref, unknown_ref])
    result, surface, inner, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.detail.code is StopCode.INVALID_DECISION
    assert result.detail.validation_codes == ["UNKNOWN_REF", "UNKNOWN_REF"]
    assert surface.acts == [] and inner.dispatched_actions == 0
    assert result.outputs == {} and result.corrective_retries == 1


def test_model_output_error_counts_against_the_retry():
    llm = ScriptedLLM([ModelOutputError("ACT requires action_type"), ModelOutputError("again")])
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.detail.validation_codes == ["SHAPE", "SHAPE"]
    assert llm.requests[1].feedback == "INVALID(SHAPE): ACT requires action_type"
    assert surface.acts == []


def test_provider_error_is_model_error_with_a_safe_diagnostic():
    secret = "sk-THIS-MUST-NEVER-APPEAR"
    exc = ProviderError(ProviderErrorKind.RATE_LIMIT, status_code=429)
    exc.__cause__ = RuntimeError(f"body with {secret}")
    llm = ScriptedLLM([exc])
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.detail.code is StopCode.PROVIDER_ERROR
    assert result.detail.provider_error_kind == "RATE_LIMIT"
    assert result.detail.message == "provider call failed: RATE_LIMIT (HTTP 429)"
    assert secret not in result.model_dump_json()
    assert surface.acts == [] and result.corrective_retries == 0 and result.model_calls == 1


# --- bounded execution ----------------------------------------------------------------------------


def test_max_steps_stops_the_loop():
    click_forever = SemanticLLM(
        intents=[Nav("/members/search")] + [Click("link", "Member Search")] * 30
    )
    result, surface, _, _, _ = run_flagship(click_forever, max_steps=3, no_progress_limit=10)
    assert result.stop_reason is StopReason.MAX_STEPS
    assert result.detail.code is StopCode.MAX_STEPS
    assert surface.act_types == ["NAVIGATE", "CLICK", "CLICK"] and result.outputs == {}


def test_timeout_uses_the_injected_clock():
    """The deadline comes from the injected Clock: when a provider call outlives the run, the
    decided action still runs (it was authorized) but no further observation is taken."""
    recorder = recorder_for()
    inner = scripted_surface(recorder)
    surface = RecordingSurface(inner)
    clock = FakeClock()

    def nav_then_advance(request):
        clock.sleep(20.0)  # the provider call took longer than the whole run allows
        return decision_for(Nav("/members/search"), request)

    llm = ScriptedLLM([nav_then_advance, lambda r: decision_for(FLAGSHIP_INTENTS[1], r)])
    agent, _, _ = agent_for(surface, llm, clock=clock, recorder=recorder, timeout_s=10.0)
    result = agent.run(GOAL, {"member_id": MEMBER})
    assert result.stop_reason is StopReason.TIMEOUT
    assert result.detail.code is StopCode.TIMEOUT
    assert surface.act_types == ["NAVIGATE"] and surface.observes == 1 and llm.calls == 1


def test_timeout_is_checked_again_before_a_model_call():
    """A slow observation is caught before the model is asked anything."""
    recorder = recorder_for()
    clock = FakeClock()

    class SlowObserve(RecordingSurface):
        def observe(self):
            clock.sleep(20.0)
            return super().observe()

    surface = SlowObserve(scripted_surface(recorder))
    llm = ScriptedLLM([lambda r: decision_for(Nav("/members/search"), r)])
    agent, _, _ = agent_for(surface, llm, clock=clock, recorder=recorder, timeout_s=10.0)
    result = agent.run(GOAL, {"member_id": MEMBER})
    assert result.stop_reason is StopReason.TIMEOUT
    assert surface.observes == 1 and llm.calls == 0 and surface.acts == []


def test_no_progress_dead_end_after_unchanged_pages():
    llm = SemanticLLM(
        intents=[
            Nav("/members/search"),
            Fill("textbox", "Member ID", InputRef(input_name="member_id")),
            Click("button", "Search"),
            Click("button", "Search"),
            Click("button", "Search"),
            Click("button", "Search"),
        ]
    )
    recorder = recorder_for()
    inner = scripted_surface(recorder)
    inner.click_navigates = False  # the search button does nothing
    surface = RecordingSurface(inner)
    agent, _, _ = agent_for(surface, llm, recorder=recorder, no_progress_limit=3)
    result = agent.run(GOAL, {"member_id": MEMBER})
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.NO_PROGRESS
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK", "CLICK", "CLICK"]


def test_report_blocked_is_a_dead_end_and_authorizes_nothing():
    llm = SemanticLLM(intents=[Nav("/members/search"), Blocked(BlockedReason.UNEXPECTED_STATE)])
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.REPORTED_BLOCKED
    assert "UNEXPECTED_STATE" in result.detail.message
    assert surface.act_types == ["NAVIGATE"] and result.outputs == {}


def test_m404_is_handled_deliberately_as_a_reported_business_outcome():
    llm = SemanticLLM(
        intents=[
            Nav("/members/search"),
            Fill("textbox", "Member ID", InputRef(input_name="member_id")),
            Click("button", "Search"),
            Blocked(BlockedReason.BUSINESS_OUTCOME, evidence_role="alert"),
        ]
    )
    result, surface, _, _, _ = run_flagship(llm, member="M404")
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.REPORTED_BLOCKED
    assert "READ" not in surface.act_types and result.outputs == {}
    candidate = result.trace.outcome_candidate
    assert candidate is not None and candidate.reason_code == "BUSINESS_OUTCOME"
    assert candidate.target is not None and candidate.target.role == "alert"
    assert candidate.text_template == "No member found for {member_id}."
    assert "M404" not in result.trace.model_dump_json()


# --- policy authority -----------------------------------------------------------------------------


def test_policy_deny_is_blocked_by_policy_with_zero_dispatch():
    result, surface, inner, _, _ = run_flagship(policy=PolicyConfig())
    assert result.stop_reason is StopReason.BLOCKED_BY_POLICY
    assert result.detail.code is StopCode.POLICY_DENIED
    assert result.detail.gate_decision == GateDecision.DENY.value
    assert result.detail.deny_reason == DenyReason.ACTION_TYPE_NOT_ALLOWED.value
    assert surface.acts == [] and inner.dispatched_actions == 0


def test_read_denied_after_three_dispatches():
    policy = policy_for(BASE_URL, allowed_action_types=ALL_ACTIONS - {ActionType.READ})
    result, surface, _, _, _ = run_flagship(policy=policy)
    assert result.stop_reason is StopReason.BLOCKED_BY_POLICY
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK"] and result.outputs == {}


def test_require_intervention_stops_safely_with_zero_irreversible_dispatch():
    rules = (
        RiskRule(
            action_type=ActionType.CLICK,
            route_pattern="/members/search",
            target_name="Search",
            risk=RiskTier.IRREVERSIBLE,
        ),
    )
    result, surface, _, _, _ = run_flagship(policy=policy_for(BASE_URL, risk_rules=rules))
    assert result.stop_reason is StopReason.BLOCKED_BY_POLICY
    assert result.detail.code is StopCode.INTERVENTION_REQUIRED
    assert result.detail.gate_decision == GateDecision.REQUIRE_INTERVENTION.value
    assert surface.act_types == ["NAVIGATE", "FILL"]  # the click never reached the surface


@pytest.mark.parametrize(
    "state", [ControlOwnerState.PENDING_HUMAN, ControlOwnerState.HUMAN, ControlOwnerState.RETURNING]
)
def test_control_not_owned_denies_before_any_dispatch(state):
    result, surface, _, _, _ = run_flagship(owner=ControlOwner(state))
    assert result.stop_reason is StopReason.BLOCKED_BY_POLICY
    assert result.detail.deny_reason == DenyReason.CONTROL_NOT_OWNED.value
    assert surface.acts == []


def test_navigation_route_outside_policy_passes_the_validator_but_the_gate_denies():
    """The model-navigation allowlist never widens policy: the gate is final."""
    policy = policy_for(BASE_URL, allowed_routes=("/members/{member_id}/transfer",))
    with pytest.raises(ValueError, match="outside the policy allowlist"):
        assert_navigation_routes_within_policy(ENTRY_ROUTES, policy)
    result, surface, _, _, _ = run_flagship(policy=policy)  # composed anyway, on purpose
    assert result.stop_reason is StopReason.BLOCKED_BY_POLICY
    assert result.detail.deny_reason == DenyReason.ROUTE_NOT_ALLOWED.value
    assert surface.acts == []
    assert_navigation_routes_within_policy(ENTRY_ROUTES, policy_for(BASE_URL))  # the real one


def test_navigation_to_a_policy_allowed_but_unlisted_route_is_rejected_before_the_gate():
    assert "/members/{member_id}" in LEGACY_BANK_ROUTES
    llm = ScriptedLLM(
        [
            lambda r: decision_for(Nav("/members/{member_id}"), r),
            lambda r: decision_for(Nav("/members/{member_id}"), r),
        ]
    )
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.detail.validation_codes == [
        ValidationCode.NAVIGATION_NOT_ALLOWED.value,
        ValidationCode.NAVIGATION_NOT_ALLOWED.value,
    ]
    assert surface.acts == []  # never reached the gate, never reached the surface


def test_the_model_cannot_supply_a_risk_and_the_gate_classifies_alone():
    result, _, _, _, _ = run_flagship()
    risks = [s.effective_risk for s in result.trace.steps]
    assert risks == ["SAFE_READ", "REVERSIBLE_WRITE", "SAFE_READ", "SAFE_READ"]
    assert all(s.gate_decision == "ALLOW" for s in result.trace.steps)


# --- surface failures -----------------------------------------------------------------------------


def test_driver_failure_is_a_dead_end_that_is_never_repeated():
    recorder = recorder_for()
    inner = scripted_surface(recorder)
    inner.raise_on_act = SurfaceDriverError("dead port")
    surface = RecordingSurface(inner)
    agent, _, _ = agent_for(surface, SemanticLLM(), recorder=recorder)
    result = agent.run(GOAL, {"member_id": MEMBER})
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.SURFACE_ERROR
    assert "WAS attempted" in result.detail.message and "never repeated" in result.detail.message
    assert surface.act_types == ["NAVIGATE"] and inner.dispatched_actions == 1


def test_premature_finish_is_rejected_then_the_model_reads():
    llm = SemanticLLM(
        intents=[
            Nav("/members/search"),
            Fill("textbox", "Member ID", InputRef(input_name="member_id")),
            Click("button", "Search"),
            Finish(),  # premature: nothing has been read
            Finish(),
        ],
        on_feedback=lambda request, _intent: decision_for(
            Read("cell", "row: Savings", "savings_balance"), request
        ),
    )
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert result.corrective_retries == 1
    feedback = llm.requests[4].feedback
    assert feedback is not None and feedback.startswith("INVALID(PREMATURE_FINISH)")
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]


def test_literal_fill_of_the_input_value_is_rejected_and_input_ref_accepted():
    llm = SemanticLLM(
        intents=[
            Nav("/members/search"),
            Fill("textbox", "Member ID", LiteralValue(value=MEMBER)),
            Click("button", "Search"),
            Read("cell", "row: Savings", "savings_balance"),
            Finish(),
        ],
        on_feedback=lambda request, _intent: decision_for(
            Fill("textbox", "Member ID", InputRef(input_name="member_id")), request
        ),
    )
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert llm.requests[2].feedback is not None
    assert llm.requests[2].feedback.startswith("INVALID(VALUE)")
    assert MEMBER not in llm.requests[2].feedback  # feedback is model-safe too
    assert result.trace.steps[1].value_binding_kind == "INPUT_REF"


def test_finish_alone_never_establishes_goal_reached():
    llm = ScriptedLLM(
        [
            lambda r: FinishDecision(
                observation_index=r.observation.observation_index, intent_summary="done"
            ),
            lambda r: FinishDecision(
                observation_index=r.observation.observation_index, intent_summary="really done"
            ),
        ]
    )
    result, surface, _, _, _ = run_flagship(llm)
    assert result.stop_reason is StopReason.MODEL_ERROR
    assert result.detail.validation_codes == ["PREMATURE_FINISH", "PREMATURE_FINISH"]
    assert result.outputs == {} and surface.acts == []


def test_bad_inputs_are_a_caller_error_before_anything_happens():
    recorder = recorder_for()
    surface = RecordingSurface(scripted_surface(recorder))
    agent, _, _ = agent_for(surface, SemanticLLM(), recorder=recorder)
    with pytest.raises(ValueError, match="unknown inputs"):
        agent.run(GOAL, {"member": MEMBER})
    with pytest.raises(ValueError, match="missing required"):
        agent.run(GOAL, {})
    assert surface.observes == 0 and not recorder.active
