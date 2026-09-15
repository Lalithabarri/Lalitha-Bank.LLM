"""The envelope: typed, versioned, self-consistent, and structurally unable to hold a ref."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cua.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    PAYLOAD_MODELS,
    ActionCompletedPayload,
    ActionDispatchedPayload,
    EventType,
    EvidenceError,
    EvidenceEvent,
    RunKind,
    RunTerminalPayload,
    Severity,
)

TS = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
FORBIDDEN_FIELD_NAMES = {"ref", "refs", "element", "elements", "snapshot", "locator", "page"}


def event(**overrides) -> EvidenceEvent:
    base = dict(
        event_id="evt_000000000001",
        seq=1,
        ts=TS,
        run_id="run_000000000001",
        run_kind=RunKind.REPLAY,
        session_id="sess_000000000001",
        event_type=EventType.ACTION_COMPLETED,
        severity=Severity.INFO,
        artifact_id="read_savings_balance@1.0.0",
        step_id="s4_read_savings",
        step_index=4,
        payload=ActionCompletedPayload(
            dispatch_seq=4, action_type="READ", url_after="http://h/members/x", value="$1.00"
        ),
    )
    base.update(overrides)
    return EvidenceEvent(**base)


def test_round_trips_through_json_and_the_payload_type_is_recovered():
    original = event()
    line = original.model_dump_json()
    back = EvidenceEvent.model_validate_json(line)
    assert back == original
    assert isinstance(back.payload, ActionCompletedPayload)
    assert back.schema_version == EVIDENCE_SCHEMA_VERSION == "1.0"
    assert '"ts":"2026-09-15T12:00:00Z"' in line


def test_payload_kind_must_match_event_type():
    with pytest.raises(ValidationError, match="does not match"):
        event(event_type=EventType.ACTION_DISPATCHED)


def test_terminal_payload_carries_either_terminal_kind_and_nothing_else():
    completed = RunTerminalPayload(
        kind=EventType.RUN_COMPLETED, status="SUCCESS", outputs={"b": "1.00"}, dispatched_actions=4
    )
    failed = RunTerminalPayload(kind=EventType.RUN_FAILED, status="FAILURE", dispatched_actions=0)
    event(event_type=EventType.RUN_COMPLETED, payload=completed, step_id=None, step_index=None)
    event(event_type=EventType.RUN_FAILED, payload=failed, step_id=None, step_index=None)
    with pytest.raises(ValidationError):
        RunTerminalPayload(kind=EventType.RUN_STARTED, status="SUCCESS", dispatched_actions=0)


def test_seq_is_positive_and_ts_is_timezone_aware():
    with pytest.raises(ValidationError):
        event(seq=0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        event(ts=datetime(2026, 9, 15, 12, 0, 0))


def test_no_payload_model_has_a_field_that_could_hold_a_ref_or_a_driver_object():
    for model in PAYLOAD_MODELS:
        leaked = set(model.model_fields) & FORBIDDEN_FIELD_NAMES
        assert not leaked, (model.__name__, leaked)
    assert not set(EvidenceEvent.model_fields) & FORBIDDEN_FIELD_NAMES


def test_envelope_and_payloads_are_frozen_and_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        ActionDispatchedPayload(
            dispatch_seq=1, observation_index=1, action_type="CLICK", url_before="u", ref="e1"
        )
    with pytest.raises(ValidationError):
        event(extra="x")
    with pytest.raises(ValidationError):
        event().payload.value = "changed"  # type: ignore[misc]


def test_decimal_outputs_are_text_in_the_terminal_payload():
    payload = RunTerminalPayload(
        kind=EventType.RUN_COMPLETED,
        status="SUCCESS",
        outputs={"savings_balance": "15275.00", "count": 3, "flag": True},
        dispatched_actions=4,
    )
    dumped = payload.model_dump(mode="json")["outputs"]
    assert dumped == {"savings_balance": "15275.00", "count": 3, "flag": True}
    assert type(dumped["savings_balance"]) is str


def test_evidence_error_carries_event_type_and_after_dispatch():
    exc = EvidenceError("x", event_type=EventType.ACTION_COMPLETED, after_dispatch=True)
    assert exc.event_type is EventType.ACTION_COMPLETED and exc.after_dispatch is True
    assert EvidenceError("y").event_type is None and EvidenceError("y").after_dispatch is False
