"""The ambiguity guard: two equivalent Savings cells are never a valid selection, however
confidently the model picks one (ARCHITECTURE §7 fail-closed, carried into discovery)."""

from __future__ import annotations

from cua.discovery import (
    READ_SAVINGS_BALANCE_GOAL,
    DecisionValidator,
    InputTemplater,
    Invalid,
    NormalizedTrace,
    PlaceholderStyle,
    StopCode,
    StopReason,
    ValidAct,
    ValidationCode,
    derive_semantic_target,
    identity_matches,
)
from cua.evidence import EventType
from cua.llm import ActDecision, BlockedReason
from cua.surface import find
from tests.discovery.fake_llm import Blocked, SemanticLLM, decision_for
from tests.discovery.support import (
    BASE_URL,
    ENTRY_ROUTES,
    agent_for,
    detail_snapshot,
    scripted_surface,
)
from tests.evidence.memory_sink import MemoryEvidence
from tests.replay.recording_surface import RecordingSurface
from tests.replay.support import recorder_for

GOAL = READ_SAVINGS_BALANCE_GOAL
BOUND = {"member_id": "M1001"}


def validator() -> DecisionValidator:
    return DecisionValidator(
        goal=GOAL,
        bound_inputs=BOUND,
        base_url=BASE_URL,
        navigation_routes=ENTRY_ROUTES,
        trace_templater=InputTemplater(BOUND, PlaceholderStyle.TRACE),
    )


def empty_trace() -> NormalizedTrace:
    return NormalizedTrace(
        goal_name=GOAL.name,
        inputs_declared={"member_id": "STRING"},
        outputs_declared={"savings_balance": "DECIMAL"},
        provider="fake",
        model_id="fake-1",
        discovery_run_id="run_x",
        session_id="sess_x",
    )


def read(ref: str, index: int) -> ActDecision:
    return ActDecision(
        observation_index=index,
        action_type="READ",
        ref=ref,
        output_name="savings_balance",
        intent_summary="read the savings balance",
    )


def test_the_persisted_descriptor_for_a_cell_drops_the_content_name():
    snapshot = detail_snapshot()
    cell = find(snapshot, role="cell", context_hint="table: Accounts > row: Savings")[0]
    target = derive_semantic_target(cell)
    assert target.model_dump() == {
        "role": "cell",
        "accessible_name": None,
        "context_hint": "table: Accounts > row: Savings",
    }
    assert identity_matches(snapshot, target) == 1


def test_either_savings_cell_on_the_ambiguous_page_is_rejected():
    snapshot = detail_snapshot(ambiguous=True)
    cells = find(snapshot, role="cell", context_hint="table: Accounts > row: Savings")
    assert len(cells) == 2 and {c.value for c in cells} == {"$15,275.00", "$250.00"}
    for cell in cells:
        outcome = validator().validate(read(cell.ref, snapshot.step_index), snapshot, empty_trace())
        assert isinstance(outcome, Invalid)
        assert outcome.code is ValidationCode.AMBIGUOUS_TARGET
        assert "2 equivalent candidates" in outcome.feedback
    checking = find(snapshot, role="cell", context_hint="table: Accounts > row: Checking")[0]
    assert isinstance(
        validator().validate(read(checking.ref, 3), snapshot, empty_trace()), ValidAct
    )


def test_agent_with_a_careless_model_dead_ends_without_reading_either_candidate():
    memory = MemoryEvidence()
    recorder = recorder_for(memory)
    inner = scripted_surface(recorder, ambiguous=True)
    surface = RecordingSurface(inner)
    llm = SemanticLLM()  # picks the first Savings cell, and again on feedback
    agent, _, _ = agent_for(surface, llm, recorder=recorder)
    result = agent.run(GOAL, {"member_id": "M1001"})
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.AMBIGUOUS_TARGET
    assert result.detail.validation_codes == ["AMBIGUOUS_TARGET", "AMBIGUOUS_TARGET"]
    assert "READ" not in surface.act_types
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK"] and inner.dispatched_actions == 3
    assert result.outputs == {} and result.corrective_retries == 1
    assert llm.requests[-1].feedback is not None
    assert llm.requests[-1].feedback.startswith("INVALID(AMBIGUOUS_TARGET)")
    dispatched = [e.payload.action_type for e in memory.last.of(EventType.ACTION_DISPATCHED)]
    assert dispatched == ["NAVIGATE", "FILL", "CLICK"]
    calls = memory.last.of(EventType.MODEL_CALL)
    assert [c.payload.validation.code for c in calls[-2:]] == ["AMBIGUOUS_TARGET"] * 2
    assert memory.events[-1].payload.stop_reason == "DEAD_END"


def test_a_model_that_reports_the_ambiguity_ends_as_a_dead_end_too():
    recorder = recorder_for()
    inner = scripted_surface(recorder, ambiguous=True)
    surface = RecordingSurface(inner)
    llm = SemanticLLM(
        on_feedback=lambda request, _i: decision_for(Blocked(BlockedReason.UI_AMBIGUOUS), request)
    )
    agent, _, _ = agent_for(surface, llm, recorder=recorder)
    result = agent.run(GOAL, {"member_id": "M1001"})
    assert result.stop_reason is StopReason.DEAD_END
    assert result.detail.code is StopCode.REPORTED_BLOCKED
    assert "UI_AMBIGUOUS" in result.detail.message
    assert "READ" not in surface.act_types and result.outputs == {}
