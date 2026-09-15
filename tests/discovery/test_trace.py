"""NormalizedTrace: persisted actions only, no ref field anywhere, mirrors the evidence summary."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cua.discovery import NormalizedTrace, OutcomeCandidate, SemanticTarget, TraceStep
from cua.discovery.summaries import trace_summary
from cua.domain import ActionType
from cua.evidence import (
    OutcomeCandidateSummary,
    Redactor,
    SemanticTargetSummary,
    TraceStepSummary,
    TraceSummary,
)

FORBIDDEN = {"ref", "refs", "element", "elements", "snapshot", "locator", "page"}


def step(**overrides) -> TraceStep:
    base = dict(
        step_index=2,
        action_type=ActionType.FILL,
        target=SemanticTarget(role="textbox", accessible_name="Member ID"),
        identity_matches=1,
        value_binding_kind="INPUT_REF",
        input_name="member_id",
        intent_summary="enter the member id",
        gate_decision="ALLOW",
        effective_risk="REVERSIBLE_WRITE",
        url_before_template="http://h/members/search",
        url_after_template="http://h/members/search",
    )
    base.update(overrides)
    return TraceStep(**base)


def test_no_trace_model_has_a_field_that_could_hold_a_ref():
    for model in (NormalizedTrace, TraceStep, SemanticTarget, OutcomeCandidate):
        assert not set(model.model_fields) & FORBIDDEN, model.__name__


def test_semantic_target_rejects_ref_shaped_strings():
    with pytest.raises(ValidationError, match="transient observation ref"):
        SemanticTarget(role="cell", accessible_name="f1e30")
    with pytest.raises(ValidationError):
        SemanticTarget(role="e12")


def test_only_persisted_action_types_are_steps():
    with pytest.raises(ValidationError, match="not a persisted action type"):
        step(action_type=ActionType.WAIT)
    with pytest.raises(ValidationError):
        step(action_type=ActionType.FINISH)


def test_trace_and_evidence_summary_shapes_never_drift():
    assert set(TraceStep.model_fields) == set(TraceStepSummary.model_fields)
    assert set(NormalizedTrace.model_fields) == set(TraceSummary.model_fields)
    assert set(SemanticTarget.model_fields) == set(SemanticTargetSummary.model_fields)
    assert set(OutcomeCandidate.model_fields) == set(OutcomeCandidateSummary.model_fields)


def test_a_templated_trace_is_a_no_op_for_the_redactor():
    trace = NormalizedTrace(
        goal_name="read_savings_balance",
        inputs_declared={"member_id": "STRING"},
        outputs_declared={"savings_balance": "DECIMAL"},
        steps=[
            step(),
            step(
                step_index=3,
                action_type=ActionType.CLICK,
                target=SemanticTarget(role="button", accessible_name="Search"),
                value_binding_kind=None,
                input_name=None,
                url_after_template="http://h/members/{member_id}",
                page_title_after_template="Member {member_id} - Console",
            ),
        ],
        outcome_candidate=OutcomeCandidate(
            reason_code="BUSINESS_OUTCOME",
            target=SemanticTarget(role="alert"),
            text_template="No member found for {member_id}.",
        ),
        provider="openai",
        model_id="gpt-x",
        discovery_run_id="run_x",
        session_id="sess_x",
    )
    summary = trace_summary(trace)
    redacted = Redactor(sensitive_values={"input:member_id": "M1001"}).redact(summary)
    assert redacted == summary.model_dump(mode="json")
    assert summary.model_dump(mode="json") == trace.model_dump(mode="json")
