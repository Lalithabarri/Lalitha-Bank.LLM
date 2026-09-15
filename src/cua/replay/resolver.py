"""TargetResolver — bounded, deterministic semantic target resolution (ARCHITECTURE §7, D13).

One resolution deadline per target. On every pass a *fresh* observation is taken; the declared
known-outcome detectors run first; then the strategies run in declared order:

* ``>1`` matches — ``Ambiguous``, immediately. No weaker strategy is consulted, no further
  observation is taken, nothing is retried. Never ``first()``, never a guess.
* ``1`` match   — ``Resolved``.
* ``0`` matches — try the next strategy; if none matched, poll until the deadline, then
  ``NotFound``.

At least one observation is always taken, even at a zero deadline. The ref inside
``Resolved.element`` is the ephemeral ref of ``Resolved.snapshot`` and nothing else: the caller
must use both together, immediately, and persist neither.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cua.domain import SurfaceElement, SurfaceSnapshot
from cua.replay.binding import BoundDescriptor, BoundKnownOutcome
from cua.replay.clock import Clock
from cua.replay.conditions import detect_known_outcome
from cua.replay.matching import resolve_on_snapshot


@dataclass(frozen=True)
class Resolved:
    element: SurfaceElement
    snapshot: SurfaceSnapshot
    strategy_index: int
    observations: int


@dataclass(frozen=True)
class Ambiguous:
    candidates: list[SurfaceElement]
    snapshot: SurfaceSnapshot
    strategy_index: int
    observations: int


@dataclass(frozen=True)
class NotFound:
    snapshot: SurfaceSnapshot
    observations: int


@dataclass(frozen=True)
class OutcomeDetected:
    outcome: BoundKnownOutcome
    snapshot: SurfaceSnapshot
    observations: int


Resolution = Resolved | Ambiguous | NotFound | OutcomeDetected


class TargetResolver:
    def __init__(self, clock: Clock, *, timeout_s: float, poll_interval_s: float) -> None:
        if timeout_s < 0 or poll_interval_s <= 0:
            raise ValueError("timeout must be >= 0 and poll interval > 0")
        self._clock = clock
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s

    def resolve(
        self,
        descriptor: BoundDescriptor,
        observe: Callable[[], SurfaceSnapshot],
        known_outcomes: list[BoundKnownOutcome],
    ) -> Resolution:
        deadline = self._clock.now() + self._timeout_s
        observations = 0
        while True:
            snapshot = observe()
            observations += 1
            outcome = detect_known_outcome(known_outcomes, snapshot)
            if outcome is not None:
                return OutcomeDetected(outcome, snapshot, observations)
            on = resolve_on_snapshot(descriptor, snapshot)
            if on.ambiguous:
                assert on.strategy_index is not None
                return Ambiguous(on.candidates, snapshot, on.strategy_index, observations)
            if on.resolved:
                assert on.strategy_index is not None
                return Resolved(on.candidates[0], snapshot, on.strategy_index, observations)
            if self._clock.now() >= deadline:
                return NotFound(snapshot, observations)
            self._clock.sleep(self._poll_interval_s)
