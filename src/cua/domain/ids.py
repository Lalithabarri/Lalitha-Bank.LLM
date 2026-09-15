"""Wrapper-generated identifiers."""

import uuid


def new_session_id() -> str:
    """A session id minted by the wrapper, never by the driver (ARCHITECTURE §9, D18).

    It names one browser/context/page lifetime and survives a human handoff unchanged.
    """
    return f"sess_{uuid.uuid4().hex[:12]}"


def new_run_id() -> str:
    """One execution of a capability (discovery or replay)."""
    return f"run_{uuid.uuid4().hex[:12]}"
