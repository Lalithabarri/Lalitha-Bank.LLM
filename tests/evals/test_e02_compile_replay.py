"""E02: discover M1001 (official E01 record) -> compile -> replay the generated artifact on M1002
with zero model decisions; plus the focused verification that the DECLARED MEMBER_NOT_FOUND
outcome holds for the generated artifact on M404.

Set ``CUA_CAPABILITIES_ROOT`` / ``CUA_EVIDENCE_ROOT`` to write the committed copies
(``capabilities/generated``, ``evidence``); by default everything goes to ``tmp_path``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cua.artifact import ArtifactStore, ProvenanceSource
from cua.evidence import EventType, RunKind
from cua.replay import MonotonicClock, StepStatus, TerminalStatus
from tests.evals.e02_compile_replay import (
    ARTIFACT_ID,
    ROOT,
    audit,
    compile_record,
    run_e02,
)
from tests.replay.recording_surface import RecordingSurface
from tests.replay.support import engine_for

GENERATED_ROOT = ROOT / "capabilities" / "generated"


def _root(env: str, default: Path) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else default


@pytest.mark.browser
def test_e02_generated_artifact_replays_m1002_with_zero_model_decisions(tmp_path):
    run = run_e02(
        _root("CUA_CAPABILITIES_ROOT", tmp_path / "generated"),
        _root("CUA_EVIDENCE_ROOT", tmp_path / "evidence"),
    )
    report = audit(run)
    print("E02 REPORT", json.dumps(report, indent=2, default=str))
    failed = sorted(name for name, ok in report["must_pass"].items() if not ok)
    assert failed == [], failed
    assert report["all_must_pass"]


@pytest.mark.browser
def test_generated_artifact_reports_m404_as_the_declared_business_outcome(
    recorded_surface, live_bank, evidence_recorder, evidence_store
):
    """Verifies the DECLARED known outcome on the compiler's output (never the handwritten file):
    a legitimate "no such member" is a BUSINESS_OUTCOME, not a failure, and nothing is read."""
    artifact = compile_record().artifact
    assert artifact.provenance.source is ProvenanceSource.DISCOVERY
    before = artifact.model_dump()
    recording = RecordingSurface(recorded_surface)
    engine = engine_for(
        recording, MonotonicClock(), base_url=live_bank.base_url, recorder=evidence_recorder
    )
    result = engine.run(artifact, {"member_id": "M404"})
    assert result.status is TerminalStatus.BUSINESS_OUTCOME
    assert result.outcome is not None and result.outcome.code == "MEMBER_NOT_FOUND"
    assert result.outcome.step_id == "s3_click"
    assert result.failure is None and result.outputs == {}
    assert recording.act_types == ["NAVIGATE", "FILL", "CLICK"]
    assert result.steps[3].status is StepStatus.NOT_REACHED
    assert artifact.model_dump() == before
    path = evidence_store.events_path(RunKind.REPLAY, result.run_id)
    events = evidence_store.read_events(path)
    assert events[0].payload.provenance_source == "discovery"
    assert [e.event_type for e in events][-2:] == [
        EventType.BUSINESS_OUTCOME,
        EventType.RUN_COMPLETED,
    ]
    assert "M404" not in path.read_text(encoding="utf-8")


def test_committed_generated_artifact_is_reproducible_from_the_committed_e01_evidence():
    """The committed compiler output is exactly what compiling the committed E01 record yields
    (with the committed ``compiled_at``): a reviewer can regenerate and diff."""
    store = ArtifactStore(GENERATED_ROOT)
    committed = store.load_id(ARTIFACT_ID)
    committed_report = store.load_compile_report(ARTIFACT_ID)
    fresh = compile_record(compiled_at=committed.provenance.compiled_at)
    assert fresh.artifact == committed
    assert fresh.report == committed_report
    assert committed.provenance.source is ProvenanceSource.DISCOVERY
    assert committed.provenance.discovery_run_id == "run_e49e4d0cbe09"
    assert store.list_ids() == [ARTIFACT_ID]
