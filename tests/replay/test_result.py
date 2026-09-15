"""RunResult: exactly three statuses; self-contained terminal invariants; no float, no inputs."""

import json
from decimal import Decimal

import pytest
from pydantic import ValidationError

from cua.artifact import KnownOutcome
from cua.replay import (
    FailureCode,
    FailureDetail,
    OutcomeDetail,
    RunResult,
    StepStatus,
    TerminalStatus,
)


def result(**overrides) -> RunResult:
    base = dict(
        run_id="run_x",
        artifact_id="demo@0.1.0",
        capability_version="0.1.0",
        session_id="sess_x",
        status=TerminalStatus.SUCCESS,
    )
    base.update(overrides)
    return RunResult(**base)


FAILURE = FailureDetail(
    code=FailureCode.TARGET_NOT_FOUND, step_id="s4", message="m", expected="one element"
)
OUTCOME = OutcomeDetail(code="MEMBER_NOT_FOUND", description="d", step_id="s3")


def test_exactly_three_terminal_statuses():
    assert {s.value for s in TerminalStatus} == {"SUCCESS", "BUSINESS_OUTCOME", "FAILURE"}


def test_failure_code_vocabulary_is_the_m4_minimum_plus_evidence():
    """M4's eight codes plus EVIDENCE_ERROR (M5): evidence failure is its own domain."""
    assert {c.value for c in FailureCode} == {
        "AMBIGUOUS_TARGET",
        "TARGET_NOT_FOUND",
        "POLICY_DENIED",
        "INTERVENTION_REQUIRED",
        "POSTCONDITION_FAILED",
        "INVALID_INPUT",
        "TRANSFORM_ERROR",
        "SURFACE_ERROR",
        "EVIDENCE_ERROR",
    }


def test_business_outcome_literal_agrees_with_the_artifact_schema():
    """artifact/ must not import replay/, so KnownOutcome keeps a string literal; they agree."""
    literal = KnownOutcome.model_fields["terminal_status"].annotation.__args__
    assert literal == (TerminalStatus.BUSINESS_OUTCOME.value,)


def test_result_fields_are_exactly_the_minimized_set():
    assert set(RunResult.model_fields) == {
        "run_id",
        "artifact_id",
        "capability_version",
        "session_id",
        "status",
        "outputs",
        "outcome",
        "failure",
        "steps",
    }
    assert not hasattr(result(), "inputs")


# --- invariants -------------------------------------------------------------------------------


def test_success_carries_outputs_and_nothing_else():
    ok = result(outputs={"savings_balance": Decimal("15275.00")})
    assert ok.outcome is None and ok.failure is None
    with pytest.raises(ValidationError):
        result(outcome=OUTCOME)
    with pytest.raises(ValidationError):
        result(failure=FAILURE)


def test_success_with_empty_outputs_is_the_engines_call_not_the_models():
    assert result(outputs={}).status is TerminalStatus.SUCCESS


def test_business_outcome_requires_outcome_and_empty_outputs():
    ok = result(status=TerminalStatus.BUSINESS_OUTCOME, outcome=OUTCOME)
    assert ok.outputs == {}
    with pytest.raises(ValidationError):
        result(status=TerminalStatus.BUSINESS_OUTCOME)
    with pytest.raises(ValidationError):
        result(status=TerminalStatus.BUSINESS_OUTCOME, outcome=OUTCOME, failure=FAILURE)
    with pytest.raises(ValidationError):
        result(status=TerminalStatus.BUSINESS_OUTCOME, outcome=OUTCOME, outputs={"x": "1"})


def test_failure_requires_failure_and_empty_outputs():
    ok = result(status=TerminalStatus.FAILURE, failure=FAILURE)
    assert ok.outputs == {}
    with pytest.raises(ValidationError):
        result(status=TerminalStatus.FAILURE)
    with pytest.raises(ValidationError):
        result(status=TerminalStatus.FAILURE, failure=FAILURE, outcome=OUTCOME)
    with pytest.raises(ValidationError):
        result(status=TerminalStatus.FAILURE, failure=FAILURE, outputs={"partial": "1"})


# --- typing / serialization ---------------------------------------------------------------


def test_outputs_keep_exact_python_types():
    ok = result(outputs={"d": Decimal("4120.75"), "i": 7, "b": True, "s": "x"})
    assert {k: type(v) for k, v in ok.outputs.items()} == {
        "d": Decimal,
        "i": int,
        "b": bool,
        "s": str,
    }


def test_decimal_output_serializes_as_a_string_never_a_float():
    payload = json.loads(result(outputs={"d": Decimal("15275.00")}).model_dump_json())
    assert payload["outputs"]["d"] == "15275.00"
    assert isinstance(payload["outputs"]["d"], str)


def test_failure_detail_cannot_carry_a_ref_or_approval():
    with pytest.raises(ValidationError):
        FailureDetail(
            code=FailureCode.SURFACE_ERROR, step_id=None, message="", expected="", ref="e1"
        )
    with pytest.raises(ValidationError):
        result(approved_by="human")
    assert StepStatus.NOT_REACHED.value == "NOT_REACHED"
