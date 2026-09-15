"""The caller-facing result of a replay (ARCHITECTURE §7, REQUIREMENTS §5, D15).

Exactly three terminal statuses. A recoverable condition the engine resolves during execution
(polling, re-observation) is never a status; it only shows up as ``StepRecord.observations``.
A failure carries what step, what was expected, and what was observed.

``RunResult`` enforces only invariants it can judge on its own:

* SUCCESS          -> typed outputs, no outcome, no failure
* BUSINESS_OUTCOME -> an outcome, no failure, ``outputs == {}``
* FAILURE          -> a failure, no outcome, ``outputs == {}``

Outputs are authoritative only on SUCCESS: a value READ before a later step or the final
checkpoint failed is discarded from the terminal result rather than exposed as a trustworthy
partial output. Whether the outputs match the artifact's declared outputs is a cross-object
fact that ``ReplayEngine`` proves before it constructs a SUCCESS — the result never carries
artifact output specs. It carries no inputs either: echoing member identifiers (or, later,
sensitive inputs) into every terminal result is unnecessary; evidence and redaction are the
EvidenceWriter's job.

No field anywhere in this module can hold a transient observation ref (I1): observed
elements are reported as ``ResolvedTarget`` (role, name, context, value).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import model_validator

from cua.domain import ActionType, DomainModel, SurfaceSnapshot
from cua.policy import DenyReason, GateDecision, ResolvedTarget, RiskTier


class TerminalStatus(StrEnum):
    SUCCESS = "SUCCESS"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    FAILURE = "FAILURE"


class FailureCode(StrEnum):
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    POLICY_DENIED = "POLICY_DENIED"
    # The gate answered REQUIRE_INTERVENTION and the engine cannot yet suspend for a human
    # (HITL transitions arrive at ARCHITECTURE §15 step 16). Never conflated with POLICY_DENIED:
    # a hard DENY is not a request for approval, and vice versa.
    INTERVENTION_REQUIRED = "INTERVENTION_REQUIRED"
    POSTCONDITION_FAILED = "POSTCONDITION_FAILED"
    INVALID_INPUT = "INVALID_INPUT"
    TRANSFORM_ERROR = "TRANSFORM_ERROR"
    SURFACE_ERROR = "SURFACE_ERROR"
    # The evidence layer could not record the run (ARCHITECTURE §10, D19). Its own failure
    # domain: never SURFACE_ERROR, never POLICY_DENIED. A run without its proof is not a success;
    # the message says which event failed and whether a driver action had already been attempted.
    EVIDENCE_ERROR = "EVIDENCE_ERROR"


class StepStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    OUTCOME = "OUTCOME"  # a declared business outcome was detected at this step
    NOT_REACHED = "NOT_REACHED"


OutputValue = Decimal | int | bool | str


class SnapshotSummary(DomainModel):
    """What the page looked like, without elements or refs."""

    url: str
    page_title: str
    step_index: int
    outline_excerpt: str

    @classmethod
    def of(cls, snapshot: SurfaceSnapshot, limit: int = 300) -> SnapshotSummary:
        return cls(
            url=snapshot.url,
            page_title=snapshot.page_title,
            step_index=snapshot.step_index,
            outline_excerpt=snapshot.visible_text_outline[:limit],
        )


class FailureDetail(DomainModel):
    code: FailureCode
    step_id: str | None  # None: the artifact-level success checkpoint
    message: str
    expected: str
    observed: SnapshotSummary | None = None
    candidates: list[ResolvedTarget] = []  # AMBIGUOUS_TARGET only
    deny_reason: DenyReason | None = None  # POLICY_DENIED only


class OutcomeDetail(DomainModel):
    code: str
    description: str
    step_id: str | None  # None: detected while verifying the final checkpoint


class StepRecord(DomainModel):
    step_id: str
    action: ActionType
    declared_risk: RiskTier
    status: StepStatus
    observations: int = 0
    gate_decision: GateDecision | None = None
    effective_risk: RiskTier | None = None
    dispatched: bool = False


class RunResult(DomainModel):
    run_id: str
    artifact_id: str
    capability_version: str
    session_id: str
    status: TerminalStatus
    outputs: dict[str, OutputValue] = {}
    outcome: OutcomeDetail | None = None
    failure: FailureDetail | None = None
    steps: list[StepRecord] = []

    @model_validator(mode="after")
    def _terminal_invariants(self) -> RunResult:
        if self.status is TerminalStatus.SUCCESS:
            if self.outcome is not None or self.failure is not None:
                raise ValueError("SUCCESS carries neither an outcome nor a failure")
        elif self.status is TerminalStatus.BUSINESS_OUTCOME:
            if self.outcome is None:
                raise ValueError("BUSINESS_OUTCOME requires an outcome")
            if self.failure is not None:
                raise ValueError("BUSINESS_OUTCOME carries no failure")
            if self.outputs:
                raise ValueError("outputs are authoritative only on SUCCESS")
        else:
            if self.failure is None:
                raise ValueError("FAILURE requires a failure detail")
            if self.outcome is not None:
                raise ValueError("FAILURE carries no outcome")
            if self.outputs:
                raise ValueError("outputs are authoritative only on SUCCESS")
        return self
