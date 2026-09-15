"""Zero-model proof without a browser: a fresh interpreter, an import guard, a full replay."""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_zero_model(*args: str) -> dict:
    completed = subprocess.run(
        [sys.executable, "-m", "tests.replay.zero_model_replay", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def test_scripted_replay_completes_under_the_import_guard(tmp_path):
    report = run_zero_model("--member", "M1002", "--evidence-root", str(tmp_path))
    assert report["status"] == "SUCCESS"
    assert report["outputs"] == {"savings_balance": "4120.75"}
    assert report["output_types"] == {"savings_balance": "Decimal"}
    assert report["forbidden_loaded"] == []
    assert report["forbidden_attempted"] == []
    # M5: the real evidence layer ran too, under the same guard, and persisted no member id.
    assert report["events_written"] == 14 and report["member_in_evidence"] is False
    assert Path(report["evidence_path"]).is_relative_to(tmp_path / "replay")
    assert report["event_types"][0] == "RUN_STARTED"
    assert report["event_types"][-1] == "RUN_COMPLETED"


def test_business_outcome_under_the_import_guard():
    report = run_zero_model("--member", "M404")
    assert report["status"] == "BUSINESS_OUTCOME" and report["outputs"] == {}
    assert report["forbidden_loaded"] == [] and report["forbidden_attempted"] == []
    assert report["event_types"][-2:] == ["BUSINESS_OUTCOME", "RUN_COMPLETED"]
    assert report["member_in_evidence"] is False


def test_the_guard_itself_bites():
    """Sanity: the guard is not vacuous — importing a forbidden name in that interpreter fails."""
    code = (
        "import sys; from tests.replay.zero_model_replay import _Guard; "
        "sys.meta_path.insert(0, _Guard());\n"
        "try:\n    import cua.llm\nexcept ImportError as e:\n    print('blocked:', e)\n"
        "else:\n    print('NOT BLOCKED')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    )
    assert out.stdout.startswith("blocked:")
