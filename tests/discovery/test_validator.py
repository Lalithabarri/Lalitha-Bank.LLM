"""DecisionValidator: every rejection code, in isolation, against the Milestone 2 captures."""

from __future__ import annotations

import pytest

from cua.artifact import InputRef, LiteralValue
from cua.discovery import (
    READ_SAVINGS_BALANCE_GOAL,
    DecisionValidator,
    InputTemplater,
    Invalid,
    NormalizedTrace,
    PlaceholderStyle,
    ValidAct,
    ValidationCode,
    ValidBlocked,
    ValidFinish,
    normalize_route,
)
from cua.discovery.trace import SemanticTarget, TraceStep
from cua.domain import ActionType
from cua.llm import ActDecision, BlockedDecision, BlockedReason, FinishDecision
from cua.surface import find
from tests.discovery.support import (
    BASE_URL,
    ENTRY_ROUTES,
    detail_snapshot,
    not_found_snapshot,
    search_snapshot,
)

GOAL = READ_SAVINGS_BALANCE_GOAL
BOUND = {"member_id": "M1001"}


def validator(**overrides) -> DecisionValidator:
    kwargs = dict(
        goal=GOAL,
        bound_inputs=BOUND,
        base_url=BASE_URL,
        navigation_routes=ENTRY_ROUTES,
        trace_templater=InputTemplater(BOUND, PlaceholderStyle.TRACE),
    )
    kwargs.update(overrides)
    return DecisionValidator(**kwargs)


def trace(*steps: TraceStep) -> NormalizedTrace:
    return NormalizedTrace(
        goal_name=GOAL.name,
        inputs_declared={"member_id": "STRING"},
        outputs_declared={"savings_balance": "DECIMAL"},
        steps=list(steps),
        provider="fake",
        model_id="fake-1",
        discovery_run_id="run_x",
        session_id="sess_x",
    )


def act(snapshot, **fields) -> ActDecision:
    fields.setdefault("observation_index", snapshot.step_index)
    fields.setdefault("intent_summary", "do the next thing")
    return ActDecision(**fields)


def ref_of(snapshot, role, name=None, context=None) -> str:
    matches = find(snapshot, role=role, name=name, context_hint=context)
    assert len(matches) == 1, (role, name, context, len(matches))
    return matches[0].ref


def code_of(outcome) -> ValidationCode:
    assert isinstance(outcome, Invalid), outcome
    return outcome.code


# --- observation binding -----------------------------------------------------------------------


def test_stale_observation_index_is_rejected_first():
    snap = search_snapshot(step_index=2)
    decision = act(
        snap, observation_index=1, action_type="CLICK", ref=ref_of(snap, "button", "Search")
    )
    assert (
        code_of(validator().validate(decision, snap, trace())) is ValidationCode.STALE_OBSERVATION
    )


@pytest.mark.parametrize("ref", ["e999", "zz", "f1e99", "e10"])
def test_unknown_invented_or_previous_page_refs_are_rejected(ref):
    snap = detail_snapshot()  # refs are f1e…; ``e10`` belongs to the search page
    decision = act(snap, action_type="CLICK", ref=ref)
    assert code_of(validator().validate(decision, snap, trace())) is ValidationCode.UNKNOWN_REF


# --- shape ----------------------------------------------------------------------------------------


def test_shape_violations():
    snap = search_snapshot()
    button = ref_of(snap, "button", "Search")
    textbox = ref_of(snap, "textbox", "Member ID")
    v = validator()
    cases = [
        act(snap, action_type="NAVIGATE", route="/members/search", ref=button),  # ref on NAVIGATE
        act(snap, action_type="NAVIGATE"),  # no route
        act(snap, action_type="CLICK", ref=button, route="/members/search"),  # route on CLICK
        act(snap, action_type="CLICK"),  # no ref
        act(snap, action_type="FILL", ref=textbox),  # no value
        act(snap, action_type="CLICK", ref=button, value=LiteralValue(value="x")),  # value on CLICK
        act(snap, action_type="READ", ref=textbox),  # no output_name
        act(
            snap, action_type="CLICK", ref=button, output_name="savings_balance"
        ),  # output on CLICK
    ]
    for decision in cases:
        assert code_of(v.validate(decision, snap, trace())) is ValidationCode.SHAPE, decision


# --- illegal action / element combinations --------------------------------------------------------


def test_illegal_targets():
    snap = detail_snapshot()
    v = validator()
    cell = ref_of(snap, "cell", context="table: Accounts > row: Savings")
    heading = find(snap, role="heading")[0].ref
    link = ref_of(snap, "link", "Transfer funds")
    fill_a_link = act(snap, action_type="FILL", ref=link, value=LiteralValue(value="x"))
    select_a_cell = act(snap, action_type="SELECT", ref=cell, value=LiteralValue(value="x"))
    click_a_cell = act(snap, action_type="CLICK", ref=cell)
    click_a_heading = act(snap, action_type="CLICK", ref=heading)
    for decision in (fill_a_link, select_a_cell, click_a_cell, click_a_heading):
        assert code_of(v.validate(decision, snap, trace())) is ValidationCode.ILLEGAL_TARGET


def test_disabled_controls_cannot_be_operated():
    snap = search_snapshot()
    disabled = snap.model_copy(
        update={"elements": [e.model_copy(update={"enabled": False}) for e in snap.elements]}
    )
    button = ref_of(disabled, "button", "Search")
    decision = act(disabled, action_type="CLICK", ref=button)
    assert (
        code_of(validator().validate(decision, disabled, trace())) is ValidationCode.ILLEGAL_TARGET
    )
    # READ of a disabled element is still fine: reading changes nothing
    textbox = ref_of(disabled, "textbox", "Member ID")
    readable = act(disabled, action_type="READ", ref=textbox, output_name="savings_balance")
    assert isinstance(validator().validate(readable, disabled, trace()), ValidAct)


# --- values ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value, code",
    [
        (InputRef(input_name="account_number"), ValidationCode.VALUE),
        (LiteralValue(value="M1001"), ValidationCode.VALUE),
        (LiteralValue(value="see M1001 now"), ValidationCode.VALUE),
        (LiteralValue(value="<input:member_id>"), ValidationCode.VALUE),
        (LiteralValue(value="x" * 201), ValidationCode.VALUE),
        (LiteralValue(value="a\nb"), ValidationCode.VALUE),
    ],
)
def test_value_bindings_that_are_rejected(value, code):
    snap = search_snapshot()
    textbox = ref_of(snap, "textbox", "Member ID")
    decision = act(snap, action_type="FILL", ref=textbox, value=value)
    assert code_of(validator().validate(decision, snap, trace())) is code


def test_input_ref_binds_the_runtime_value_in_application_code():
    snap = search_snapshot()
    textbox = ref_of(snap, "textbox", "Member ID")
    decision = act(snap, action_type="FILL", ref=textbox, value=InputRef(input_name="member_id"))
    outcome = validator().validate(decision, snap, trace())
    assert isinstance(outcome, ValidAct)
    assert outcome.action.action_type is ActionType.FILL
    assert outcome.action.ref == textbox and outcome.action.value == "M1001"
    assert outcome.value_binding == InputRef(input_name="member_id")
    assert outcome.target is not None and outcome.target.accessible_name == "Member ID"
    assert outcome.semantic_target == SemanticTarget(
        role="textbox", accessible_name="Member ID", context_hint="group: Look up member"
    )
    assert outcome.identity_matches == 1


def test_a_plain_literal_is_allowed():
    snap = search_snapshot()
    textbox = ref_of(snap, "textbox", "Member ID")
    decision = act(snap, action_type="FILL", ref=textbox, value=LiteralValue(value="500.00"))
    outcome = validator().validate(decision, snap, trace())
    assert isinstance(outcome, ValidAct) and outcome.action.value == "500.00"


# --- navigation -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "route, code",
    [
        ("/members/{member_id}", ValidationCode.NAVIGATION_NOT_ALLOWED),
        ("/members/M1001", ValidationCode.NAVIGATION_NOT_ALLOWED),
        ("/members/search/../M1001", ValidationCode.NAVIGATION_NOT_ALLOWED),
        ("http://evil.example/members/search", ValidationCode.NAVIGATION_NOT_ALLOWED),
        ("/members/search?x=1", ValidationCode.NAVIGATION_NOT_ALLOWED),
    ],
)
def test_navigation_outside_the_entry_routes_is_rejected_before_the_gate(route, code):
    snap = search_snapshot()
    decision = act(snap, action_type="NAVIGATE", route=route)
    assert code_of(validator().validate(decision, snap, trace())) is code


def test_entry_route_binds_to_one_destination_on_the_base_url():
    snap = search_snapshot()
    decision = act(snap, action_type="NAVIGATE", route="/members/search/")
    outcome = validator().validate(decision, snap, trace())
    assert isinstance(outcome, ValidAct)
    assert outcome.action.url == f"{BASE_URL}/members/search"
    assert outcome.route_template == "/members/search" and outcome.target is None


def test_a_listed_template_route_binds_placeholders_and_refuses_the_literal():
    v = validator(navigation_routes=("/members/search", "/members/{member_id}"))
    snap = search_snapshot()
    ok = v.validate(act(snap, action_type="NAVIGATE", route="/members/{member_id}"), snap, trace())
    assert isinstance(ok, ValidAct) and ok.action.url == f"{BASE_URL}/members/M1001"
    assert ok.route_template == "/members/{member_id}"
    literal = v.validate(act(snap, action_type="NAVIGATE", route="/members/M1001"), snap, trace())
    assert code_of(literal) is ValidationCode.NAVIGATION_NOT_ALLOWED
    unbound = validator(
        navigation_routes=("/members/{other}",),
    ).validate(act(snap, action_type="NAVIGATE", route="/members/{other}"), snap, trace())
    assert code_of(unbound) is ValidationCode.ROUTE


def test_normalize_route_strips_one_trailing_slash_only():
    assert normalize_route("/members/search/") == "/members/search"
    assert normalize_route("/") == "/"


# --- outputs --------------------------------------------------------------------------------------


def test_read_outputs_must_be_declared_and_read_once():
    snap = detail_snapshot()
    cell = ref_of(snap, "cell", context="table: Accounts > row: Savings")
    undeclared = act(snap, action_type="READ", ref=cell, output_name="checking_balance")
    assert code_of(validator().validate(undeclared, snap, trace())) is ValidationCode.OUTPUT
    already = TraceStep(
        step_index=1,
        action_type=ActionType.READ,
        target=SemanticTarget(role="cell", context_hint="table: Accounts > row: Savings"),
        identity_matches=1,
        output_name="savings_balance",
        read_text="$1.00",
        intent_summary="read",
        gate_decision="ALLOW",
        effective_risk="SAFE_READ",
        url_before_template="u",
        url_after_template="u",
    )
    again = act(snap, action_type="READ", ref=cell, output_name="savings_balance")
    assert code_of(validator().validate(again, snap, trace(already))) is ValidationCode.OUTPUT
    fine = validator().validate(again, snap, trace())
    assert isinstance(fine, ValidAct) and fine.output_name == "savings_balance"


# --- FINISH / REPORT_BLOCKED ---------------------------------------------------------------------


def test_premature_finish_is_rejected_with_the_verifier_reason():
    snap = detail_snapshot()
    decision = FinishDecision(observation_index=snap.step_index, intent_summary="done")
    outcome = validator().validate(decision, snap, trace())
    assert code_of(outcome) is ValidationCode.PREMATURE_FINISH
    assert "no READ populating 'savings_balance'" in outcome.feedback


def test_finish_after_a_verified_read_is_valid():
    snap = detail_snapshot()
    read = TraceStep(
        step_index=4,
        action_type=ActionType.READ,
        target=SemanticTarget(role="cell", context_hint="table: Accounts > row: Savings"),
        identity_matches=1,
        output_name="savings_balance",
        read_text="$15,275.00",
        input_evidence={"member_id": "ROUTE"},
        intent_summary="read",
        gate_decision="ALLOW",
        effective_risk="SAFE_READ",
        url_before_template="u",
        url_after_template="u",
    )
    decision = FinishDecision(observation_index=snap.step_index, intent_summary="done")
    outcome = validator().validate(decision, snap, trace(read))
    assert isinstance(outcome, ValidFinish) and outcome.verdict.satisfied


def test_report_blocked_with_evidence_ref_yields_a_templated_outcome_candidate():
    v = validator(
        bound_inputs={"member_id": "M404"},
        trace_templater=InputTemplater({"member_id": "M404"}, PlaceholderStyle.TRACE),
    )
    snap = not_found_snapshot()
    alert = find(snap, role="alert")[0]
    decision = BlockedDecision(
        observation_index=snap.step_index,
        reason_code=BlockedReason.BUSINESS_OUTCOME,
        evidence_ref=alert.ref,
        intent_summary="the console reports no such member",
    )
    outcome = v.validate(decision, snap, trace())
    assert isinstance(outcome, ValidBlocked)
    assert outcome.outcome_candidate is not None
    assert outcome.outcome_candidate.target.role == "alert"
    assert outcome.outcome_candidate.text_template == "No member found for {member_id}."
    unknown = BlockedDecision(
        observation_index=snap.step_index,
        reason_code=BlockedReason.OTHER,
        evidence_ref="e999",
        intent_summary="blocked",
    )
    assert code_of(v.validate(unknown, snap, trace())) is ValidationCode.UNKNOWN_REF
