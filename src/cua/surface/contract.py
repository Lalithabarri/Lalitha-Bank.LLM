"""The driver-neutral Surface contract (ARCHITECTURE §3, D04).

A Surface is the seam between "how we perceive/act on a UI" and everything above it. Nothing in
this module — or in any type it exposes — names a driver concept: no Page, Locator, element
handle, or selector. Refs inside a ``SurfaceSnapshot`` are live observation references only:
they are valid for the snapshot that produced them, are replaced by the next ``observe()``, and
must never be persisted as a target or treated as a browser-stable id (ARCHITECTURE §5).

Production automated execution reaches ``Surface.act()`` only through ``ActionGate``
(ARCHITECTURE §8) — the gate is the sole production caller. Direct ``act()`` calls exist only in
adapter-level tests that exercise the driver boundary itself.

Errors fall into two classes, and callers treat them differently:

* runtime / environment failures — ``SurfaceDriverError`` (the driver itself failed: navigation
  refused, load or locator timeout, page gone) and ``UnknownRefError`` (the element vanished
  between observe and act). A replay may report these as a failure of the run.
* caller-contract violations — ``StaleObservationError`` and ``UnsupportedActionError``. These
  mean the caller misused the contract and must propagate as programming errors.

Dispatch evidence (ARCHITECTURE §10): a Surface implementation reports every driver-bound action
to a ``DispatchListener`` — ``on_dispatched`` immediately *before* the driver operation (the
ground truth that an automated action was attempted), then exactly one of ``on_completed`` /
``on_failed``. The listener is observational: it never decides, resolves, or acts. If
``on_dispatched`` raises, the driver operation must not run and the action must not be counted as
attempted; if a later notification raises, the exception propagates as-is (the action did happen).
``DispatchRecord`` carries no ref — refs never leave the snapshot they came from.
"""

from typing import Protocol, runtime_checkable

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


class SurfaceDriverError(SurfaceError):
    """The underlying driver failed (navigation refused, timeout, page gone).

    Raised by a Surface implementation in place of any driver-specific exception, so no driver
    type ever crosses the boundary. The original driver error is chained as ``__cause__``.
    """


class DispatchRecord(DomainModel):
    """What the Surface knows about an action as it crosses the driver boundary. No ref."""

    session_id: str
    dispatch_seq: int  # the number this attempt gets if it proceeds (== dispatched count after)
    observation_index: int  # the surface's observation counter at dispatch time
    action_type: ActionType
    url_before: str
    destination: str | None = None  # NAVIGATE
    target_role: str | None = None  # semantic identity of the element, from the observation
    target_name: str | None = None
    value: str | None = None  # FILL / SELECT input; in memory only, redacted before disk


class DispatchListener(Protocol):
    def on_dispatched(self, record: DispatchRecord) -> None:
        """Called immediately before the driver operation. Raising prevents the operation."""
        ...

    def on_completed(self, record: DispatchRecord, result: ActResult) -> None: ...

    def on_failed(self, record: DispatchRecord, error: SurfaceDriverError) -> None: ...


class NullDispatchListener:
    """For adapter-level tests and non-run usage only; a recorded run wires an EvidenceRecorder."""

    def on_dispatched(self, record: DispatchRecord) -> None:
        return None

    def on_completed(self, record: DispatchRecord, result: ActResult) -> None:
        return None

    def on_failed(self, record: DispatchRecord, error: SurfaceDriverError) -> None:
        return None


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


@runtime_checkable
class ScreenshotCapable(Protocol):
    """Optional, additive capability: a pixel capture of the current page as PNG bytes.

    Evidence only (Milestone 8 intervention pre/post captures). Never a source of decisions —
    the deterministic observation is. Checked with ``isinstance`` at composition; a surface that
    lacks it cannot be used for interventions.
    """

    def capture_screenshot(self) -> bytes: ...
