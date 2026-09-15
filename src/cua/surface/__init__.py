"""surface/ — Driver, observe/act, session_id, ACTION_DISPATCHED.

Owns: the ``Surface`` contract and ``PlaywrightSurface`` (ARCHITECTURE §3, D04, D05).
Must never: leak Playwright types upward.

This package exports only the driver-neutral contract. ``PlaywrightSurface`` lives in
``cua.surface.playwright_surface`` and is imported explicitly by a composition root, so
``import cua.surface`` never loads Playwright.
"""

from cua.surface.contract import (
    SURFACE_ACTION_TYPES,
    ActResult,
    StaleObservationError,
    Surface,
    SurfaceAction,
    SurfaceError,
    UnknownRefError,
    UnsupportedActionError,
)
from cua.surface.query import find

__all__ = [
    "SURFACE_ACTION_TYPES",
    "ActResult",
    "StaleObservationError",
    "Surface",
    "SurfaceAction",
    "SurfaceError",
    "UnknownRefError",
    "UnsupportedActionError",
    "find",
]
