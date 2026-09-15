"""JsonlEvidenceWriter / EvidenceStore: redact before disk, one line per event, never overwrite."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cua.evidence import (
    EVENTS_FILE,
    ActionCompletedPayload,
    ActionFailedPayload,
    EventType,
    EvidenceError,
    EvidenceEvent,
    EvidenceStore,
    JsonlEvidenceWriter,
    Redactor,
    RunKind,
    Severity,
    event_types,
)

TS = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
SECRET = "SENTINEL-7Q9Z-DO-NOT-PERSIST"
REDACTOR = Redactor(sensitive_values={"input:member_id": "M1001", "sentinel": SECRET})


def event(seq: int, event_type=EventType.ACTION_COMPLETED, **payload_overrides) -> EvidenceEvent:
    if event_type is EventType.ACTION_FAILED:
        payload = ActionFailedPayload(
            dispatch_seq=seq,
            action_type="NAVIGATE",
            error_type="SurfaceDriverError",
            error_message=payload_overrides.get("error_message", f"failed for {SECRET}"),
        )
    else:
        payload = ActionCompletedPayload(
            dispatch_seq=seq,
            action_type="CLICK",
            url_after=payload_overrides.get("url_after", f"http://h/members/M1001?t={SECRET}"),
            value=payload_overrides.get("value"),
        )
    return EvidenceEvent(
        event_id=f"evt_{seq:012d}",
        seq=seq,
        ts=TS,
        run_id="run_000000000001",
        run_kind=RunKind.REPLAY,
        session_id="sess_1",
        event_type=event_type,
        severity=Severity.INFO,
        payload=payload,
    )


def test_layout_one_json_object_per_line_and_read_back(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    writer = store.open_run(RunKind.REPLAY, "run_000000000001", REDACTOR)
    assert writer.path == tmp_path / "evidence" / "replay" / "run_000000000001" / EVENTS_FILE
    writer.write(event(1))
    writer.write(event(2, EventType.ACTION_FAILED))
    writer.close()
    lines = writer.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(line) for line in lines)
    events = store.read_events(writer.path)
    assert event_types(events) == [EventType.ACTION_COMPLETED, EventType.ACTION_FAILED]
    assert [e.seq for e in events] == [1, 2]
    assert all(e.redaction_applied for e in events)


def test_redaction_happens_before_any_byte_reaches_disk(tmp_path):
    writer = JsonlEvidenceWriter(tmp_path / EVENTS_FILE, REDACTOR)
    writer.write(event(1))
    writer.write(event(2, EventType.ACTION_FAILED))
    raw = writer.path.read_bytes()
    assert SECRET.encode() not in raw
    assert b"M1001" not in raw
    assert b"/members/<input:member_id>?t=[REDACTED]" in raw
    assert b"failed for <sentinel>" in raw
    assert b'"redaction_applied":true' in raw


def test_each_line_is_flushed_and_visible_to_another_reader_before_close(tmp_path):
    writer = JsonlEvidenceWriter(tmp_path / EVENTS_FILE, REDACTOR)
    writer.write(event(1))
    with open(writer.path, encoding="utf-8") as other:
        assert other.read().count("\n") == 1
    writer.close()


def test_a_run_file_is_never_overwritten(tmp_path):
    store = EvidenceStore(tmp_path)
    store.open_run(RunKind.REPLAY, "run_x", REDACTOR).close()
    with pytest.raises(EvidenceError, match="cannot create"):
        store.open_run(RunKind.REPLAY, "run_x", REDACTOR)
    assert store.events_path(RunKind.REPLAY, "run_x").read_text() == ""


def test_write_after_close_is_an_evidence_error(tmp_path):
    writer = JsonlEvidenceWriter(tmp_path / EVENTS_FILE, REDACTOR)
    writer.close()
    writer.close()  # idempotent
    with pytest.raises(EvidenceError, match="closed") as info:
        writer.write(event(1))
    assert info.value.event_type is EventType.ACTION_COMPLETED


def test_os_failure_on_append_is_an_evidence_error_with_the_cause_chained(tmp_path, monkeypatch):
    writer = JsonlEvidenceWriter(tmp_path / EVENTS_FILE, REDACTOR)

    def boom(_line):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(writer._fh, "write", boom)
    with pytest.raises(EvidenceError, match="No space left") as info:
        writer.write(event(1))
    assert isinstance(info.value.__cause__, OSError)
    assert info.value.event_type is EventType.ACTION_COMPLETED
    assert writer.events_written == 0


def test_unwritable_location_is_an_evidence_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    with pytest.raises(EvidenceError, match="cannot create"):
        JsonlEvidenceWriter(blocker / "run" / EVENTS_FILE, REDACTOR)


def test_redactor_refusal_is_an_evidence_error_not_a_type_error(tmp_path):
    class Refusing(Redactor):
        def redact(self, data):
            raise TypeError("cannot redact")

    writer = JsonlEvidenceWriter(tmp_path / EVENTS_FILE, Refusing())
    with pytest.raises(EvidenceError, match="cannot serialise"):
        writer.write(event(1))
    assert writer.path.read_text() == ""


def test_read_events_fails_loudly_on_a_malformed_or_unredacted_line(tmp_path):
    path = tmp_path / EVENTS_FILE
    path.write_text('{"not": "an event"}\n')
    with pytest.raises(EvidenceError, match=":1: malformed"):
        EvidenceStore.read_events(path)
    unredacted = event(1).model_dump_json() + "\n"
    path.write_text(unredacted)
    with pytest.raises(EvidenceError, match="without redaction"):
        EvidenceStore.read_events(path)
    with pytest.raises(EvidenceError, match="cannot read"):
        EvidenceStore.read_events(tmp_path / "missing.jsonl")


def test_run_ids_cannot_escape_the_store_root(tmp_path):
    store = EvidenceStore(tmp_path)
    for bad in ("../x", "a/b", "", ".", ".."):
        with pytest.raises(ValueError):
            store.run_dir(RunKind.REPLAY, bad)
    assert store.run_dir(RunKind.REPLAY, "run_1") == Path(tmp_path) / "replay" / "run_1"
