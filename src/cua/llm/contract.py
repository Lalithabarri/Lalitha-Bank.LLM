"""The provider-neutral model boundary (ARCHITECTURE §3, §5; D08; D24).

``LLMClient.decide(DiscoveryRequest) -> DecisionReply`` is the whole contract. Everything the
model sees is a ``DiscoveryRequest``; everything it may answer is a ``DiscoveryDecision`` — a
closed, discriminated vocabulary (``ACT`` | ``FINISH`` | ``REPORT_BLOCKED``) with no field that
could carry a selector, a piece of code, a risk tier, a policy or a whole plan. The reply carries
safe call provenance (provider, model id, response id, token counts, latency) — never the prompt,
never the completion, never a provider object.

What the model sees is the **model-safe** ``Observation``: derived from the current
``SurfaceSnapshot`` with every known bound input value replaced by ``<input:<name>>`` (the same
placeholder evidence persists), refs kept because the model needs them for this one turn. The raw
snapshot stays local to the discovery loop. Bound input *values* never appear in a request.

Decisions are proposals. Deterministic application code validates each one against the exact
observation it answers, the policy gate authorizes it, and only the gate touches the surface.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Protocol

from pydantic import Field, field_validator

from cua.artifact import ValueBinding
from cua.domain import ActionType, DomainModel

# --- errors ------------------------------------------------------------------------------------


class LLMError(Exception):
    """Base class for model-boundary failures."""


class ProviderErrorKind(StrEnum):
    CONFIG = "CONFIG"  # no credential / bad configuration; raised before any network call
    AUTH = "AUTH"
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    CONNECTION = "CONNECTION"
    SERVER = "SERVER"
    REFUSAL = "REFUSAL"  # the model declined to produce a structured decision
    INCOMPLETE = "INCOMPLETE"  # the response was cut off (token limit) before a decision existed
    OTHER = "OTHER"


class ProviderError(LLMError):
    """The provider could not deliver a decision. Fixed-template message: never the request, the
    response body, or a credential. The original exception is chained in memory only."""

    def __init__(
        self,
        kind: ProviderErrorKind,
        *,
        status_code: int | None = None,
        provider_request_id: str | None = None,
        detail: str = "",
    ) -> None:
        self.kind = ProviderErrorKind(kind)
        self.status_code = status_code
        self.provider_request_id = provider_request_id
        self.detail = detail
        message = f"provider call failed: {self.kind.value}"
        if status_code is not None:
            message += f" (HTTP {status_code})"
        if detail:
            message += f": {detail}"
        super().__init__(message)


class ModelOutputError(LLMError):
    """The provider answered, but not with a usable decision (schema-valid yet inconsistent, or
    empty). Counts against the single corrective retry; ``feedback`` is what the model is told."""

    def __init__(self, feedback: str) -> None:
        super().__init__(feedback)
        self.feedback = feedback


# --- request -----------------------------------------------------------------------------------


class DeclaredInput(DomainModel):
    name: str
    type: str  # TransformType value
    description: str = ""
    placeholder: str  # the token that marks this input's value in observed content


class DeclaredOutput(DomainModel):
    name: str
    type: str
    description: str = ""


class GoalStatement(DomainModel):
    name: str
    description: str
    inputs: list[DeclaredInput]
    outputs: list[DeclaredOutput]


class ObservedElement(DomainModel):
    ref: str
    role: str
    accessible_name: str
    value: str | None = None
    enabled: bool = True
    tag_hint: str | None = None
    context_hint: str | None = None


class Observation(DomainModel):
    """The model-safe view of one ``SurfaceSnapshot``. Refs are valid for this turn only."""

    observation_index: int
    url: str
    page_title: str
    elements: list[ObservedElement]
    visible_text_outline: str
    truncated: bool = False


class HistoryTarget(DomainModel):
    role: str
    accessible_name: str | None = None
    context_hint: str | None = None


class HistoryEntry(DomainModel):
    """One earlier dispatched step, in semantic terms (model-safe)."""

    step_index: int
    action_type: str
    target: HistoryTarget | None = None
    value_binding: str | None = None  # "INPUT_REF(name)" | "LITERAL(text)"
    route: str | None = None
    url_after: str
    read_text: str | None = None
    output_name: str | None = None


class DiscoveryRequest(DomainModel):
    goal: GoalStatement
    inputs_masked: bool = True  # False only on an explicitly labelled DIAGNOSTIC run
    input_values: dict[str, str] = {}  # populated only when inputs_masked is False
    observation: Observation
    history: list[HistoryEntry] = []
    step_index: int
    steps_remaining: int
    seconds_remaining: float | None = None
    navigation_routes: list[str]  # the narrow entry-route list; the gate stays authoritative
    feedback: str | None = None  # validation feedback on the one corrective retry


# --- decision ----------------------------------------------------------------------------------


class DecisionKind(StrEnum):
    ACT = "ACT"
    FINISH = "FINISH"
    REPORT_BLOCKED = "REPORT_BLOCKED"


class BlockedReason(StrEnum):
    BUSINESS_OUTCOME = "BUSINESS_OUTCOME"  # the UI reports a legitimate business result
    UI_AMBIGUOUS = "UI_AMBIGUOUS"
    CONTROL_MISSING = "CONTROL_MISSING"
    UNEXPECTED_STATE = "UNEXPECTED_STATE"
    OTHER = "OTHER"


# The action types a decision may propose. WAIT (D08) is not needed by this target and is not
# admitted; adding it is a deliberate vocabulary change, not a default.
ACT_ACTION_TYPES: frozenset[ActionType] = frozenset(
    {ActionType.NAVIGATE, ActionType.CLICK, ActionType.FILL, ActionType.SELECT, ActionType.READ}
)

MAX_INTENT_SUMMARY_LEN = 200

# Belt and braces on top of the structural fact that no field can hold code or a selector: an
# ``intent_summary`` carrying selector syntax or code is rejected as a whole.
FORBIDDEN_INTENT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"(?<!:)//",  # XPath / comment syntax (``http://`` is allowed)
        r"\bcss=",
        r"\bxpath=",
        r"\btext=",
        r"\b(?:document|window|page)\.[A-Za-z_]",
        r"\blocator\(",
        r"querySelector",
        r"\bgetBy[A-Za-z]+\(",
        r"\beval\(",
        r"\bexec\(",
        r"<script",
        r"\bimport\s",
        r"\bdef\s",
        r"\blambda\b",
        r"`",
        r"\$\(",
        r"\bsubprocess\b",
        r"\bos\.system\b",
        r"\brm\s+-",
    )
)


def _check_intent_summary(text: str) -> str:
    if not text.strip():
        raise ValueError("intent_summary must not be empty")
    if len(text) > MAX_INTENT_SUMMARY_LEN:
        raise ValueError(f"intent_summary longer than {MAX_INTENT_SUMMARY_LEN} characters")
    if any(ord(c) < 32 and c not in " " for c in text):
        raise ValueError("intent_summary must be a single line of plain text")
    for pattern in FORBIDDEN_INTENT_PATTERNS:
        if pattern.search(text):
            raise ValueError("intent_summary must not contain selector or code syntax")
    return text.strip()


class ActDecision(DomainModel):
    kind: Literal[DecisionKind.ACT] = DecisionKind.ACT
    observation_index: int  # must echo the observation this decision answers
    action_type: ActionType
    ref: str | None = None  # an element of the current observation
    value: ValueBinding | None = None  # FILL / SELECT: INPUT_REF or LITERAL (D12)
    route: str | None = None  # NAVIGATE: one of the request's navigation_routes
    output_name: str | None = None  # READ: the declared output this read populates
    intent_summary: str

    @field_validator("action_type")
    @classmethod
    def _proposable(cls, value: ActionType) -> ActionType:
        if value not in ACT_ACTION_TYPES:
            raise ValueError(f"{value.value} cannot be proposed as an action")
        return value

    @field_validator("intent_summary")
    @classmethod
    def _intent(cls, value: str) -> str:
        return _check_intent_summary(value)


class FinishDecision(DomainModel):
    kind: Literal[DecisionKind.FINISH] = DecisionKind.FINISH
    observation_index: int
    intent_summary: str

    @field_validator("intent_summary")
    @classmethod
    def _intent(cls, value: str) -> str:
        return _check_intent_summary(value)


class BlockedDecision(DomainModel):
    kind: Literal[DecisionKind.REPORT_BLOCKED] = DecisionKind.REPORT_BLOCKED
    observation_index: int
    reason_code: BlockedReason
    evidence_ref: str | None = None  # an element of the current observation evidencing the block
    intent_summary: str

    @field_validator("intent_summary")
    @classmethod
    def _intent(cls, value: str) -> str:
        return _check_intent_summary(value)


DiscoveryDecision = Annotated[
    ActDecision | FinishDecision | BlockedDecision, Field(discriminator="kind")
]


# --- reply and client --------------------------------------------------------------------------


class CallMetadata(DomainModel):
    """Safe provenance of one provider call. No prompt, no completion, no provider object."""

    provider: str
    model_id_requested: str
    model_id_reported: str | None = None
    provider_response_id: str | None = None
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class DecisionReply(DomainModel):
    decision: DiscoveryDecision
    call: CallMetadata


class LLMClient(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model_id(self) -> str: ...

    def decide(self, request: DiscoveryRequest) -> DecisionReply:
        """One decision for one observation. Raises ``ProviderError`` or ``ModelOutputError``."""
        ...
