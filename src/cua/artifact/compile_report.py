"""Compile result vocabulary: the typed report a successful compilation carries, and the closed
error vocabulary a failed one carries (ARCHITECTURE §15 step 13: ``compile_report.json``).

The report makes reviewer-visible *which* invariant was checked and *what evidence* satisfied it,
step by step, so a generated artifact can be audited without re-running the compiler. It holds
no model text, no observed values and no runtime inputs — only structural facts and the
derivation rule applied at each site.

A compilation either yields ``CompileSuccess`` (a schema-valid artifact plus its report) or
``CompileFailure`` (one or more deterministic ``CompileError``s). Nothing in between: a partially
valid artifact is never constructed (mirrors ``ValidAct | Invalid`` in the decision validator).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from cua.artifact.schema import CapabilityArtifact, Condition
from cua.domain import DomainModel


class CompileErrorCode(StrEnum):
    # the record
    TRACE_NOT_SUCCESSFUL = "TRACE_NOT_SUCCESSFUL"
    TRACE_EMPTY = "TRACE_EMPTY"
    DECLARATION_MISMATCH = "DECLARATION_MISMATCH"
    # steps (I3)
    STEP_ORDER_INVALID = "STEP_ORDER_INVALID"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"
    STEP_NOT_DISPATCHED = "STEP_NOT_DISPATCHED"
    ROUTE_MISSING = "ROUTE_MISSING"
    ROUTE_INVALID = "ROUTE_INVALID"
    # targets (I1, I4)
    TARGET_MISSING = "TARGET_MISSING"
    TARGET_IDENTITY_UNPROVEN = "TARGET_IDENTITY_UNPROVEN"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    TRANSIENT_REF = "TRANSIENT_REF"
    SELECTOR_SYNTAX = "SELECTOR_SYNTAX"
    READ_VALUE_IN_TARGET = "READ_VALUE_IN_TARGET"
    # bindings and inputs (I2)
    BINDING_MISSING = "BINDING_MISSING"
    BINDING_INVALID = "BINDING_INVALID"
    INPUT_UNDECLARED = "INPUT_UNDECLARED"
    INPUT_NOT_PARAMETERIZED = "INPUT_NOT_PARAMETERIZED"
    TRACE_NOT_TEMPLATED = "TRACE_NOT_TEMPLATED"
    # outputs and checkpoint (I5)
    OUTPUT_UNDECLARED = "OUTPUT_UNDECLARED"
    OUTPUT_DUPLICATE_PRODUCER = "OUTPUT_DUPLICATE_PRODUCER"
    OUTPUT_NOT_PRODUCED = "OUTPUT_NOT_PRODUCED"
    OUTPUT_TRANSFORM_INCONSISTENT = "OUTPUT_TRANSFORM_INCONSISTENT"
    SUCCESS_SEMANTICS_MISSING = "SUCCESS_SEMANTICS_MISSING"
    SUCCESS_SEMANTICS_UNDERIVABLE = "SUCCESS_SEMANTICS_UNDERIVABLE"
    # risk and outcomes
    RISK_INVALID = "RISK_INVALID"
    IRREVERSIBLE_STEP = "IRREVERSIBLE_STEP"
    KNOWN_OUTCOME_INVALID = "KNOWN_OUTCOME_INVALID"
    # defence in depth: the assembled artifact failed schema validation
    SCHEMA_REJECTED = "SCHEMA_REJECTED"


class CompileError(DomainModel):
    code: CompileErrorCode
    message: str
    step_index: int | None = None
    field: str | None = None


InvariantId = Literal["I1", "I2", "I3", "I4", "I5", "I6"]


class InvariantCheck(DomainModel):
    invariant: InvariantId
    statement: str
    evidence: list[str] = []


class InputMapping(DomainModel):
    type: str
    required: bool
    action_sites: list[str]  # step ids where the input drives an action
    observation_sites: list[str]  # where it appears in postconditions / checkpoint / detectors


class OutputMapping(DomainModel):
    type: str
    producer_step_id: str
    read_text_converts_under_transform: bool


class StepMapping(DomainModel):
    step_index: int
    step_id: str
    action: str
    target_rule: str | None = None
    target_identity_matches: int | None = None
    binding_rule: str | None = None
    postcondition_rule: str | None = None
    route_changed: bool | None = None
    risk: str
    risk_source: str


class CheckpointDerivation(DomainModel):
    rule: str
    conditions: list[Condition]
    parameterized_by: list[str]
    evidence_kind: str | None = None


class KnownOutcomeMapping(DomainModel):
    code: str
    source: Literal["DECLARED"]


class CompileReport(DomainModel):
    compiler_version: str
    compiled_at: datetime
    discovery_run_id: str
    provider: str
    model_id: str
    stop_reason: str
    capability_name: str
    capability_version: str
    artifact_id: str
    inputs: dict[str, InputMapping]
    outputs: dict[str, OutputMapping]
    steps: list[StepMapping] = Field(min_length=1)
    success_checkpoint: CheckpointDerivation
    known_outcomes: list[KnownOutcomeMapping]
    invariants: list[InvariantCheck] = Field(min_length=6)
    unused_trace_fields: list[str]


@dataclass(frozen=True)
class CompileSuccess:
    artifact: CapabilityArtifact
    report: CompileReport


@dataclass(frozen=True)
class CompileFailure:
    errors: tuple[CompileError, ...]

    def __post_init__(self) -> None:
        if not self.errors:
            raise ValueError("a compile failure carries at least one error")

    @property
    def codes(self) -> tuple[CompileErrorCode, ...]:
        return tuple(error.code for error in self.errors)


CompileResult = CompileSuccess | CompileFailure
