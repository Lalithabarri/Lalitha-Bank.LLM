"""CapabilityArtifact — the typed, versioned, parameterized capability (ARCHITECTURE §6, D11–D14).

The artifact is data, never code: no eval, no generated code, no CSS/XPath, no driver objects,
no transient observation refs (I1), no secrets or approvals (I6). Targets are ordered semantic
strategies (D13). Values are explicit ``LITERAL`` or ``INPUT_REF`` bindings (D12). Every
mutating/navigation step carries a declarative postcondition; READ steps declare their output;
the artifact carries a mandatory final checkpoint and declared business outcomes (D14).

``schema_version`` is the format; ``capability_version`` is the capability's own semver — they
evolve independently (D11).

Approved schema extensions beyond the frozen text (recorded in PROJECT_STATUS.md):
* ``Provenance.source`` records whether an artifact was handwritten or discovered, so a
  handwritten fixture can never be mistaken for genuine discovery output in evidence.
* ``success_checkpoint`` is a non-empty **list** of conditions with **ALL** semantics: every
  condition must hold, evaluated in declared order, short-circuiting on the first that does
  not. ARCHITECTURE names a singular checkpoint; the four-condition vocabulary has no
  conjunction form, and a meaningful checkpoint for this flow needs route *and* heading.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from cua.domain import ActionType, DomainModel
from cua.policy.risk import RiskTier

# --- closed vocabularies -------------------------------------------------------------------------


class TransformType(StrEnum):
    STRING = "STRING"
    DECIMAL = "DECIMAL"
    INTEGER = "INTEGER"
    BOOLEAN = "BOOLEAN"


class ValueKind(StrEnum):
    LITERAL = "LITERAL"
    INPUT_REF = "INPUT_REF"


class ConditionType(StrEnum):
    ROUTE_MATCHES = "route_matches"
    ELEMENT_PRESENT = "element_present"
    TEXT_PRESENT = "text_present"
    VALUE_EQUALS = "value_equals"


class ProvenanceSource(StrEnum):
    HANDWRITTEN = "handwritten"
    DISCOVERY = "discovery"


# ARCHITECTURE §6: persisted actions only. WAIT is never persisted.
PERSISTED_ACTION_TYPES: frozenset[ActionType] = frozenset(
    {ActionType.NAVIGATE, ActionType.FILL, ActionType.SELECT, ActionType.CLICK, ActionType.READ}
)

# Belt-and-braces guards on top of the structural guarantee (no selector field exists at all).
# Deliberately narrow: only Playwright's own ref shape and unambiguous selector-engine syntax, so
# real accessible names such as ".NET", "/accounts" or "#1 priority" are not rejected.
_TRANSIENT_REF = re.compile(r"^(f\d+)?e\d+$")
_SELECTOR_PREFIXES = ("//", "css=", "xpath=", "text=")
_PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
_OUTCOME_CODE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _reject_non_semantic(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if _TRANSIENT_REF.match(value):
        raise ValueError(f"{field} {value!r} looks like a transient observation ref (I1)")
    if value.startswith(_SELECTOR_PREFIXES):
        raise ValueError(f"{field} {value!r} looks like a selector, not a semantic (I3)")
    return value


def placeholders(text: str) -> set[str]:
    return set(_PLACEHOLDER.findall(text))


# --- targets -------------------------------------------------------------------------------------


class TargetStrategy(DomainModel):
    """One semantic strategy; present fields are AND-ed. Strings may carry ``{input}`` anchors."""

    role: str | None = None
    name: str | None = None
    scope: str | None = None
    text_contains: str | None = None

    @field_validator("role", "name", "scope", "text_contains")
    @classmethod
    def _semantic_only(cls, value: str | None, info) -> str | None:
        return _reject_non_semantic(value, info.field_name)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> TargetStrategy:
        if not any((self.role, self.name, self.scope, self.text_contains)):
            raise ValueError("a target strategy must set at least one semantic field")
        return self

    def placeholders(self) -> set[str]:
        found: set[str] = set()
        for value in (self.role, self.name, self.scope, self.text_contains):
            if value:
                found |= placeholders(value)
        return found


class TargetDescriptor(DomainModel):
    strategies: list[TargetStrategy] = Field(min_length=1)

    def placeholders(self) -> set[str]:
        return set().union(*(s.placeholders() for s in self.strategies))


# --- values --------------------------------------------------------------------------------------


class LiteralValue(DomainModel):
    kind: Literal[ValueKind.LITERAL] = ValueKind.LITERAL
    value: str


class InputRef(DomainModel):
    kind: Literal[ValueKind.INPUT_REF] = ValueKind.INPUT_REF
    input_name: str = Field(pattern=_IDENT.pattern)


ValueBinding = Annotated[LiteralValue | InputRef, Field(discriminator="kind")]


# --- conditions ----------------------------------------------------------------------------------


class RouteMatches(DomainModel):
    type: Literal[ConditionType.ROUTE_MATCHES] = ConditionType.ROUTE_MATCHES
    route: str

    @field_validator("route")
    @classmethod
    def _path(cls, value: str) -> str:
        if not value.startswith("/") or "://" in value:
            raise ValueError("route must be a path starting with '/', not a full URL")
        return value


class ElementPresent(DomainModel):
    type: Literal[ConditionType.ELEMENT_PRESENT] = ConditionType.ELEMENT_PRESENT
    target: TargetDescriptor


class TextPresent(DomainModel):
    type: Literal[ConditionType.TEXT_PRESENT] = ConditionType.TEXT_PRESENT
    text: str = Field(min_length=1)


class ValueEquals(DomainModel):
    type: Literal[ConditionType.VALUE_EQUALS] = ConditionType.VALUE_EQUALS
    target: TargetDescriptor
    value: ValueBinding


Condition = Annotated[
    RouteMatches | ElementPresent | TextPresent | ValueEquals, Field(discriminator="type")
]


def _condition_placeholders(condition: Condition) -> set[str]:
    if isinstance(condition, RouteMatches):
        return placeholders(condition.route)
    if isinstance(condition, TextPresent):
        return placeholders(condition.text)
    found = condition.target.placeholders()
    if isinstance(condition, ValueEquals) and isinstance(condition.value, LiteralValue):
        found |= placeholders(condition.value.value)
    return found


def _condition_input_refs(condition: Condition) -> set[str]:
    if isinstance(condition, ValueEquals) and isinstance(condition.value, InputRef):
        return {condition.value.input_name}
    return set()


# --- steps ---------------------------------------------------------------------------------------


class InputSpec(DomainModel):
    type: TransformType
    required: bool = True
    description: str = ""


class OutputSpec(DomainModel):
    type: TransformType
    description: str = ""


class Step(DomainModel):
    step_id: str = Field(pattern=_IDENT.pattern)
    description: str
    action: ActionType
    route: str | None = None
    target: TargetDescriptor | None = None
    value: ValueBinding | None = None
    output: str | None = Field(default=None, pattern=_IDENT.pattern)
    risk: RiskTier
    postcondition: Condition | None = None

    @field_validator("action")
    @classmethod
    def _persisted_only(cls, value: ActionType) -> ActionType:
        if value not in PERSISTED_ACTION_TYPES:
            raise ValueError(f"{value.value} is not a persisted action type")
        return value

    @field_validator("route")
    @classmethod
    def _path(cls, value: str | None) -> str | None:
        if value is not None and (not value.startswith("/") or "://" in value):
            raise ValueError("route must be a path starting with '/', not a full URL")
        return value

    @model_validator(mode="after")
    def _shape_per_action(self) -> Step:
        is_navigate = self.action is ActionType.NAVIGATE
        needs_value = self.action in (ActionType.FILL, ActionType.SELECT)
        is_read = self.action is ActionType.READ

        if is_navigate:
            if self.route is None:
                raise ValueError("NAVIGATE requires route")
            if self.target is not None:
                raise ValueError("NAVIGATE must not carry a target")
        else:
            if self.route is not None:
                raise ValueError(f"{self.action.value} must not carry a route")
            if self.target is None:
                raise ValueError(f"{self.action.value} requires a target")

        if needs_value and self.value is None:
            raise ValueError(f"{self.action.value} requires a value binding")
        if not needs_value and self.value is not None:
            raise ValueError(f"{self.action.value} must not carry a value binding")

        if is_read:
            if self.output is None:
                raise ValueError("READ must declare its output")
            if self.postcondition is not None:
                raise ValueError("READ declares an extraction target, not a postcondition")
        else:
            if self.output is not None:
                raise ValueError(f"{self.action.value} must not declare an output")
            if self.postcondition is None:
                raise ValueError(f"{self.action.value} requires a postcondition")
        return self

    def placeholders(self) -> set[str]:
        found: set[str] = set()
        if self.route:
            found |= placeholders(self.route)
        if self.target:
            found |= self.target.placeholders()
        if isinstance(self.value, LiteralValue):
            found |= placeholders(self.value.value)
        if self.postcondition:
            found |= _condition_placeholders(self.postcondition)
        return found

    def input_refs(self) -> set[str]:
        found: set[str] = set()
        if isinstance(self.value, InputRef):
            found.add(self.value.input_name)
        if self.postcondition:
            found |= _condition_input_refs(self.postcondition)
        return found


# --- outcomes, provenance, artifact --------------------------------------------------------------


class KnownOutcome(DomainModel):
    code: str = Field(pattern=_OUTCOME_CODE.pattern)
    terminal_status: Literal["BUSINESS_OUTCOME"]
    detector: Condition
    description: str = ""


class Provenance(DomainModel):
    source: ProvenanceSource
    compiled_at: datetime
    discovery_run_id: str | None = None
    model_id: str | None = None
    compiler_version: str | None = None
    goal_summary: str | None = None


class CapabilityArtifact(DomainModel):
    artifact_id: str
    capability_name: str = Field(pattern=_IDENT.pattern)
    schema_version: Literal["1.0"]
    capability_version: str = Field(pattern=_SEMVER.pattern)
    description: str
    inputs: dict[str, InputSpec]
    outputs: dict[str, OutputSpec]
    steps: list[Step] = Field(min_length=1)
    success_checkpoint: list[Condition] = Field(min_length=1)  # ALL must hold, in order
    known_outcomes: list[KnownOutcome] = []
    provenance: Provenance

    @field_validator("inputs", "outputs")
    @classmethod
    def _identifier_keys(cls, value: dict, info) -> dict:
        for key in value:
            if not _IDENT.match(key):
                raise ValueError(f"{info.field_name} name {key!r} is not an identifier")
        return value

    @model_validator(mode="after")
    def _invariants(self) -> CapabilityArtifact:
        expected_id = f"{self.capability_name}@{self.capability_version}"
        if self.artifact_id != expected_id:
            raise ValueError(f"artifact_id must be {expected_id!r}")

        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step_id values must be unique")

        # I2: every reference names a declared input; every required input is actually used.
        declared = set(self.inputs)
        referenced: set[str] = set()
        for step in self.steps:
            referenced |= step.input_refs() | step.placeholders()
        for condition in self.success_checkpoint:
            referenced |= _condition_input_refs(condition) | _condition_placeholders(condition)
        for outcome in self.known_outcomes:
            referenced |= _condition_input_refs(outcome.detector)
            referenced |= _condition_placeholders(outcome.detector)
        undeclared = referenced - declared
        if undeclared:
            raise ValueError(f"references to undeclared inputs: {sorted(undeclared)}")
        unused = {name for name, spec in self.inputs.items() if spec.required} - referenced
        if unused:
            raise ValueError(
                f"required inputs never referenced (hard-coded value?): {sorted(unused)}"
            )

        # I4: outputs and READ steps correspond one-to-one.
        produced = [step.output for step in self.steps if step.action is ActionType.READ]
        if len(produced) != len(set(produced)):
            raise ValueError("an output may be produced by only one READ step")
        undeclared_outputs = set(produced) - set(self.outputs)
        if undeclared_outputs:
            raise ValueError(f"READ produces undeclared outputs: {sorted(undeclared_outputs)}")
        never_read = set(self.outputs) - set(produced)
        if never_read:
            raise ValueError(f"declared outputs never produced by a READ: {sorted(never_read)}")
        return self
