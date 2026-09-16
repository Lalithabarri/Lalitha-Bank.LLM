"""Who controls the live session (ARCHITECTURE §9, D18).

Ownership is an explicit state, not a flag. ``ActionGate`` denies every automated action while
the owner is anything other than ``AUTOMATION`` (``CONTROL_NOT_OWNED``), and that denial is a
hard boundary. The transitions are the four edges of the state machine and nothing else:

    AUTOMATION --escalate--> PENDING_HUMAN --accept--> HUMAN --hand_back--> RETURNING
    RETURNING --restore--> AUTOMATION

There is deliberately no HUMAN -> AUTOMATION edge: automation regains control only after a
fresh observation and deterministic verification (the engine calls ``restore`` only then). A
run that ends in an unverifiable or abandoned state leaves the owner in RETURNING, so nothing
can dispatch afterwards. Any other edge is a programming error (``IllegalTransition``).

One ``ControlOwner`` instance is shared by the replay engine and the gate — a wiring invariant
the engine asserts at run start (Milestone 8, amendment A1).
"""

from enum import StrEnum


class ControlOwnerState(StrEnum):
    AUTOMATION = "AUTOMATION"
    PENDING_HUMAN = "PENDING_HUMAN"
    HUMAN = "HUMAN"
    RETURNING = "RETURNING"


class IllegalTransition(ValueError):
    """An edge the state machine does not have."""


_EDGES: dict[str, tuple[ControlOwnerState, ControlOwnerState]] = {
    "escalate": (ControlOwnerState.AUTOMATION, ControlOwnerState.PENDING_HUMAN),
    "accept": (ControlOwnerState.PENDING_HUMAN, ControlOwnerState.HUMAN),
    "hand_back": (ControlOwnerState.HUMAN, ControlOwnerState.RETURNING),
    "restore": (ControlOwnerState.RETURNING, ControlOwnerState.AUTOMATION),
}


class ControlOwner:
    """Holds the current owner and moves it only along the four edges.

    The ``state`` constructor argument is a **test-only** mechanism: it lets tests construct a
    non-AUTOMATION owner directly. Production code constructs ``ControlOwner()`` and moves it
    through the transitions.
    """

    def __init__(self, state: ControlOwnerState = ControlOwnerState.AUTOMATION) -> None:
        self._state = ControlOwnerState(state)

    @property
    def state(self) -> ControlOwnerState:
        return self._state

    @property
    def is_automation(self) -> bool:
        return self._state is ControlOwnerState.AUTOMATION

    # --- transitions (ARCHITECTURE §9) ----------------------------------------------------------

    def escalate(self) -> None:
        self._move("escalate")

    def accept(self) -> None:
        self._move("accept")

    def hand_back(self) -> None:
        self._move("hand_back")

    def restore(self) -> None:
        self._move("restore")

    def _move(self, edge: str) -> None:
        source, target = _EDGES[edge]
        if self._state is not source:
            raise IllegalTransition(
                f"{edge} requires owner {source.value}, not {self._state.value}"
            )
        self._state = target

    def __repr__(self) -> str:
        return f"ControlOwner({self._state.value})"
