"""One bound strategy against one observation. Pure; returns every match, never a first one.

V1 semantics (D13, frozen for M4):
* ``role`` and ``name`` — exact equality with ``SurfaceElement.role`` / ``accessible_name``.
* ``scope`` — exact equality with ``SurfaceElement.context_hint`` (``None`` never matches).
* ``text_contains`` — substring of the accessible name OR of the value (alert text lives in the
  value; a cell's text lives in both).
* Set fields are AND-ed; the strategy has at least one set field (schema guarantee).
"""

from __future__ import annotations

from dataclasses import dataclass

from cua.domain import SurfaceElement, SurfaceSnapshot
from cua.replay.binding import BoundDescriptor, BoundStrategy
from cua.surface import find


def match_strategy(strategy: BoundStrategy, snapshot: SurfaceSnapshot) -> list[SurfaceElement]:
    matches = find(snapshot, role=strategy.role, name=strategy.name, context_hint=strategy.scope)
    text = strategy.text_contains
    if text is not None:
        matches = [e for e in matches if text in e.accessible_name or text in (e.value or "")]
    return matches


@dataclass(frozen=True)
class OnSnapshot:
    """Outcome of resolving a descriptor against a single observation.

    ``candidates`` holds the matches of the deciding strategy: one when resolved, several when
    ambiguous, none when no strategy matched. ``strategy_index`` names the deciding strategy.
    """

    candidates: list[SurfaceElement]
    strategy_index: int | None

    @property
    def resolved(self) -> bool:
        return len(self.candidates) == 1

    @property
    def ambiguous(self) -> bool:
        return len(self.candidates) > 1


def resolve_on_snapshot(descriptor: BoundDescriptor, snapshot: SurfaceSnapshot) -> OnSnapshot:
    """Strategies in declared order. ``>1`` decides immediately — a weaker strategy is never
    consulted after a stronger one returned several candidates; ``0`` moves to the next."""
    for index, strategy in enumerate(descriptor.strategies):
        matches = match_strategy(strategy, snapshot)
        if matches:
            return OnSnapshot(candidates=matches, strategy_index=index)
    return OnSnapshot(candidates=[], strategy_index=None)
