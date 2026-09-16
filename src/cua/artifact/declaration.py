"""CapabilityDeclaration — the declared contract the compiler combines with a discovered trace.

A discovery trace records what *happened*: ordered actions, verified semantic targets, bindings,
routes. It cannot record what the capability *promises*: input descriptions and required flags,
output descriptions, and the known business outcomes a replay must distinguish from failures
(D14: detector coverage is "what's declared at compile time"). Those are authored by the
capability's owner, deterministically, and handed to the compiler as this model — the
model-free projection of a ``GoalSpec`` (which additionally carries a verifier the compiler
must never call).

The compiler cross-checks every declared name and type against what the trace itself declared
(``inputs_declared`` / ``outputs_declared``); a disagreement is a compile failure, never a
silent preference for one side.
"""

from __future__ import annotations

import re

from pydantic import Field, field_validator, model_validator

from cua.artifact.schema import (
    InputSpec,
    KnownOutcome,
    OutputSpec,
    _condition_input_refs,
    _condition_placeholders,
)
from cua.domain import DomainModel

_IDENT = re.compile(r"^[a-z][a-z0-9_]*$")


class CapabilityDeclaration(DomainModel):
    name: str = Field(pattern=_IDENT.pattern)
    description: str = Field(min_length=1)
    inputs: dict[str, InputSpec]
    outputs: dict[str, OutputSpec] = Field(min_length=1)
    known_outcomes: list[KnownOutcome] = []

    @field_validator("inputs", "outputs")
    @classmethod
    def _identifier_keys(cls, value: dict, info) -> dict:
        for key in value:
            if not _IDENT.match(key):
                raise ValueError(f"{info.field_name} name {key!r} is not an identifier")
        return value

    @model_validator(mode="after")
    def _outcomes_are_consistent(self) -> CapabilityDeclaration:
        codes = [outcome.code for outcome in self.known_outcomes]
        if len(codes) != len(set(codes)):
            raise ValueError("known outcome codes must be unique")
        for outcome in self.known_outcomes:
            referenced = _condition_placeholders(outcome.detector) | _condition_input_refs(
                outcome.detector
            )
            unknown = referenced - set(self.inputs)
            if unknown:
                raise ValueError(
                    f"known outcome {outcome.code} references undeclared inputs: {sorted(unknown)}"
                )
        return self
