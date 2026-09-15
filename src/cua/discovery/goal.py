"""Goals and their deterministic verifiers.

A ``GoalSpec`` is what discovery starts from: a name, a description, typed inputs and outputs, and
a ``GoalVerifier``. The model may propose FINISH; only the verifier — deterministic code judging
the run's own trace — can establish ``GOAL_REACHED``. A verifier never consults the model, an
artifact, or an expected value.

``ReadSavingsBalanceVerifier`` is the flagship verifier. It is satisfied when the trace proves,
from what actually happened:

1. exactly one completed READ populating ``savings_balance``;
2. that READ targeted a ``cell`` in a row whose header is ``Savings`` and the ambiguity guard saw
   exactly one such candidate (``identity_matches == 1``);
3. the page the value was read from evidenced the requested member (the bound ``member_id``
   as a whole URL path segment or a whole token in a heading — recorded by the loop from the RAW
   observation as ``input_evidence``);
4. the observed text converts to ``Decimal`` under the same transform replay uses;
5. the result populates the declared output shape exactly.

No balance constant exists in this module or anywhere in ``src/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from cua.artifact import InputSpec, OutputSpec, TransformType
from cua.discovery.trace import NormalizedTrace, TraceStep
from cua.domain import ActionType, DomainModel
from cua.replay.transforms import TransformError, apply_transform, validate_output

OutputValue = Decimal | int | bool | str


class InputEvidence(StrEnum):
    ROUTE = "ROUTE"  # the value is a whole segment of the observation's URL path
    HEADING = "HEADING"  # the value is a whole token of a heading line in the outline


class GoalVerdict(DomainModel):
    satisfied: bool
    outputs: dict[str, OutputValue] = {}
    reason: str


class GoalVerifier(Protocol):
    def verify(self, trace: NormalizedTrace, bound_inputs: Mapping[str, str]) -> GoalVerdict: ...


@dataclass(frozen=True)
class GoalSpec:
    name: str
    description: str
    inputs: dict[str, InputSpec]
    outputs: dict[str, OutputSpec]
    verifier: GoalVerifier


def bind_goal_inputs(goal: GoalSpec, raw: Mapping[str, object]) -> dict[str, str]:
    """Validate runtime inputs against the goal's declared inputs (same rules as replay binding).

    Unknown names are rejected; required names must be present; every value must be non-empty
    text that parses under its declared type. A violation is a caller error (``ValueError``).
    """
    unknown = set(raw) - set(goal.inputs)
    if unknown:
        raise ValueError(f"unknown inputs: {sorted(unknown)}")
    bound: dict[str, str] = {}
    for name, spec in goal.inputs.items():
        if name not in raw:
            if spec.required:
                raise ValueError(f"missing required input {name!r}")
            continue
        value = raw[name]
        if not isinstance(value, str) or not value:
            raise ValueError(f"input {name!r} must be non-empty text")
        try:
            apply_transform(spec.type, value)
        except TransformError as exc:
            raise ValueError(f"input {name!r} is not a valid {spec.type.value}: {exc}") from exc
        bound[name] = value
    return bound


# --- flagship ------------------------------------------------------------------------------------

SAVINGS_OUTPUT = "savings_balance"
MEMBER_INPUT = "member_id"
SAVINGS_ROW_LABEL = "savings"


def _row_label(context_hint: str | None) -> str | None:
    """The row header named by a context hint such as ``table: Accounts > row: Savings``."""
    if not context_hint:
        return None
    for segment in context_hint.split(" > "):
        if segment.startswith("row: "):
            return segment[len("row: ") :]
    return None


def _reads_of(trace: NormalizedTrace, output_name: str) -> list[TraceStep]:
    return [
        s for s in trace.steps if s.action_type is ActionType.READ and s.output_name == output_name
    ]


class ReadSavingsBalanceVerifier:
    def verify(self, trace: NormalizedTrace, bound_inputs: Mapping[str, str]) -> GoalVerdict:
        reads = _reads_of(trace, SAVINGS_OUTPUT)
        if not reads:
            return GoalVerdict(
                satisfied=False,
                reason=f"no READ populating {SAVINGS_OUTPUT!r} has occurred yet",
            )
        if len(reads) > 1:
            return GoalVerdict(
                satisfied=False,
                reason=f"{len(reads)} READs populate {SAVINGS_OUTPUT!r}; exactly one is required",
            )
        read = reads[0]
        target = read.target
        if target is None or target.role != "cell":
            return GoalVerdict(
                satisfied=False,
                reason="the READ did not target a table cell",
            )
        label = _row_label(target.context_hint)
        if label is None or label.casefold() != SAVINGS_ROW_LABEL:
            return GoalVerdict(
                satisfied=False,
                reason=(
                    "the READ cell is not in a row headed 'Savings' "
                    f"(context {target.context_hint!r})"
                ),
            )
        if read.identity_matches != 1:
            return GoalVerdict(
                satisfied=False,
                reason=(
                    "the Savings cell was not identified unambiguously "
                    f"({read.identity_matches} candidates)"
                ),
            )
        if MEMBER_INPUT not in bound_inputs:
            return GoalVerdict(satisfied=False, reason=f"no bound input {MEMBER_INPUT!r}")
        if MEMBER_INPUT not in read.input_evidence:
            return GoalVerdict(
                satisfied=False,
                reason="the page the value was read from did not evidence the requested member",
            )
        if read.read_text is None:
            return GoalVerdict(satisfied=False, reason="the READ returned no text")
        try:
            value = apply_transform(TransformType.DECIMAL, read.read_text)
            validate_output(TransformType.DECIMAL, value)
        except TransformError as exc:
            return GoalVerdict(satisfied=False, reason=f"the read text is not a decimal: {exc}")
        outputs = {SAVINGS_OUTPUT: value}
        if (
            set(outputs) != set(trace.outputs_declared)
            or trace.outputs_declared.get(SAVINGS_OUTPUT) != TransformType.DECIMAL.value
        ):
            return GoalVerdict(
                satisfied=False,
                reason="the produced outputs do not match the declared output shape",
            )
        return GoalVerdict(
            satisfied=True,
            outputs=outputs,
            reason=(
                f"one READ of the unique Savings cell on the requested member's page "
                f"({read.input_evidence[MEMBER_INPUT]}) converted to DECIMAL"
            ),
        )


READ_SAVINGS_BALANCE_GOAL = GoalSpec(
    name="read_savings_balance",
    description=(
        "Find the member identified by member_id in the operations console and read the current "
        "balance of that member's Savings account."
    ),
    inputs={
        MEMBER_INPUT: InputSpec(
            type=TransformType.STRING,
            required=True,
            description="Member number, as printed on the account card",
        )
    },
    outputs={
        SAVINGS_OUTPUT: OutputSpec(
            type=TransformType.DECIMAL, description="Savings account balance in USD"
        )
    },
    verifier=ReadSavingsBalanceVerifier(),
)
