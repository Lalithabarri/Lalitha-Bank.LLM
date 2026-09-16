"""Replay results as evidence payloads.

``cua.evidence`` must not import ``cua.replay`` (replay depends on evidence, not the reverse), so
the mapping from ``RunResult`` / ``ReplayConfig`` to the evidence layer's own payload models lives
here, on the replay side. Lossless for status, failure, outcome and step records; ``Decimal``
outputs become text (never a float).
"""

from __future__ import annotations

from collections.abc import Mapping

from cua.artifact import CapabilityArtifact
from cua.evidence import (
    EventType,
    FailureSummary,
    ObservationSummary,
    OutcomeSummary,
    RunKind,
    RunStartedPayload,
    RunTerminalPayload,
    StepSummary,
    TargetSummary,
)
from cua.replay.result import RunResult, TerminalStatus


def run_started_payload(
    artifact: CapabilityArtifact,
    *,
    run_kind: RunKind,
    base_url: str,
    resolve_timeout_s: float,
    condition_timeout_s: float,
    poll_interval_s: float,
) -> RunStartedPayload:
    return RunStartedPayload(
        run_kind=run_kind,
        artifact_id=artifact.artifact_id,
        capability_name=artifact.capability_name,
        capability_version=artifact.capability_version,
        provenance_source=artifact.provenance.source.value,
        base_url=base_url,
        inputs_declared=sorted(artifact.inputs),  # names only
        resolve_timeout_s=resolve_timeout_s,
        condition_timeout_s=condition_timeout_s,
        poll_interval_s=poll_interval_s,
    )


def terminal_payload(result: RunResult, *, dispatched_actions: int) -> RunTerminalPayload:
    kind = (
        EventType.RUN_FAILED if result.status is TerminalStatus.FAILURE else EventType.RUN_COMPLETED
    )
    outputs: Mapping[str, str | int | bool] = result.model_dump(mode="json")["outputs"]
    failure = result.failure
    outcome = result.outcome
    return RunTerminalPayload(
        kind=kind,
        status=result.status.value,
        outputs=dict(outputs),
        outcome=(
            OutcomeSummary(
                code=outcome.code, description=outcome.description, step_id=outcome.step_id
            )
            if outcome
            else None
        ),
        failure=(
            FailureSummary(
                code=failure.code.value,
                step_id=failure.step_id,
                message=failure.message,
                expected=failure.expected,
                observed=(
                    ObservationSummary(
                        url=failure.observed.url,
                        page_title=failure.observed.page_title,
                        observation_index=failure.observed.step_index,
                        outline_excerpt=failure.observed.outline_excerpt,
                    )
                    if failure.observed
                    else None
                ),
                candidates=[
                    TargetSummary(
                        role=c.role,
                        accessible_name=c.accessible_name,
                        context_hint=c.context_hint,
                        value=c.value,
                    )
                    for c in failure.candidates
                ],
                deny_reason=failure.deny_reason.value if failure.deny_reason else None,
                safe_to_retry=failure.safe_to_retry,
            )
            if failure
            else None
        ),
        steps=[
            StepSummary(
                step_id=step.step_id,
                step_index=index,
                action=step.action.value,
                declared_risk=step.declared_risk.value,
                status=step.status.value,
                observations=step.observations,
                gate_decision=step.gate_decision.value if step.gate_decision else None,
                effective_risk=step.effective_risk.value if step.effective_risk else None,
                dispatched=step.dispatched,
                completed_by=step.completed_by.value if step.completed_by else None,
            )
            for index, step in enumerate(result.steps, start=1)
        ],
        dispatched_actions=dispatched_actions,
    )
