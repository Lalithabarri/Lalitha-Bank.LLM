"""ReadSavingsBalanceVerifier: satisfied only when every condition holds, from the trace alone."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from cua.discovery import READ_SAVINGS_BALANCE_GOAL, NormalizedTrace, bind_goal_inputs
from cua.discovery.trace import SemanticTarget, TraceStep
from cua.domain import ActionType

GOAL = READ_SAVINGS_BALANCE_GOAL
BOUND = {"member_id": "M1001"}
CUA_SRC = Path(__file__).resolve().parents[2] / "src" / "cua"


def read_step(**overrides) -> TraceStep:
    base = dict(
        step_index=4,
        action_type=ActionType.READ,
        target=SemanticTarget(role="cell", context_hint="table: Accounts > row: Savings"),
        identity_matches=1,
        output_name="savings_balance",
        read_text="$15,275.00",
        input_evidence={"member_id": "ROUTE"},
        intent_summary="read the savings balance",
        gate_decision="ALLOW",
        effective_risk="SAFE_READ",
        url_before_template="http://h/members/{member_id}",
        url_after_template="http://h/members/{member_id}",
    )
    base.update(overrides)
    return TraceStep(**base)


def trace(*steps: TraceStep, outputs=None) -> NormalizedTrace:
    return NormalizedTrace(
        goal_name=GOAL.name,
        inputs_declared={"member_id": "STRING"},
        outputs_declared=outputs if outputs is not None else {"savings_balance": "DECIMAL"},
        steps=list(steps),
        provider="fake",
        model_id="fake-1",
        discovery_run_id="run_x",
        session_id="sess_x",
    )


def test_satisfied_when_all_five_conditions_hold():
    verdict = GOAL.verifier.verify(trace(read_step()), BOUND)
    assert verdict.satisfied
    assert verdict.outputs == {"savings_balance": Decimal("15275.00")}
    assert type(verdict.outputs["savings_balance"]) is Decimal
    assert "ROUTE" in verdict.reason


def test_heading_evidence_is_accepted_too():
    verdict = GOAL.verifier.verify(trace(read_step(input_evidence={"member_id": "HEADING"})), BOUND)
    assert verdict.satisfied and "HEADING" in verdict.reason


@pytest.mark.parametrize(
    "steps, reason",
    [
        ((), "no READ populating"),
        ((read_step(), read_step(step_index=5)), "exactly one is required"),
        (
            (read_step(target=SemanticTarget(role="heading", accessible_name="Member")),),
            "table cell",
        ),
        (
            (
                read_step(
                    target=SemanticTarget(
                        role="cell", context_hint="table: Accounts > row: Checking"
                    )
                ),
            ),
            "row headed 'Savings'",
        ),
        ((read_step(target=SemanticTarget(role="cell")),), "row headed 'Savings'"),
        ((read_step(identity_matches=2),), "not identified unambiguously"),
        ((read_step(input_evidence={}),), "did not evidence the requested member"),
        ((read_step(read_text=None),), "returned no text"),
        ((read_step(read_text="n/a"),), "not a decimal"),
    ],
)
def test_each_condition_fails_with_its_own_reason(steps, reason):
    verdict = GOAL.verifier.verify(trace(*steps), BOUND)
    assert not verdict.satisfied and verdict.outputs == {}
    assert reason in verdict.reason


def test_output_shape_must_match_the_declaration():
    verdict = GOAL.verifier.verify(trace(read_step(), outputs={"savings_balance": "STRING"}), BOUND)
    assert not verdict.satisfied and "declared output shape" in verdict.reason


def test_a_read_for_another_output_does_not_count():
    other = read_step(output_name="checking_balance")
    verdict = GOAL.verifier.verify(trace(other), BOUND)
    assert not verdict.satisfied and "no READ populating" in verdict.reason


def test_row_label_match_is_case_insensitive_and_exact():
    ok = read_step(target=SemanticTarget(role="cell", context_hint="table: X > row: SAVINGS"))
    assert GOAL.verifier.verify(trace(ok), BOUND).satisfied
    near = read_step(target=SemanticTarget(role="cell", context_hint="table: X > row: Savings 2"))
    assert not GOAL.verifier.verify(trace(near), BOUND).satisfied


def test_no_balance_constant_anywhere_in_cua():
    """The seed balance lives only in the synthetic target (legacy_bank/data.py), never in cua."""
    offenders = [
        str(path)
        for path in CUA_SRC.rglob("*.py")
        if re.search(r"15,?275", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_bind_goal_inputs_rejects_unknown_missing_and_mistyped():
    assert bind_goal_inputs(GOAL, {"member_id": "M1001"}) == {"member_id": "M1001"}
    with pytest.raises(ValueError, match="unknown inputs"):
        bind_goal_inputs(GOAL, {"member_id": "M1001", "extra": "x"})
    with pytest.raises(ValueError, match="missing required"):
        bind_goal_inputs(GOAL, {})
    with pytest.raises(ValueError, match="non-empty text"):
        bind_goal_inputs(GOAL, {"member_id": ""})
    with pytest.raises(ValueError, match="non-empty text"):
        bind_goal_inputs(GOAL, {"member_id": 1001})
