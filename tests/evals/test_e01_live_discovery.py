"""E01 — the official live proof. Runs only with ``CUA_LIVE_API=1`` and ``OPENAI_API_KEY``
(``CUA_EVIDENCE_ROOT=evidence`` keeps the run as committed evidence). Prints the full report
before asserting the MUST-PASS invariants; path-specific behaviour is recorded, never asserted.
Captured provider bodies stay in memory and are never printed."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evals.e01_live_discovery import audit, run_live

pytestmark = [pytest.mark.live_api, pytest.mark.browser]


def test_e01_live_discovery(tmp_path):
    if os.environ.get("CUA_LIVE_API") != "1" or not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("live E01 needs CUA_LIVE_API=1 and OPENAI_API_KEY")
    root = os.environ.get("CUA_EVIDENCE_ROOT")
    run = run_live(Path(root) if root else tmp_path / "evidence")
    report = audit(run)
    print("\nE01 REPORT\n" + json.dumps(report, indent=2, default=str))
    failed = sorted(name for name, ok in report["must_pass"].items() if not ok)
    assert failed == [], {"failed": failed, "classification": report["classification"]}
