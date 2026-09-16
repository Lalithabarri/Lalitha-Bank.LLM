"""E08/E09 evidence audit.

1. The audit function is exercised offline on a SIMULATED run written through the real JSONL
   writer and artifact store (scripted surface, scripted operator; the human's click is a
   page-state change underneath the surface). This validates the audit, not the handoff.
2. Once the official manual headed run is committed, the same audit re-runs on its files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cua.evidence import EvidenceRecorder, EvidenceStore, RunKind
from cua.hitl import ControlOwner, HandBack, HandBackKind
from cua.policy import ActionGate
from cua.replay import MonotonicClock, ReplayConfig, ReplayDeps, ReplayEngine, TerminalStatus
from tests.evals.e08_hitl_live import AMOUNT, ARTIFACT_ID, MEMBER, ROOT, audit_events
from tests.replay.scripted_surface import ScriptedSurface
from tests.replay.support import policy_for

OFFICIAL_E08_RUN_ID: str | None = "run_af80d82668bc"  # the official manual headed run


class _SimulatedOperator:
    def __init__(self, surface: ScriptedSurface) -> None:
        self._surface = surface  # the test's hand, not the handler's browser access

    def intervene(self, request) -> HandBack:
        self._surface.simulate_human_confirm()  # SIMULATION: a state change under the surface
        return HandBack(kind=HandBackKind.DONE)


def test_audit_passes_on_a_simulated_run_written_through_the_real_writer(tmp_path):
    from cua.artifact import ArtifactStore

    store = EvidenceStore(tmp_path / "evidence")
    recorder = EvidenceRecorder(store.open_run)
    surface = ScriptedSurface(listener=recorder)
    owner = ControlOwner()
    engine = ReplayEngine(
        ReplayDeps(
            surface=surface,
            action_gate=ActionGate(policy_for("http://fake.test"), owner, observer=recorder),
            clock=MonotonicClock(),
            evidence=recorder,
            control_owner=owner,
            intervention=_SimulatedOperator(surface),
        ),
        ReplayConfig(base_url="http://fake.test", condition_timeout_s=0.5, poll_interval_s=0.05),
    )
    artifact = ArtifactStore(ROOT / "capabilities").load_id(ARTIFACT_ID)
    result = engine.run(artifact, {"member_id": MEMBER, "amount": AMOUNT})
    assert result.status is TerminalStatus.SUCCESS
    events_path = store.events_path(RunKind.REPLAY, result.run_id)
    report = audit_events(events_path)
    failed = sorted(name for name, ok in report["must_pass"].items() if not ok)
    assert failed == [], failed
    artifacts = sorted(p.name for p in (events_path.parent / "artifacts").iterdir())
    assert [a.split("_")[-1] for a in artifacts] == ["post.png", "pre.png"]


@pytest.mark.skipif(
    OFFICIAL_E08_RUN_ID is None, reason="official manual E08/E09 run not committed yet"
)
def test_the_committed_official_e08_run_passes_the_audit():
    path = EvidenceStore(ROOT / "evidence").events_path(RunKind.REPLAY, OFFICIAL_E08_RUN_ID)
    report = audit_events(Path(path))
    failed = sorted(name for name, ok in report["must_pass"].items() if not ok)
    assert failed == [], failed
