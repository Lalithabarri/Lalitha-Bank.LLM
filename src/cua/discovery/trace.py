"""The normalized trace — what discovery hands to the future ArtifactCompiler (ARCHITECTURE §5, §6).

Driver-neutral and literal-free by construction, at discovery time:

* a step's target is a ``SemanticTarget`` (role, accessible name, context) — never a ref; for
  content roles (cell / rowheader / columnheader) the name is dropped because it *is* the value;
* a FILL/SELECT value is recorded as the model's binding — ``INPUT_REF`` with the input name, or a
  ``LITERAL`` that the validator has already proven is not a bound input value;
* routes, observed URLs, titles, target text and outcome text are templated with ``{name}`` by the
  whole-segment / whole-token rule (``cua.discovery.templating``), so a redactor finds nothing to
  rewrite and the persisted trace equals the in-memory trace.

Only dispatched, completed actions become steps; a rejected decision, a policy denial or a
surface failure ends the run and is described by the ``DiscoveryResult``, not by a step.
No model in this module has a field that could hold a transient ref, an element or a snapshot.
"""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from cua.artifact import PERSISTED_ACTION_TYPES
from cua.domain import ActionType, DomainModel

_TRANSIENT_REF = re.compile(r"^(f\d+)?e\d+$")


def _no_ref(value: str | None, field: str) -> str | None:
    if value is not None and _TRANSIENT_REF.match(value):
        raise ValueError(f"{field} {value!r} looks like a transient observation ref (I1)")
    return value


class SemanticTarget(DomainModel):
    """A durable target in semantic terms. ``accessible_name`` is ``None`` for content roles."""

    role: str
    accessible_name: str | None = None
    context_hint: str | None = None

    @field_validator("role", "accessible_name", "context_hint")
    @classmethod
    def _semantic(cls, value: str | None, info) -> str | None:
        return _no_ref(value, info.field_name)


class OutcomeCandidate(DomainModel):
    """What the model pointed at when it reported a block (e.g. the not-found alert)."""

    reason_code: str
    target: SemanticTarget | None = None
    text_template: str | None = None


class TraceStep(DomainModel):
    step_index: int = Field(ge=1)
    action_type: ActionType
    target: SemanticTarget | None = None
    identity_matches: int | None = None  # 1 by the ambiguity guard for every ref-targeted step
    value_binding_kind: str | None = None  # ValueKind value: LITERAL | INPUT_REF
    input_name: str | None = None
    literal_value: str | None = None
    route_template: str | None = None  # NAVIGATE
    output_name: str | None = None  # READ
    read_text: str | None = None  # READ: the observed text (templated)
    input_evidence: dict[str, str] = {}  # input name -> how the page evidenced it (ROUTE|HEADING)
    intent_summary: str
    gate_decision: str
    effective_risk: str
    url_before_template: str
    url_after_template: str
    page_title_after_template: str | None = None  # known only once the next observation exists

    @field_validator("action_type")
    @classmethod
    def _persisted_only(cls, value: ActionType) -> ActionType:
        if value not in PERSISTED_ACTION_TYPES:
            raise ValueError(f"{value.value} is not a persisted action type")
        return value


class NormalizedTrace(DomainModel):
    goal_name: str
    inputs_declared: dict[str, str]  # name -> TransformType value
    outputs_declared: dict[str, str]
    steps: list[TraceStep] = []
    outcome_candidate: OutcomeCandidate | None = None
    provider: str
    model_id: str
    discovery_run_id: str
    session_id: str
