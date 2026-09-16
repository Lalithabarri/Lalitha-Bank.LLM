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

Vocabulary 1.1 (Milestone 6) adds ``RunKind.DISCOVERY`` and the minimum a discovery run needs to
prove that a real model drove a live surface under policy::

    DISCOVERY_STARTED  OBSERVATION  MODEL_CALL  DISCOVERY_ENDED

``OBSERVATION`` is a summary of what the loop observed (never the element list, never a ref).
``MODEL_CALL`` is written once per provider HTTP attempt — after the application validator has
judged the decision and *before* anything reaches the gate — and carries only safe metadata:
provider, model id, response id, token counts, the decision in semantic terms, the validation
status and the corrective-retry count. Prompts, completions, provider response objects and hidden
reasoning are never persisted. ``DISCOVERY_ENDED`` is the terminal event for every stop reason and
carries the normalized trace, which is literal-free by construction (templated at discovery time).
The gate and the surface emit the same ``GATE_DECISION`` / ``ACTION_*`` events for both run kinds.

Structural invariant (I1 for evidence): no payload model has a field that could hold a transient
observation ref, a ``SurfaceElement``, a ``SurfaceSnapshot`` or a driver object. Observed elements
are summarised as ``TargetSummary`` (role, name, context, value). A test asserts the field names.

Adding an event type or a ``RunKind`` is a ``schema_version`` bump: readers validate strictly, and
every earlier version stays readable under its own number.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from cua.domain import DomainModel

EVIDENCE_SCHEMA_VERSION = "1.2"
SUPPORTED_SCHEMA_VERSIONS: tuple[str, ...] = ("1.0", "1.1", "1.2")


class RunKind(StrEnum):
    REPLAY = "REPLAY"
    DISCOVERY = "DISCOVERY"


class EventType(StrEnum):
    RUN_STARTED = "RUN_STARTED"
    GATE_DECISION = "GATE_DECISION"
    ACTION_DISPATCHED = "ACTION_DISPATCHED"
    ACTION_COMPLETED = "ACTION_COMPLETED"
    ACTION_FAILED = "ACTION_FAILED"
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"
    RUN_COMPLETED = "RUN_COMPLETED"
    RUN_FAILED = "RUN_FAILED"
    # 1.1 — discovery
    DISCOVERY_STARTED = "DISCOVERY_STARTED"
    OBSERVATION = "OBSERVATION"
    MODEL_CALL = "MODEL_CALL"
    DISCOVERY_ENDED = "DISCOVERY_ENDED"
    # 1.2 — human intervention (Milestone 8)
    INTERVENTION_REQUESTED = "INTERVENTION_REQUESTED"
    CONTROL_TRANSFERRED = "CONTROL_TRANSFERRED"
    HAND_BACK = "HAND_BACK"
    INTERVENTION_VERIFIED = "INTERVENTION_VERIFIED"


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
    safe_to_retry: bool | None = None  # 1.2: False after an irreversible step of unknown state


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
    completed_by: str | None = None  # 1.2: "HUMAN" when a person completed the step


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


# --- 1.1: discovery payloads -------------------------------------------------------------------


class DiscoveryStartedPayload(DomainModel):
    kind: Literal[EventType.DISCOVERY_STARTED] = EventType.DISCOVERY_STARTED
    run_kind: RunKind
    goal_name: str
    inputs_declared: list[str]  # names only — values are never persisted
    outputs_declared: list[str]
    provider: str
    model_id: str
    base_url: str  # origin only
    max_steps: int
    timeout_s: float
    model_navigation_routes: list[str]  # the narrow entry-route list shown to the model
    mask_bound_inputs: bool  # False marks a DIAGNOSTIC run that can never be an official E01


class ObservationPayload(DomainModel):
    """What the loop observed before deciding. No elements, no refs; the digest is what the
    no-progress rule compared."""

    kind: Literal[EventType.OBSERVATION] = EventType.OBSERVATION
    observation_index: int
    url: str
    page_title: str
    element_count: int
    truncated: bool
    outline_excerpt: str
    digest: str


class SemanticTargetSummary(DomainModel):
    """A durable target in semantic terms: ``accessible_name`` is absent for content roles
    (cell / rowheader / columnheader) whose name *is* the value being read."""

    role: str
    accessible_name: str | None = None
    context_hint: str | None = None


class DecisionSummary(DomainModel):
    kind: str  # ACT | FINISH | REPORT_BLOCKED
    action_type: str | None = None
    target: TargetSummary | None = None
    value_binding_kind: str | None = None  # LITERAL | INPUT_REF
    input_name: str | None = None
    route_template: str | None = None
    output_name: str | None = None
    blocked_reason: str | None = None
    intent_summary: str  # short audit description, redacted before persistence


class ValidationSummary(DomainModel):
    status: str  # VALID | INVALID
    code: str | None = None
    feedback: str | None = None


class ModelCallPayload(DomainModel):
    """One provider HTTP attempt. Never the prompt, the completion or the response object."""

    kind: Literal[EventType.MODEL_CALL] = EventType.MODEL_CALL
    call_index: int
    step_index: int
    purpose: str  # DECIDE | CORRECTIVE_RETRY
    provider: str
    model_id_requested: str
    model_id_reported: str | None = None
    provider_response_id: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    outcome: str  # DECISION | PROVIDER_ERROR | OUTPUT_ERROR
    decision: DecisionSummary | None = None
    validation: ValidationSummary | None = None
    corrective_retry_count: int
    error_kind: str | None = None
    status_code: int | None = None


class TraceStepSummary(DomainModel):
    """Mirror of ``cua.discovery.trace.TraceStep`` (evidence never imports discovery)."""

    step_index: int
    action_type: str
    target: SemanticTargetSummary | None = None
    identity_matches: int | None = None
    value_binding_kind: str | None = None
    input_name: str | None = None
    literal_value: str | None = None
    route_template: str | None = None
    output_name: str | None = None
    read_text: str | None = None
    input_evidence: dict[str, str] = {}
    intent_summary: str
    gate_decision: str
    effective_risk: str
    url_before_template: str
    url_after_template: str
    page_title_after_template: str | None = None


class OutcomeCandidateSummary(DomainModel):
    reason_code: str
    target: SemanticTargetSummary | None = None
    text_template: str | None = None


class TraceSummary(DomainModel):
    """Mirror of ``cua.discovery.trace.NormalizedTrace``."""

    goal_name: str
    inputs_declared: dict[str, str]
    outputs_declared: dict[str, str]
    steps: list[TraceStepSummary]
    outcome_candidate: OutcomeCandidateSummary | None = None
    provider: str
    model_id: str
    discovery_run_id: str
    session_id: str


class StopDetailSummary(DomainModel):
    code: str
    message: str
    gate_decision: str | None = None
    deny_reason: str | None = None
    validation_codes: list[str] = []
    provider_error_kind: str | None = None


class DiscoveryEndedPayload(DomainModel):
    kind: Literal[EventType.DISCOVERY_ENDED] = EventType.DISCOVERY_ENDED
    stop_reason: str
    detail: StopDetailSummary
    goal_satisfied: bool
    verdict_reason: str | None = None
    outputs: dict[str, str | int | bool] = {}  # authoritative only on GOAL_REACHED; Decimal as text
    steps_dispatched: int
    dispatched_actions: int
    model_calls: int
    corrective_retries: int
    trace: TraceSummary


# --- 1.2: human intervention -----------------------------------------------------------------

_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_./-]*$")


class ArtifactRef(DomainModel):
    """A safe reference to a binary evidence artifact stored beside ``events.jsonl``.

    Only a run-relative path, a digest, the media type and the size: never bytes, never an
    absolute filesystem path, never a browser object.
    """

    path: str  # relative to the run directory, e.g. "artifacts/intervention_<id>_pre.png"
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    media_type: str
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _relative(cls, value: str) -> str:
        if not _RELATIVE_PATH.match(value) or ".." in value.split("/"):
            raise ValueError(f"artifact path must be a safe run-relative path, got {value!r}")
        return value


class InterventionRequestedPayload(DomainModel):
    """Mirror of ``cua.hitl.InterventionRequest`` plus the PRE evidence taken before any human
    control was granted (evidence never imports hitl)."""

    kind: Literal[EventType.INTERVENTION_REQUESTED] = EventType.INTERVENTION_REQUESTED
    request_id: str
    reason: str
    risk: str
    requested_action_summary: str
    target: TargetSummary | None = None
    verification_requirement: str
    owner_before: str
    owner_after: str
    pre_observation_digest: str
    pre_screenshot: ArtifactRef | None = None


class ControlTransferredPayload(DomainModel):
    kind: Literal[EventType.CONTROL_TRANSFERRED] = EventType.CONTROL_TRANSFERRED
    request_id: str
    from_owner: str
    to_owner: str
    trigger: str  # escalate | accept | hand_back | restore


class HandBackPayload(DomainModel):
    kind: Literal[EventType.HAND_BACK] = EventType.HAND_BACK
    request_id: str
    hand_back: str  # DONE | ABORT — a signal to verify, never a claim about the transaction
    note: str = ""  # redacted before persistence


class InterventionVerifiedPayload(DomainModel):
    """The human-action record (ARCHITECTURE §9): what deterministic verification established
    about the state the human left behind, with the pre/post digests and captures. The
    run/session/artifact/step identity is the envelope's."""

    kind: Literal[EventType.INTERVENTION_VERIFIED] = EventType.INTERVENTION_VERIFIED
    request_id: str
    hand_back: str
    outcome: str  # VERIFIED_COMPLETED | UNKNOWN_COMMIT_STATE
    completed_by: str | None = None  # "HUMAN" when VERIFIED_COMPLETED
    observations: int  # observations taken by the bounded verification
    pre_observation_digest: str
    post_observation_digest: str
    pre_screenshot: ArtifactRef | None = None
    post_screenshot: ArtifactRef | None = None
    evidence_note: str | None = None  # e.g. a post-capture failure, when it could be recorded


Payload = Annotated[
    RunStartedPayload
    | GateDecisionPayload
    | ActionDispatchedPayload
    | ActionCompletedPayload
    | ActionFailedPayload
    | BusinessOutcomePayload
    | RunTerminalPayload
    | DiscoveryStartedPayload
    | ObservationPayload
    | ModelCallPayload
    | DiscoveryEndedPayload
    | InterventionRequestedPayload
    | ControlTransferredPayload
    | HandBackPayload
    | InterventionVerifiedPayload,
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
    DiscoveryStartedPayload,
    ObservationPayload,
    SemanticTargetSummary,
    DecisionSummary,
    ValidationSummary,
    ModelCallPayload,
    TraceStepSummary,
    OutcomeCandidateSummary,
    TraceSummary,
    StopDetailSummary,
    DiscoveryEndedPayload,
    ArtifactRef,
    InterventionRequestedPayload,
    ControlTransferredPayload,
    HandBackPayload,
    InterventionVerifiedPayload,
)


# --- envelope --------------------------------------------------------------------------------


class EvidenceEvent(DomainModel):
    schema_version: Literal["1.0", "1.1", "1.2"] = EVIDENCE_SCHEMA_VERSION
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
