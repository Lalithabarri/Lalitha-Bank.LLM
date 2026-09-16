"""EvidenceRecorder: run bracket, step context, seq, and the one-failure-per-run discipline."""

import re
from datetime import UTC, datetime

import pytest

from cua.domain import ActionType
from cua.evidence import (
    EventType,
    EvidenceError,
    EvidenceRecorder,
    Redactor,
    RunKind,
    RunStartedPayload,
    RunTerminalPayload,
    Severity,
)
from cua.policy import (
    DenyReason,
    GateDecision,
    GateRequest,
    GateResult,
    ResolvedTarget,
    RiskTier,
)
from cua.surface import ActResult, DispatchRecord, SurfaceDriverError
from tests.evidence.memory_sink import MemoryEvidence

_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")

TS = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


def started_payload() -> RunStartedPayload:
    return RunStartedPayload(
        run_kind=RunKind.REPLAY,
        artifact_id="read_savings_balance@1.0.0",
        capability_name="read_savings_balance",
        capability_version="1.0.0",
        provenance_source="handwritten",
        base_url="http://fake.test",
        inputs_declared=["member_id"],
        resolve_timeout_s=5.0,
        condition_timeout_s=5.0,
        poll_interval_s=0.1,
    )


def terminal(kind=EventType.RUN_COMPLETED, status="SUCCESS") -> RunTerminalPayload:
    return RunTerminalPayload(kind=kind, status=status, dispatched_actions=1)


def record(seq: int = 1, action=ActionType.FILL, value: str | None = "M1001") -> DispatchRecord:
    return DispatchRecord(
        session_id="sess_1",
        dispatch_seq=seq,
        observation_index=2,
        action_type=action,
        url_before="http://fake.test/members/search",
        target_role="textbox",
        target_name="Member ID",
        value=value,
    )


def recorder_with(memory: MemoryEvidence | None = None) -> tuple[EvidenceRecorder, MemoryEvidence]:
    memory = memory or MemoryEvidence()
    ids = iter(f"evt_{n:012d}" for n in range(1, 1000))
    recorder = EvidenceRecorder(
        memory, redactor=Redactor(), wall_clock=lambda: TS, event_ids=lambda: next(ids)
    )
    return recorder, memory


def begin(recorder: EvidenceRecorder, inputs=None) -> None:
    recorder.begin_run(
        run_id="run_1",
        run_kind=RunKind.REPLAY,
        session_id="sess_1",
        artifact_id="read_savings_balance@1.0.0",
        inputs=inputs if inputs is not None else {"member_id": "M1001"},
        payload=started_payload(),
    )


# --- lifecycle ---------------------------------------------------------------------------------


def test_begin_run_opens_the_sink_with_input_placeholders_and_writes_run_started():
    recorder, memory = recorder_with()
    assert not recorder.active and recorder.dispatch_count == 0
    begin(recorder)
    assert recorder.active and recorder.run_id == "run_1"
    run_kind, run_id, redactor = memory.opened[0]
    assert (run_kind, run_id) == (RunKind.REPLAY, "run_1")
    assert redactor.sensitive_labels == ("input:member_id",)
    [event] = memory.events
    assert event.event_type is EventType.RUN_STARTED and event.seq == 1
    assert event.run_id == "run_1" and event.session_id == "sess_1"
    assert event.payload.inputs_declared == ["member_id"]
    assert "M1001" not in memory.text


def test_one_run_at_a_time():
    recorder, _ = recorder_with()
    begin(recorder)
    with pytest.raises(EvidenceError, match="still being recorded"):
        begin(recorder)
    recorder.run_completed(terminal())
    recorder.end_run()
    begin(recorder)  # reusable once released


def test_events_outside_a_run_are_refused_so_nothing_dispatches_unrecorded():
    recorder, memory = recorder_with()
    with pytest.raises(EvidenceError, match="outside a recorded run") as info:
        recorder.on_dispatched(record())
    assert info.value.event_type is EventType.ACTION_DISPATCHED
    assert info.value.after_dispatch is False
    assert memory.sinks == [] and recorder.dispatch_count == 0
    with pytest.raises(RuntimeError, match="no run"):
        recorder.begin_step("s1", 1)


def test_seq_is_strictly_increasing_and_step_context_is_stamped_then_cleared():
    recorder, memory = recorder_with()
    begin(recorder)
    recorder.begin_step("s2_enter_member", 2)
    recorder.on_decision(*allow_request())
    recorder.on_dispatched(record())
    recorder.on_completed(record(), ActResult(action_type=ActionType.FILL, url_after="u"))
    recorder.clear_step()
    recorder.run_completed(terminal())
    assert [e.seq for e in memory.events] == [1, 2, 3, 4, 5]
    assert [e.event_id for e in memory.events] == [f"evt_{n:012d}" for n in range(1, 6)]
    assert memory.types == [
        EventType.RUN_STARTED,
        EventType.GATE_DECISION,
        EventType.ACTION_DISPATCHED,
        EventType.ACTION_COMPLETED,
        EventType.RUN_COMPLETED,
    ]
    assert [(e.step_id, e.step_index) for e in memory.events] == [
        (None, None),
        ("s2_enter_member", 2),
        ("s2_enter_member", 2),
        ("s2_enter_member", 2),
        (None, None),
    ]
    assert all(e.ts == TS for e in memory.events)


def test_terminal_events_close_the_run_and_refuse_anything_after():
    recorder, memory = recorder_with()
    begin(recorder)
    recorder.run_failed(terminal(EventType.RUN_FAILED, "FAILURE"))
    assert memory.last.closed
    assert memory.events[-1].severity is Severity.ERROR
    with pytest.raises(EvidenceError, match="complete"):
        recorder.on_dispatched(record())
    recorder.end_run()
    recorder.end_run()  # idempotent
    assert not recorder.active


def test_business_outcome_then_run_completed_with_business_outcome_status():
    recorder, memory = recorder_with()
    begin(recorder)
    recorder.business_outcome("MEMBER_NOT_FOUND", "no such member", "s3_search")
    recorder.run_completed(terminal(status="BUSINESS_OUTCOME"))
    outcome, completed = memory.events[-2:]
    assert outcome.event_type is EventType.BUSINESS_OUTCOME and outcome.step_id == "s3_search"
    assert outcome.payload.code == "MEMBER_NOT_FOUND"
    assert completed.event_type is EventType.RUN_COMPLETED
    assert completed.payload.status == "BUSINESS_OUTCOME" and completed.step_id is None


def test_terminal_kind_must_match_the_method():
    recorder, _ = recorder_with()
    begin(recorder)
    with pytest.raises(ValueError, match="does not match"):
        recorder.run_completed(terminal(EventType.RUN_FAILED, "FAILURE"))


# --- gate and surface notifications ---------------------------------------------------------------


def allow_request(decision=GateDecision.ALLOW, deny_reason=None):
    request = GateRequest(
        action_type=ActionType.FILL,
        origin="http://fake.test",
        route="/members/search",
        target=ResolvedTarget(role="textbox", accessible_name="Member ID", context_hint="group: x"),
        declared_risk=RiskTier.REVERSIBLE_WRITE,
    )
    result = GateResult(
        decision=decision,
        risk=RiskTier.REVERSIBLE_WRITE,
        deny_reason=deny_reason,
        explanation="because",
    )
    return request, result


def test_gate_decisions_carry_the_semantic_target_and_a_warning_severity_when_not_allowed():
    recorder, memory = recorder_with()
    begin(recorder)
    recorder.on_decision(*allow_request())
    recorder.on_decision(*allow_request(GateDecision.DENY, DenyReason.ROUTE_NOT_ALLOWED))
    recorder.on_decision(*allow_request(GateDecision.REQUIRE_INTERVENTION))
    allow, deny, intervene = memory.events[1:]
    assert allow.severity is Severity.INFO and allow.payload.decision == "ALLOW"
    assert allow.payload.target.model_dump() == {
        "role": "textbox",
        "accessible_name": "Member ID",
        "context_hint": "group: x",
        "value": None,
    }
    assert allow.payload.declared_risk == "REVERSIBLE_WRITE"
    assert deny.severity is Severity.WARNING and deny.payload.deny_reason == "ROUTE_NOT_ALLOWED"
    assert intervene.severity is Severity.WARNING
    assert intervene.payload.decision == "REQUIRE_INTERVENTION"


def test_dispatch_count_increments_only_after_action_dispatched_was_persisted():
    recorder, memory = recorder_with(MemoryEvidence(fail_on={EventType.ACTION_DISPATCHED}))
    begin(recorder)
    with pytest.raises(EvidenceError) as info:
        recorder.on_dispatched(record())
    assert recorder.dispatch_count == 0
    assert info.value.event_type is EventType.ACTION_DISPATCHED
    assert info.value.after_dispatch is False
    assert recorder.evidence_failed and recorder.failure is info.value

    recorder, memory = recorder_with()
    begin(recorder)
    recorder.on_dispatched(record())
    assert recorder.dispatch_count == 1
    dispatched = memory.events[-1]
    assert dispatched.payload.value == "<input:member_id>"
    assert dispatched.payload.target_name == "Member ID"


def test_completed_and_failed_notifications_never_copy_the_ref_and_mark_after_dispatch():
    recorder, memory = recorder_with()
    begin(recorder)
    recorder.on_dispatched(record())
    result = ActResult(action_type=ActionType.FILL, ref="e10", value="M1001", url_after="u")
    recorder.on_completed(record(), result)
    recorder.on_failed(record(2), SurfaceDriverError("TimeoutError: e12 vanished at http://h?t=1"))
    completed, failed = memory.events[-2:]
    assert completed.payload.model_dump() == {
        "kind": "ACTION_COMPLETED",
        "dispatch_seq": 1,
        "action_type": "FILL",
        "url_after": "u",
        "value": "<input:member_id>",
    }
    assert failed.payload.error_type == "SurfaceDriverError"
    assert failed.payload.error_message == "TimeoutError: <ref> vanished at http://h?t=[REDACTED]"
    # refs must be gone as *tokens*; random hex event/run ids may legitimately contain "e10"
    assert _REF_TOKEN.search(memory.text) is None

    recorder, memory = recorder_with(MemoryEvidence(fail_on={EventType.ACTION_COMPLETED}))
    begin(recorder)
    recorder.on_dispatched(record())
    with pytest.raises(EvidenceError) as info:
        recorder.on_completed(record(), result)
    assert info.value.after_dispatch is True and recorder.dispatch_count == 1


# --- one failure per run --------------------------------------------------------------------------


def test_after_the_first_failure_nothing_more_is_written_and_every_write_is_refused():
    memory = MemoryEvidence(fail_on={EventType.GATE_DECISION})
    recorder, memory = recorder_with(memory)
    begin(recorder)
    with pytest.raises(EvidenceError, match="could not record GATE_DECISION"):
        recorder.on_decision(*allow_request())
    seen_before = len(memory.last.raw_seen)
    for attempt in (
        lambda: recorder.on_dispatched(record()),
        lambda: recorder.business_outcome("X", "y", None),
        lambda: recorder.run_failed(terminal(EventType.RUN_FAILED, "FAILURE")),
        lambda: recorder.run_completed(terminal()),
    ):
        with pytest.raises(EvidenceError, match="already failed"):
            attempt()
    assert len(memory.last.raw_seen) == seen_before  # the sink was never asked again
    assert memory.types == [EventType.RUN_STARTED]
    assert recorder.dispatch_count == 0
    recorder.end_run()  # never raises
    assert not recorder.active


def test_open_failure_is_an_evidence_error_and_leaves_no_run():
    recorder, memory = recorder_with(MemoryEvidence(fail_open=True))
    with pytest.raises(EvidenceError, match="cannot open") as info:
        begin(recorder)
    assert info.value.event_type is EventType.RUN_STARTED
    assert not recorder.active and memory.sinks == []


def test_close_failure_on_the_terminal_write_is_an_evidence_error():
    recorder, _ = recorder_with(MemoryEvidence(fail_close=True))
    begin(recorder)
    with pytest.raises(EvidenceError, match="close") as info:
        recorder.run_completed(terminal())
    assert info.value.event_type is EventType.RUN_COMPLETED
    assert recorder.evidence_failed
    recorder.end_run()


def test_end_run_swallows_a_failing_close_on_an_already_failed_run():
    memory = MemoryEvidence(fail_on={EventType.ACTION_DISPATCHED}, fail_close=True)
    recorder, _ = recorder_with(memory)
    begin(recorder)
    with pytest.raises(EvidenceError):
        recorder.on_dispatched(record())
    recorder.end_run()
    assert not recorder.active


def test_non_text_inputs_are_not_registered_as_placeholders():
    recorder, memory = recorder_with()
    with pytest.raises(ValueError):
        begin(recorder, inputs={"member_id": ""})
