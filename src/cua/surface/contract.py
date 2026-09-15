"""The driver-neutral Surface contract (ARCHITECTURE §3, D04).

A Surface is the seam between "how we perceive/act on a UI" and everything above it. Nothing in
this module — or in any type it exposes — names a driver concept: no Page, Locator, element
handle, or selector. Refs inside a ``SurfaceSnapshot`` are live observation references only:
they are valid for the snapshot that produced them, are replaced by the next ``observe()``, and
must never be persisted as a target or treated as a browser-stable id (ARCHITECTURE §5).

Production automated execution reaches ``Surface.act()`` only through ``ActionGate``
(ARCHITECTURE §8) — the gate is the sole production caller. Direct ``act()`` calls exist only in
adapter-level tests that exercise the driver boundary itself.
"""

from typing import Protocol

from cua.domain import ActionType, DomainModel, SurfaceSnapshot

# Actions a Surface can perform. WAIT/FINISH/REPORT_BLOCKED are loop-level decisions, not
# surface operations (D08).
SURFACE_ACTION_TYPES: frozenset[ActionType] = frozenset(
    {ActionType.NAVIGATE, ActionType.CLICK, ActionType.FILL, ActionType.SELECT, ActionType.READ}
)


class SurfaceAction(DomainModel):
    """A request to the Surface. ``ref`` must come from the current snapshot."""

    action_type: ActionType
    ref: str | None = None
    value: str | None = None
    url: str | None = None


class ActResult(DomainModel):
    action_type: ActionType
    ref: str | None = None
    value: str | None = None
    url_after: str


class SurfaceError(Exception):
    """Base class for Surface failures."""


class UnknownRefError(SurfaceError):
    """The ref is not part of the current observation."""


class StaleObservationError(SurfaceError):
    """An action was attempted without a fresh ``observe()`` since the last action."""


class UnsupportedActionError(SurfaceError):
    """The action type is not a surface operation, or a required field is missing."""


class Surface(Protocol):
    @property
    def session_id(self) -> str: ...

    def observe(self) -> SurfaceSnapshot:
        """Take a fresh snapshot. Replaces every ref issued by any earlier observation."""
        ...

    def act(self, action: SurfaceAction) -> ActResult:
        """Perform one action against a ref from the current snapshot (or a URL for NAVIGATE)."""
        ...

    def close(self) -> None: ...
