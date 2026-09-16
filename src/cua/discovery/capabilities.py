"""Declared capability contracts — what the compiler combines with a discovered trace.

A ``GoalSpec`` says what discovery must achieve; a ``CapabilityDeclaration`` is its model-free
projection (name, description, typed inputs and outputs) plus the **declared** known business
outcomes a replay must distinguish from failures (D14: detector coverage is what is declared at
compile time). A successful discovery trace can never evidence a business outcome by
construction — an outcome ends discovery as ``REPORT_BLOCKED``, never ``GOAL_REACHED`` — so the
outcome is authored here from observed UI semantics (the Milestone 2 not-found capture) and is
verified by replay, not by discovery. The compiler labels it ``DECLARED``, never ``DISCOVERED``.
Trace-learned outcomes (an outcome-discovery run merged through ``OutcomeCandidate``) are a
later extension; nothing here reads the handwritten artifact.
"""

from __future__ import annotations

from collections.abc import Sequence

from cua.artifact.declaration import CapabilityDeclaration
from cua.artifact.schema import ElementPresent, KnownOutcome, TargetDescriptor, TargetStrategy
from cua.discovery.goal import READ_SAVINGS_BALANCE_GOAL, GoalSpec


def capability_declaration(
    goal: GoalSpec, *, known_outcomes: Sequence[KnownOutcome] = ()
) -> CapabilityDeclaration:
    """The declaration for ``goal``: same name, description, inputs and outputs; no verifier."""
    return CapabilityDeclaration(
        name=goal.name,
        description=goal.description,
        inputs=dict(goal.inputs),
        outputs=dict(goal.outputs),
        known_outcomes=list(known_outcomes),
    )


# Observed on the Milestone 2 not-found capture: the search screen renders an ``alert`` whose
# text names the requested id. Declared, parameterized by the runtime input, verified by replay.
MEMBER_NOT_FOUND = KnownOutcome(
    code="MEMBER_NOT_FOUND",
    terminal_status="BUSINESS_OUTCOME",
    detector=ElementPresent(
        target=TargetDescriptor(
            strategies=[
                TargetStrategy(role="alert", text_contains="No member found for {member_id}.")
            ]
        )
    ),
    description="The console reports no member with the requested id.",
)

READ_SAVINGS_BALANCE_DECLARATION = capability_declaration(
    READ_SAVINGS_BALANCE_GOAL, known_outcomes=[MEMBER_NOT_FOUND]
)
