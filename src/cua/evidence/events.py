"""The typed, versioned evidence envelope (ARCHITECTURE §10, D19).

One ``EvidenceEvent`` per line of ``events.jsonl``. The envelope fields are system-generated
identifiers and enums — never observed or user-supplied text. Everything observed lives in the
``payload``, which is the only part the ``Redactor`` needs to rewrite before persistence.

Vocabulary 1.0 — the minimum needed to prove a replay happened, what the gate decided, and what
crossed the driver boundary::

    RUN_STARTED  GATE_DECISION  ACTION_DISPATCHED  ACTION_COMPLETED  ACTION_FAILED
    BUSINESS_OUTCOME  RUN_COMPLETED  RUN_FAILED

``ACTION_DISPATCHED`` is written at the Surface boundary immediately *before* the driver call:
it means the system crossed the final authority boundary and attempted the operation, not that
the browser completed it. Exactly one ``ACTION_COMPLETED`` or ``ACTION_FAILED`` follows — unless
the process died first. **An unpaired ``ACTION_DISPATCHED`` must always be read as "the action may
have executed."** ``ACTION_FAILED`` means the driver operation did not complete cleanly, not that it
had no effect.

``RUN_COMPLETED`` is the terminal event for both ``SUCCESS`` and ``BUSINESS_OUTCOME`` — a
legitimate business result is not a crash (REQUIREMENTS §5). ``RUN_FAILED`` is the terminal event
for ``FAILURE``.

Structural invariant (I1 for evidence): no payload model has a field that could hold a transient
observation ref, a ``SurfaceElement``, a ``SurfaceSnapshot`` or a driver object. Observed elements
are summarised as ``TargetSummary`` (role, name, context, value). A test asserts the field names.

Adding an event type or a ``RunKind`` is a ``schema_version`` bump: readers validate strictly.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from cua.domain import DomainModel

EVIDENCE_SCHEMA_VERSION = "1.0"


class RunKind(StrEnum):
    REPLAY = "REPLAY"


class EventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    GATE_DECISION = "GATE_DECISION"
    ACTION_DISPATCHED = "ACTION_DISPATCHED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"


class Severity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class EvidenceError(Exception):
    """Evidence could not be recorded — its own failure domain.

    Raised for environment and state failures of the evidence layer only (cannot open or append
    to the run file, serialisation refused, a write outside an active run, a write after the
    run's evidence already failed). It is never wrapped as a surface or policy failure, and the
    replay engine maps it to ``FAILURE / EVIDENCE_ERROR``. Programming errors inside evidence
    code are not converted into it.

    ``event_type`` names the event that could not be recorded. ``after_dispatch`` is True when a
    driver action had already been attempted when the failure happened — the caller must then
    never repeat the action.
    """

    def __init__(
        self,
        message: str,
        *,
        event_type: EventType | None = None,
        after_dispatch: bool = False,
    ) -> None:
        super().__init__(message)
        self.event_type = event_type
        self.after_dispatch = after_dispatch


# --- payload building blocks -------------------------------------------------------------------


class TargetSummary(DomainModel):
    """An element in semantic terms. Deliberately no ref (mirrors ``policy.ResolvedTarget``)."""

    role: str
    accessible_name: str
    context_hint: str | None = None
    value: str | None = None


class ObservationSummary(DomainModel):
    """What a page looked like, without elements or refs."""

    url: str
    page_title: str
    observation_index: int
    outline_excerpt: str


class OutcomeSummary(DomainModel):
    code: str
    description: str
    step_id: str | None


class FailureSummary(DomainModel):
    code: str
    step_id: str | None
    message: str
    expected: str
    observed: ObservationSummary | None = None
    candidates: list[TargetSummary] = []
    deny_reason: str | None = None


class StepSummary(DomainModel):
    step_id: str
    step_index: int
    action: str
    declared_risk: str
    status: str
    observations: int
    gate_decision: str | None = None
    effective_risk: str | None = None
    dispatched: bool


# --- payloads --------------------------------------------------------------------------------


class RunStartedPayload(DomainModel):
    kind: Literal[EventType.RUN_STARTED] = EventType.RUN_STARTED
    run_kind: RunKind
    artifact_id: str
    capability_name: str
    capability_version: str
    provenance_source: str
    base_url: str  # origin only
    inputs_declared: list[str]  # names only — values are never persisted
    resolve_timeout_s: float
    condition_timeout_s: float
    poll_interval_s: float


class GateDecisionPayload(DomainModel):
    kind: Literal[EventType.GATE_DECISION] = EventType.GATE_DECISION
    action_type: str
    origin: str
    route: str
    decision: str
    risk: str
    declared_risk: str | None = None
    deny_reason: str | None = None
    explanation: str
    target: TargetSummary | None = None


class ActionDispatchedPayload(DomainModel):
    kind: Literal[EventType.ACTION_DISPATCHED] = EventType.ACTION_DISPATCHED
    dispatch_seq: int
    observation_index: int
    action_type: str
    url_before: str
    destination: str | None = None  # NAVIGATE
    target_role: str | None = None
    target_name: str | None = None
    value: str | None = None  # FILL / SELECT


class ActionCompletedPayload(DomainModel):
    kind: Literal[EventType.ACTION_COMPLETED] = EventType.ACTION_COMPLETED
    dispatch_seq: int
    action_type: str
    url_after: str
    value: str | None = None  # READ result / FILL echo


class ActionFailedPayload(DomainModel):
    kind: Literal[EventType.ACTION_FAILED] = EventType.ACTION_FAILED
    dispatch_seq: int
    action_type: str
    error_type: str
    error_message: str


class BusinessOutcomePayload(DomainModel):
    kind: Literal[EventType.BUSINESS_OUTCOME] = EventType.BUSINESS_OUTCOME
    code: str
    description: str


class RunTerminalPayload(DomainModel):
    kind: Literal[EventType.RUN_COMPLETED, EventType.RUN_FAILED]
    status: str
    outputs: dict[str, str | int | bool] = {}  # authoritative only on SUCCESS; Decimal as text
    outcome: OutcomeSummary | None = None
    failure: FailureSummary | None = None
    steps: list[StepSummary] = []
    dispatched_actions: int


Payload = Annotated[
    RunStartedPayload
    | GateDecisionPayload
    | ActionDispatchedPayload
    | ActionCompletedPayload
    | ActionFailedPayload
    | BusinessOutcomePayload
    | RunTerminalPayload,
    Field(discriminator="kind"),
]

PAYLOAD_MODELS: tuple[type[DomainModel], ...] = (
    RunStartedPayload,
    GateDecisionPayload,
    ActionDispatchedPayload,
    ActionCompletedPayload,
    ActionFailedPayload,
    BusinessOutcomePayload,
    RunTerminalPayload,
    TargetSummary,
    ObservationSummary,
    OutcomeSummary,
    FailureSummary,
    StepSummary,
)


# --- envelope --------------------------------------------------------------------------------


class EvidenceEvent(DomainModel):
    schema_version: Literal["1.0"] = EVIDENCE_SCHEMA_VERSION
    event_id: str
    seq: int = Field(ge=1)  # strictly increasing within a run: the chronology key
    ts: datetime  # wall-clock UTC; the replay Clock is monotonic and never used here
    run_id: str
    run_kind: RunKind
    session_id: str
    event_type: EventType
    severity: Severity
    artifact_id: str | None = None
    step_id: str | None = None
    step_index: int | None = None  # 1-based position in the artifact's steps
    payload: Payload
    redaction_applied: bool = False  # the writer persists only True

    @model_validator(mode="after")
    def _consistent(self) -> EvidenceEvent:
        if self.payload.kind != self.event_type:
            raise ValueError(
                f"payload kind {self.payload.kind} does not match event_type {self.event_type}"
            )
        if self.ts.tzinfo is None:
            raise ValueError("ts must be timezone-aware")
        return self
