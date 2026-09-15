"""Discovery state as evidence payloads.

``cua.evidence`` must not import ``cua.discovery`` (discovery depends on evidence, not the
reverse), so the mapping from the loop's own objects to the evidence layer's payload models lives
here — the same arrangement as ``cua.replay.summaries``. Nothing here carries a ref, an element
list, a prompt, a completion or a provider object; ``Decimal`` outputs become text.
"""

from __future__ import annotations

from cua.artifact import InputRef
from cua.discovery.goal import GoalSpec
from cua.discovery.result import DiscoveryResult
from cua.discovery.trace import NormalizedTrace, SemanticTarget
from cua.domain import SurfaceElement, SurfaceSnapshot
from cua.evidence import (
    DecisionSummary,
    DiscoveryEndedPayload,
    DiscoveryStartedPayload,
    ModelCallPayload,
    ObservationPayload,
    OutcomeCandidateSummary,
    RunKind,
    SemanticTargetSummary,
    StopDetailSummary,
    TargetSummary,
    TraceStepSummary,
    TraceSummary,
    ValidationSummary,
)
from cua.llm import ActDecision, BlockedDecision, CallMetadata, DiscoveryDecision

OUTLINE_EXCERPT = 300


def discovery_started_payload(
    goal: GoalSpec,
    *,
    provider: str,
    model_id: str,
    base_url: str,
    max_steps: int,
    timeout_s: float,
    navigation_routes: tuple[str, ...],
    mask_bound_inputs: bool,
) -> DiscoveryStartedPayload:
    return DiscoveryStartedPayload(
        run_kind=RunKind.DISCOVERY,
        goal_name=goal.name,
        inputs_declared=sorted(goal.inputs),  # names only
        outputs_declared=sorted(goal.outputs),
        provider=provider,
        model_id=model_id,
        base_url=base_url,
        max_steps=max_steps,
        timeout_s=timeout_s,
        model_navigation_routes=list(navigation_routes),
        mask_bound_inputs=mask_bound_inputs,
    )


def observation_payload(
    snapshot: SurfaceSnapshot, *, digest: str, truncated: bool
) -> ObservationPayload:
    return ObservationPayload(
        observation_index=snapshot.step_index,
        url=snapshot.url,
        page_title=snapshot.page_title,
        element_count=len(snapshot.elements),
        truncated=truncated,
        outline_excerpt=snapshot.visible_text_outline[:OUTLINE_EXCERPT],
        digest=digest,
    )


def decision_summary(
    decision: DiscoveryDecision, element: SurfaceElement | None
) -> DecisionSummary:
    """The decision in semantic terms. ``element`` is the resolved ref, when it resolved."""
    target = (
        TargetSummary(
            role=element.role,
            accessible_name=element.accessible_name,
            context_hint=element.context_hint,
            value=element.value,
        )
        if element
        else None
    )
    if isinstance(decision, ActDecision):
        binding = decision.value
        return DecisionSummary(
            kind=decision.kind.value,
            action_type=decision.action_type.value,
            target=target,
            value_binding_kind=binding.kind.value if binding else None,
            input_name=binding.input_name if isinstance(binding, InputRef) else None,
            route_template=decision.route,
            output_name=decision.output_name,
            intent_summary=decision.intent_summary,
        )
    if isinstance(decision, BlockedDecision):
        return DecisionSummary(
            kind=decision.kind.value,
            target=target,
            blocked_reason=decision.reason_code.value,
            intent_summary=decision.intent_summary,
        )
    return DecisionSummary(kind=decision.kind.value, intent_summary=decision.intent_summary)


def model_call_payload(
    *,
    call_index: int,
    step_index: int,
    purpose: str,
    provider: str,
    model_id: str,
    call: CallMetadata | None,
    outcome: str,
    decision: DecisionSummary | None,
    validation_status: str | None,
    validation_code: str | None,
    validation_feedback: str | None,
    corrective_retry_count: int,
    error_kind: str | None = None,
    status_code: int | None = None,
) -> ModelCallPayload:
    return ModelCallPayload(
        call_index=call_index,
        step_index=step_index,
        purpose=purpose,
        provider=provider,
        model_id_requested=call.model_id_requested if call else model_id,
        model_id_reported=call.model_id_reported if call else None,
        provider_response_id=call.provider_response_id if call else None,
        latency_ms=call.latency_ms if call else None,
        input_tokens=call.input_tokens if call else None,
        output_tokens=call.output_tokens if call else None,
        outcome=outcome,
        decision=decision,
        validation=(
            ValidationSummary(
                status=validation_status, code=validation_code, feedback=validation_feedback
            )
            if validation_status
            else None
        ),
        corrective_retry_count=corrective_retry_count,
        error_kind=error_kind,
        status_code=status_code,
    )


def _target_summary(target: SemanticTarget | None) -> SemanticTargetSummary | None:
    if target is None:
        return None
    return SemanticTargetSummary(
        role=target.role, accessible_name=target.accessible_name, context_hint=target.context_hint
    )


def trace_summary(trace: NormalizedTrace) -> TraceSummary:
    """Field-for-field; a test asserts the two shapes never drift."""
    return TraceSummary(
        goal_name=trace.goal_name,
        inputs_declared=dict(trace.inputs_declared),
        outputs_declared=dict(trace.outputs_declared),
        steps=[
            TraceStepSummary(
                step_index=s.step_index,
                action_type=s.action_type.value,
                target=_target_summary(s.target),
                identity_matches=s.identity_matches,
                value_binding_kind=s.value_binding_kind,
                input_name=s.input_name,
                literal_value=s.literal_value,
                route_template=s.route_template,
                output_name=s.output_name,
                read_text=s.read_text,
                input_evidence=dict(s.input_evidence),
                intent_summary=s.intent_summary,
                gate_decision=s.gate_decision,
                effective_risk=s.effective_risk,
                url_before_template=s.url_before_template,
                url_after_template=s.url_after_template,
                page_title_after_template=s.page_title_after_template,
            )
            for s in trace.steps
        ],
        outcome_candidate=(
            OutcomeCandidateSummary(
                reason_code=trace.outcome_candidate.reason_code,
                target=_target_summary(trace.outcome_candidate.target),
                text_template=trace.outcome_candidate.text_template,
            )
            if trace.outcome_candidate
            else None
        ),
        provider=trace.provider,
        model_id=trace.model_id,
        discovery_run_id=trace.discovery_run_id,
        session_id=trace.session_id,
    )


def discovery_ended_payload(
    result: DiscoveryResult, *, dispatched_actions: int
) -> DiscoveryEndedPayload:
    outputs = result.model_dump(mode="json")["outputs"]
    detail = result.detail
    return DiscoveryEndedPayload(
        stop_reason=result.stop_reason.value,
        detail=StopDetailSummary(
            code=detail.code.value,
            message=detail.message,
            gate_decision=detail.gate_decision,
            deny_reason=detail.deny_reason,
            validation_codes=list(detail.validation_codes),
            provider_error_kind=detail.provider_error_kind,
        ),
        goal_satisfied=result.verdict.satisfied if result.verdict else False,
        verdict_reason=result.verdict.reason if result.verdict else None,
        outputs=dict(outputs),
        steps_dispatched=result.steps_dispatched,
        dispatched_actions=dispatched_actions,
        model_calls=result.model_calls,
        corrective_retries=result.corrective_retries,
        trace=trace_summary(result.trace),
    )


__all__ = [
    "OUTLINE_EXCERPT",
    "decision_summary",
    "discovery_ended_payload",
    "discovery_started_payload",
    "model_call_payload",
    "observation_payload",
    "trace_summary",
]
