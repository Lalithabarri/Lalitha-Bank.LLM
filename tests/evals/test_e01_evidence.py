"""The committed official E01 evidence (``run_e49e4d0cbe09``) passes its public-safety audit in the
normal, offline suite — the same scans the live audit applied, re-derived from the file alone.

The four provider-boundary invariants of the live audit (no member id in any model request or
outbound HTTP body, no key in any body, ``store: false`` on every body) were verified in memory at
run time and cannot be re-derived from disk by design: nothing from the provider boundary is
persisted. Everything else is.
"""

from __future__ import annotations

import json
import re
import socket
from decimal import Decimal
from pathlib import Path

import pytest

from cua.evidence import EventType, EvidenceStore, RunKind

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_E01_RUN_ID = "run_e49e4d0cbe09"
OFFICIAL_MODEL = "gpt-5.6-sol"
MEMBER = "M1001"
_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")
STEP = [
    EventType.OBSERVATION,
    EventType.MODEL_CALL,
    EventType.GATE_DECISION,
    EventType.ACTION_DISPATCHED,
    EventType.ACTION_COMPLETED,
]


@pytest.fixture(scope="module")
def official():
    store = EvidenceStore(ROOT / "evidence")
    path = store.events_path(RunKind.DISCOVERY, OFFICIAL_E01_RUN_ID)
    assert path.exists(), path
    raw = path.read_text(encoding="utf-8")
    return raw, [json.loads(line) for line in raw.splitlines()], store.read_events(path)


def test_public_safety_scan(official):
    raw, lines, _ = official
    assert MEMBER not in raw
    assert "<input:member_id>" in raw
    assert '"ref"' not in raw and '"refs"' not in raw
    assert not _REF_TOKEN.search(raw)
    assert all(ln["redaction_applied"] is True for ln in lines)
    assert all(ln["schema_version"] == "1.1" for ln in lines)
    assert "Bearer" not in raw and not re.search(r"sk-[A-Za-z0-9_-]{8,}", raw)
    assert "untrusted application data" not in raw  # no prompt text
    assert '"text_format"' not in raw and "json_schema" not in raw  # no raw HTTP body
    assert "/Users/" not in raw and "/home/" not in raw and "/tmp/" not in raw
    assert socket.gethostname() not in raw and "@" not in raw


def test_chronology_is_valid_and_ends_in_a_verified_goal(official):
    _, _, events = official
    types = [e.event_type for e in events]
    assert types == [
        EventType.DISCOVERY_STARTED,
        *STEP * 4,
        EventType.OBSERVATION,
        EventType.MODEL_CALL,
        EventType.DISCOVERY_ENDED,
    ]
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert len({e.session_id for e in events}) == 1
    assert {e.run_kind for e in events} == {RunKind.DISCOVERY}
    for i, event in enumerate(events):
        if event.event_type is EventType.ACTION_DISPATCHED:
            gate = events[i - 1]
            assert gate.event_type is EventType.GATE_DECISION and gate.payload.decision == "ALLOW"
            assert gate.payload.action_type == event.payload.action_type
            assert events[i + 1].event_type is EventType.ACTION_COMPLETED
    started = events[0].payload
    assert started.provider == "openai" and started.model_id == OFFICIAL_MODEL
    assert started.mask_bound_inputs is True
    assert started.model_navigation_routes == ["/members/search"]
    ended = events[-1].payload
    assert ended.stop_reason == "GOAL_REACHED" and ended.goal_satisfied is True
    assert ended.outputs == {"savings_balance": "15275.00"}
    assert Decimal(ended.outputs["savings_balance"]) == Decimal("15275.00")
    assert ended.dispatched_actions == 4 and ended.steps_dispatched == 4
    assert ended.model_calls == 5 and ended.corrective_retries == 0


def test_model_call_provenance_shows_real_openai_participation(official):
    _, _, events = official
    calls = [e.payload for e in events if e.event_type is EventType.MODEL_CALL]
    assert len(calls) == 5
    ids = set()
    for n, call in enumerate(calls, start=1):
        assert call.provider == "openai"
        assert call.model_id_requested == OFFICIAL_MODEL
        assert call.model_id_reported == OFFICIAL_MODEL
        assert call.provider_response_id and call.provider_response_id.startswith("resp_")
        ids.add(call.provider_response_id)
        assert call.call_index == n and call.purpose == "DECIDE" and call.outcome == "DECISION"
        assert call.validation.status == "VALID"
        assert (call.input_tokens or 0) > 0 and (call.output_tokens or 0) > 0
        assert (call.latency_ms or 0) > 0
    assert len(ids) == 5  # five distinct real responses
    assert [c.decision.kind for c in calls] == ["ACT", "ACT", "ACT", "ACT", "FINISH"]
    assert [c.decision.action_type for c in calls[:4]] == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert calls[1].decision.value_binding_kind == "INPUT_REF"
    assert calls[1].decision.input_name == "member_id"


def test_trace_is_coherent_and_literal_free(official):
    _, _, events = official
    trace = events[-1].payload.trace
    assert trace.provider == "openai" and trace.model_id == OFFICIAL_MODEL
    assert trace.discovery_run_id == OFFICIAL_E01_RUN_ID
    assert trace.session_id == events[0].session_id
    assert [s.action_type for s in trace.steps] == ["NAVIGATE", "FILL", "CLICK", "READ"]
    nav, fill, click, read = trace.steps
    assert nav.route_template == "/members/search"
    assert fill.value_binding_kind == "INPUT_REF" and fill.input_name == "member_id"
    assert fill.literal_value is None
    assert click.url_after_template.endswith("/members/{member_id}")
    assert read.target.role == "cell" and read.target.accessible_name is None
    assert read.target.context_hint == "table: Accounts > row: Savings"
    assert read.identity_matches == 1 and read.output_name == "savings_balance"
    assert read.read_text == "$15,275.00" and read.input_evidence == {"member_id": "ROUTE"}
    assert all(s.gate_decision == "ALLOW" for s in trace.steps)
    dispatched = [e.payload for e in events if e.event_type is EventType.ACTION_DISPATCHED]
    assert dispatched[1].action_type == "FILL" and dispatched[1].value == "<input:member_id>"
