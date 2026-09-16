"""Discovery evidence: the chronology on the (in-memory, redacted) sink, provider/model
provenance, what never reaches disk, and the fail-closed evidence semantics (A4)."""

from __future__ import annotations

import json

import pytest

from cua.artifact import InputRef
from cua.discovery import READ_SAVINGS_BALANCE_GOAL, StopCode, StopReason
from cua.discovery.summaries import trace_summary
from cua.evidence import EventType, EvidenceRecorder, EvidenceStore, Redactor, RunKind
from cua.llm import ActDecision, ProviderError, ProviderErrorKind
from tests.discovery.fake_llm import (
    Click,
    Fill,
    Finish,
    Nav,
    Read,
    ScriptedLLM,
    SemanticLLM,
    decision_for,
)
from tests.discovery.support import BASE_URL, agent_for, scripted_surface
from tests.evidence.memory_sink import MemoryEvidence
from tests.replay.recording_surface import RecordingSurface
from tests.replay.scripted_surface import ScriptedSurface
from tests.replay.support import recorder_for

GOAL = READ_SAVINGS_BALANCE_GOAL
MEMBER = "M1001"
STEP = [
    EventType.OBSERVATION,
    EventType.MODEL_CALL,
    EventType.GATE_DECISION,
    EventType.ACTION_DISPATCHED,
    EventType.ACTION_COMPLETED,
]


def run(llm=None, *, memory: MemoryEvidence | None = None, member: str = MEMBER, **overrides):
    memory = memory or MemoryEvidence()
    recorder = recorder_for(memory)
    inner = scripted_surface(recorder)
    surface = RecordingSurface(inner)
    agent, _, _ = agent_for(surface, llm or SemanticLLM(), recorder=recorder, **overrides)
    result = agent.run(GOAL, {"member_id": member})
    return result, surface, inner, memory


# --- chronology and provenance -------------------------------------------------------------------


def test_successful_discovery_leaves_a_coherent_chronology():
    result, _, _, memory = run()
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert memory.types == [
        EventType.DISCOVERY_STARTED,
        *STEP * 4,
        EventType.OBSERVATION,
        EventType.MODEL_CALL,
        EventType.DISCOVERY_ENDED,
    ]
    events = memory.events
    assert [e.seq for e in events] == list(range(1, len(events) + 1))
    assert {e.run_kind for e in events} == {RunKind.DISCOVERY}
    assert {e.session_id for e in events} == {"sess_scripted"}
    assert {e.schema_version for e in events} == {"1.2"}  # 1.1 lines stay readable (M6 E01)
    assert all(e.artifact_id is None for e in events)
    # every event of step n is stamped d<n>
    for event in events[1:-1]:
        assert event.step_id == f"d{event.step_index}"
    # every dispatch was preceded by an ALLOW for that very action
    for i, event in enumerate(events):
        if event.event_type is EventType.ACTION_DISPATCHED:
            gate = events[i - 1]
            assert gate.event_type is EventType.GATE_DECISION
            assert gate.payload.decision == "ALLOW"
            assert gate.payload.action_type == event.payload.action_type
    started = events[0].payload
    assert started.goal_name == "read_savings_balance"
    assert started.inputs_declared == ["member_id"] and started.outputs_declared == [
        "savings_balance"
    ]
    assert started.provider == "fake" and started.model_id == "fake-semantic-1"
    assert started.model_navigation_routes == ["/members/search"]
    assert started.mask_bound_inputs is True
    ended = events[-1].payload
    assert ended.stop_reason == "GOAL_REACHED" and ended.goal_satisfied is True
    assert ended.outputs == {"savings_balance": "15275.00"}
    assert ended.dispatched_actions == 4 and ended.steps_dispatched == 4
    assert ended.model_calls == 5 and ended.corrective_retries == 0


def test_every_model_call_carries_provider_and_model_metadata():
    result, _, _, memory = run()
    calls = memory.last.of(EventType.MODEL_CALL)
    assert len(calls) == 5
    for n, event in enumerate(calls, start=1):
        p = event.payload
        assert p.provider == "fake" and p.model_id_requested == "fake-semantic-1"
        assert p.model_id_reported == "fake-semantic-1"
        assert p.provider_response_id == f"fake_resp_{n}"
        assert p.call_index == n and p.purpose == "DECIDE" and p.outcome == "DECISION"
        assert p.validation.status == "VALID" and p.corrective_retry_count == 0
        assert p.decision is not None and p.decision.intent_summary
    kinds = [e.payload.decision.kind for e in calls]
    assert kinds == ["ACT", "ACT", "ACT", "ACT", "FINISH"]
    fill = calls[1].payload.decision
    assert fill.action_type == "FILL" and fill.value_binding_kind == "INPUT_REF"
    assert fill.input_name == "member_id"


def test_corrective_retry_is_evidenced_as_its_own_call():
    llm = SemanticLLM(
        intents=[
            Nav("/members/search"),
            Fill("textbox", "Member ID", InputRef(input_name="member_id")),
            Click("button", "Search"),
            Finish(),
            Finish(),
        ],
        on_feedback=lambda request, _i: decision_for(
            Read("cell", "row: Savings", "savings_balance"), request
        ),
    )
    result, _, _, memory = run(llm)
    assert result.stop_reason is StopReason.GOAL_REACHED
    calls = memory.last.of(EventType.MODEL_CALL)
    purposes = [(c.payload.purpose, c.payload.validation.status) for c in calls]
    assert purposes[3:5] == [("DECIDE", "INVALID"), ("CORRECTIVE_RETRY", "VALID")]
    assert calls[3].payload.validation.code == "PREMATURE_FINISH"
    assert calls[4].payload.corrective_retry_count == 1
    assert calls[3].step_index == calls[4].step_index == 4


def test_provider_error_is_evidenced_without_the_body():
    exc = ProviderError(ProviderErrorKind.SERVER, status_code=503)
    exc.__cause__ = RuntimeError("raw provider body sk-SECRET")
    _, _, _, memory = run(ScriptedLLM([exc]))
    call = memory.last.of(EventType.MODEL_CALL)[0].payload
    assert call.outcome == "PROVIDER_ERROR" and call.error_kind == "SERVER"
    assert call.status_code == 503 and call.provider_response_id is None
    assert "SECRET" not in memory.text
    assert memory.types[-1] is EventType.DISCOVERY_ENDED
    assert memory.events[-1].payload.stop_reason == "MODEL_ERROR"


# --- what never reaches disk ----------------------------------------------------------------------


def test_member_id_refs_and_raw_response_never_reach_persisted_evidence():
    llm = SemanticLLM()
    result, _, _, memory = run(llm)
    text = memory.text
    assert MEMBER not in text
    assert "<input:member_id>" in text  # the FILL echo, the route, the heading
    assert '"ref"' not in text and '"refs"' not in text
    dispatched = memory.last.of(EventType.ACTION_DISPATCHED)
    assert dispatched[1].payload.action_type == "FILL"
    assert dispatched[1].payload.value == "<input:member_id>"
    assert all(json.loads(line)["redaction_applied"] is True for line in memory.last.lines)
    # no request/prompt/completion text was persisted at all
    for request in llm.requests:
        assert request.model_dump_json() not in text
    assert "visible_text_outline" not in text and "instructions" not in text


def test_a_synthetic_secret_in_model_output_cannot_survive():
    secret = "hunter2-SENTINEL"

    def leaky(request):
        return ActDecision(
            observation_index=request.observation.observation_index,
            action_type="NAVIGATE",
            route="/members/search",
            intent_summary=f"open search (password: {secret})",
        )

    llm = ScriptedLLM(
        [leaky, lambda r: decision_for(Finish(), r), lambda r: decision_for(Finish(), r)]
    )
    # The redactor knows secret-shaped keys and *registered* values; an arbitrary secret in prose
    # is the documented V1 limit — so register it the way a caller registers a known value.
    memory = MemoryEvidence()
    recorder = recorder_for(memory, redactor=Redactor(sensitive_values={"secret": secret}))
    inner = scripted_surface(recorder)
    agent, _, _ = agent_for(RecordingSurface(inner), llm, recorder=recorder)
    agent.run(GOAL, {"member_id": MEMBER})
    assert secret not in memory.text and "<secret>" in memory.text


def test_bound_inputs_are_registered_before_the_first_state_bearing_event():
    """A page that already echoes the id at the very first observation cannot leak it."""
    memory = MemoryEvidence()
    recorder = recorder_for(memory)
    inner = scripted_surface(recorder)
    inner.path = f"/members/{MEMBER}"  # the browser is already on the member page
    llm = SemanticLLM(intents=[Read("cell", "row: Savings", "savings_balance"), Finish()])
    agent, _, _ = agent_for(RecordingSurface(inner), llm, recorder=recorder)
    result = agent.run(GOAL, {"member_id": MEMBER})
    assert result.stop_reason is StopReason.GOAL_REACHED
    first_observation = memory.last.of(EventType.OBSERVATION)[0].payload
    assert first_observation.url == f"{BASE_URL}/members/<input:member_id>"
    assert MEMBER not in first_observation.page_title
    assert MEMBER not in memory.text
    opened_redactor = memory.opened[0][2]
    assert opened_redactor.sensitive_labels == ("input:member_id",)


def test_persisted_trace_equals_the_in_memory_trace():
    """The trace is literal-free by construction, so redaction is a no-op on it."""
    result, _, _, memory = run()
    persisted = memory.events[-1].payload.trace
    assert persisted.model_dump(mode="json") == trace_summary(result.trace).model_dump(mode="json")
    assert persisted.steps[1].value_binding_kind == "INPUT_REF"
    assert persisted.steps[2].url_after_template == f"{BASE_URL}/members/{{member_id}}"


def test_the_real_jsonl_writer_persists_a_discovery_run(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    recorder = EvidenceRecorder(store.open_run)
    inner = scripted_surface(recorder)
    agent, _, _ = agent_for(RecordingSurface(inner), SemanticLLM(), recorder=recorder)
    result = agent.run(GOAL, {"member_id": MEMBER})
    path = store.events_path(RunKind.DISCOVERY, result.run_id)
    assert path.exists() and path.parent.parent.name == "discovery"
    events = store.read_events(path)
    assert events[0].event_type is EventType.DISCOVERY_STARTED
    assert events[-1].event_type is EventType.DISCOVERY_ENDED
    raw = path.read_text(encoding="utf-8")
    assert MEMBER not in raw and "<input:member_id>" in raw


# --- fail closed (A4) -----------------------------------------------------------------------------


def test_terminal_evidence_failure_after_verified_success_is_not_released():
    memory = MemoryEvidence(fail_on={EventType.DISCOVERY_ENDED})
    result, surface, inner, memory = run(memory=memory)
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.EVIDENCE_ERROR
    assert result.outputs == {} and result.verdict is None
    assert "was GOAL_REACHED" in result.detail.message
    assert "4 driver action(s) were attempted" in result.detail.message
    assert "none is repeated" in result.detail.message
    # nothing was redispatched, and exactly one terminal write was attempted, then nothing
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert inner.dispatched_actions == 4
    attempted = [e.event_type for e in memory.last.raw_seen]
    assert attempted.count(EventType.DISCOVERY_ENDED) == 1
    assert attempted[-1] is EventType.DISCOVERY_ENDED
    assert (
        memory.types[-1] is EventType.MODEL_CALL
    )  # the FINISH decision was the last persisted event
    assert EventType.DISCOVERY_ENDED not in memory.types


def test_evidence_failure_on_model_call_write_means_zero_dispatch():
    memory = MemoryEvidence(fail_on={EventType.MODEL_CALL})
    result, surface, inner, memory = run(memory=memory)
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.EVIDENCE_ERROR
    assert "MODEL_CALL" in result.detail.message and "NOT attempted" in result.detail.message
    assert surface.acts == [] and inner.dispatched_actions == 0
    assert memory.types == [EventType.DISCOVERY_STARTED, EventType.OBSERVATION]


def test_evidence_failure_before_and_after_the_driver_call_wording():
    before = MemoryEvidence(fail_on={EventType.ACTION_DISPATCHED})
    result, surface, inner, _ = run(memory=before)
    assert result.detail.code is StopCode.EVIDENCE_ERROR
    assert "ACTION_DISPATCHED" in result.detail.message and "NOT attempted" in result.detail.message
    assert inner.dispatched_actions == 0 and inner.path == "about:blank"  # nothing happened

    after = MemoryEvidence(fail_on={EventType.ACTION_COMPLETED})
    result, surface, inner, _ = run(memory=after)
    assert result.detail.code is StopCode.EVIDENCE_ERROR
    assert "ACTION_COMPLETED" in result.detail.message and "WAS attempted" in result.detail.message
    assert inner.dispatched_actions == 1 and surface.act_types == ["NAVIGATE"]
    assert inner.path == "/members/search"  # the action happened and is never repeated


def test_evidence_failure_on_begin_run_means_zero_observations_and_zero_model_calls():
    llm = SemanticLLM()
    result, surface, inner, memory = run(llm, memory=MemoryEvidence(fail_open=True))
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.EVIDENCE_ERROR
    assert surface.observes == 0 and llm.calls == 0 and inner.dispatched_actions == 0
    assert memory.sinks == []


def test_after_an_evidence_failure_nothing_more_is_written():
    memory = MemoryEvidence(fail_on={EventType.OBSERVATION})
    result, _, _, memory = run(memory=memory)
    assert result.detail.code is StopCode.EVIDENCE_ERROR
    assert memory.types == [EventType.DISCOVERY_STARTED]
    raw = [e.event_type for e in memory.last.raw_seen]
    assert raw == [EventType.DISCOVERY_STARTED, EventType.OBSERVATION]  # no DISCOVERY_ENDED attempt


def test_a_surface_not_wired_to_the_recorder_fails_loudly_on_the_first_action():
    recorder = recorder_for()
    unwired = ScriptedSurface(base_url=BASE_URL)  # no listener
    agent, _, _ = agent_for(RecordingSurface(unwired), SemanticLLM(), recorder=recorder)
    with pytest.raises(RuntimeError, match="not wired to the run's evidence recorder"):
        agent.run(GOAL, {"member_id": MEMBER})
