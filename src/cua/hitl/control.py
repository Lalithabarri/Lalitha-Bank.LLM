"""Who controls the live session (ARCHITECTURE §9, D18).

Ownership is an explicit state, not a flag. ``ActionGate`` denies every automated action while
the owner is anything other than ``AUTOMATION`` (``CONTROL_NOT_OWNED``), and that denial is a
hard boundary. The transitions of the state machine (escalate, accept, done, verified,
abort) are implemented at ARCHITECTURE §15 step 16; this module only fixes the states and the
holder the gate reads.
"""

from enum import StrEnum


class ControlOwnerState(StrEnum):
    AUTOMATION = "AUTOMATION"
    PENDING_HUMAN = "PENDING_HUMAN"
    HUMAN = "HUMAN"
    RETURNING = "RETURNING"


class ControlOwner:
    """Holds the current owner. Read-only until the HITL transitions exist.

    The ``state`` constructor argument is a **test-only** mechanism: it lets tests construct a
    non-AUTOMATION owner so the gate's ``CONTROL_NOT_OWNED`` branch (invariants S4/H2) is
    exercised now. Production code constructs ``ControlOwner()`` and, from step 16 on, moves
    it only through the state-machine transitions.
    """

    def __init__(self, state: ControlOwnerState = ControlOwnerState.AUTOMATION) -> None:
        self._state = ControlOwnerState(state)

    @property
    def state(self) -> ControlOwnerState:
        return self._state

    @property
    def is_automation(self) -> bool:
        return self._state is ControlOwnerState.AUTOMATION

    def __repr__(self) -> str:
        return f"ControlOwner({self._state.value})"
