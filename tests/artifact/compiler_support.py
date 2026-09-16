"""Helpers for compiler tests: the official E01 record and deterministic mutations of it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cua.artifact.compiler import ArtifactCompiler
from cua.artifact.declaration import CapabilityDeclaration
from cua.evidence import DiscoveryEndedPayload, EventType, EvidenceStore, RunKind

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_E01_RUN_ID = "run_e49e4d0cbe09"
COMPILED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)
VERSION = "1.0.0"


def official_run() -> DiscoveryEndedPayload:
    """The persisted terminal record of the official E01 discovery, exactly as committed."""
    store = EvidenceStore(ROOT / "evidence")
    events = store.read_events(store.events_path(RunKind.DISCOVERY, OFFICIAL_E01_RUN_ID))
    last = events[-1]
    assert last.event_type is EventType.DISCOVERY_ENDED
    payload = last.payload
    assert isinstance(payload, DiscoveryEndedPayload)
    return payload


def mutate(
    run: DiscoveryEndedPayload, *, step: int | None = None, **fields
) -> DiscoveryEndedPayload:
    """A copy of ``run`` with top-level fields (``step=None``), trace fields (``step=0``) or one
    step's fields (``step=n``, 1-based) replaced. Values are plain JSON so a mutation can insert
    what the typed models would otherwise refuse."""
    data = run.model_dump(mode="json")
    if step is None:
        data.update(fields)
    elif step == 0:
        data["trace"].update(fields)
    else:
        data["trace"]["steps"][step - 1].update(fields)
    return DiscoveryEndedPayload.model_validate(data)


def compile_official(declaration: CapabilityDeclaration, run: DiscoveryEndedPayload | None = None):
    return ArtifactCompiler().compile(
        run or official_run(), declaration, capability_version=VERSION, compiled_at=COMPILED_AT
    )
