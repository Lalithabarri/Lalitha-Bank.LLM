"""The provider-neutral contract: a closed decision vocabulary with no room for code."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from cua.artifact import InputRef, LiteralValue
from cua.discovery import InputTemplater, PlaceholderStyle, to_observation
from cua.llm import (
    ACT_ACTION_TYPES,
    MAX_INTENT_SUMMARY_LEN,
    ActDecision,
    BlockedDecision,
    BlockedReason,
    CallMetadata,
    DecisionReply,
    DeclaredInput,
    DeclaredOutput,
    DiscoveryDecision,
    DiscoveryRequest,
    FinishDecision,
    GoalStatement,
    ModelOutputError,
    ProviderError,
    ProviderErrorKind,
)
from tests.discovery.support import detail_snapshot

DECISION = TypeAdapter(DiscoveryDecision)


def test_the_three_kinds_round_trip_through_json():
    act = ActDecision(
        observation_index=1,
        action_type="FILL",
        ref="e10",
        value=InputRef(input_name="member_id"),
        intent_summary="enter the member id",
    )
    finish = FinishDecision(observation_index=5, intent_summary="the balance has been read")
    blocked = BlockedDecision(
        observation_index=3,
        reason_code=BlockedReason.BUSINESS_OUTCOME,
        evidence_ref="f6e7",
        intent_summary="the console reports no such member",
    )
    for decision in (act, finish, blocked):
        back = DECISION.validate_json(DECISION.dump_json(decision))
        assert back == decision and type(back) is type(decision)


def test_unknown_kind_extra_fields_and_missing_echo_are_rejected():
    with pytest.raises(ValidationError):
        DECISION.validate_python({"kind": "PLAN", "observation_index": 1, "intent_summary": "x"})
    with pytest.raises(ValidationError):
        ActDecision(
            observation_index=1, action_type="CLICK", ref="e1", intent_summary="x", css="#a"
        )
    with pytest.raises(ValidationError):
        FinishDecision(intent_summary="x")  # no observation_index
    with pytest.raises(ValidationError):
        ActDecision(
            observation_index=1, action_type="CLICK", ref="e1", intent_summary="x", risk="SAFE_READ"
        )


@pytest.mark.parametrize("action", ["WAIT", "FINISH", "REPORT_BLOCKED", "SCROLL", "EVAL"])
def test_only_surface_actions_can_be_proposed(action):
    with pytest.raises(ValidationError):
        ActDecision(observation_index=1, action_type=action, ref="e1", intent_summary="x")
    assert {a.value for a in ACT_ACTION_TYPES} == {"NAVIGATE", "CLICK", "FILL", "SELECT", "READ"}


@pytest.mark.parametrize(
    "summary",
    [
        "click //button[@id='go']",
        "use css=#member-id",
        "xpath=//td[2]",
        "run document.querySelector('td')",
        "page.click('x')",
        "locator('td').nth(1)",
        "<script>alert(1)</script>",
        "import os; os.system('rm -rf /')",
        "def go(): pass",
        "`code`",
        "$(td).click()",
        "x" * (MAX_INTENT_SUMMARY_LEN + 1),
        "",
        "two\nlines",
    ],
)
def test_intent_summary_rejects_selectors_code_and_overlength(summary):
    with pytest.raises(ValidationError):
        FinishDecision(observation_index=1, intent_summary=summary)


@pytest.mark.parametrize(
    "summary",
    [
        "Open the member search page.",
        "Type the member id into the Member ID field",
        "Read the Savings balance from the Accounts table",
        "The page at http://127.0.0.1:8000/members/search is the entry route",
        "Default to the Search button",
    ],
)
def test_ordinary_audit_sentences_are_accepted(summary):
    assert (
        FinishDecision(observation_index=1, intent_summary=summary).intent_summary
        == summary.strip()
    )


def test_value_binding_is_the_artifact_vocabulary():
    literal = ActDecision(
        observation_index=1,
        action_type="FILL",
        ref="e1",
        value=LiteralValue(value="500.00"),
        intent_summary="enter the amount",
    )
    assert literal.value.kind == "LITERAL"
    with pytest.raises(ValidationError):
        ActDecision(
            observation_index=1,
            action_type="FILL",
            ref="e1",
            value={"kind": "RAW", "value": "x"},
            intent_summary="x",
        )


def test_provider_error_message_is_a_template_and_model_output_error_carries_feedback():
    exc = ProviderError(ProviderErrorKind.AUTH, status_code=401, detail="check OPENAI_API_KEY")
    assert str(exc) == "provider call failed: AUTH (HTTP 401): check OPENAI_API_KEY"
    assert exc.kind is ProviderErrorKind.AUTH and exc.status_code == 401
    assert str(ProviderError(ProviderErrorKind.CONNECTION)) == "provider call failed: CONNECTION"
    out = ModelOutputError("ACT requires action_type")
    assert out.feedback == "ACT requires action_type"


def test_request_and_reply_round_trip_and_are_frozen():
    request = DiscoveryRequest(
        goal=GoalStatement(
            name="read_savings_balance",
            description="d",
            inputs=[
                DeclaredInput(name="member_id", type="STRING", placeholder="<input:member_id>")
            ],
            outputs=[DeclaredOutput(name="savings_balance", type="DECIMAL")],
        ),
        observation=to_observation(
            detail_snapshot(), InputTemplater({"member_id": "M1001"}, PlaceholderStyle.MODEL)
        ),
        step_index=3,
        steps_remaining=22,
        navigation_routes=["/members/search"],
    )
    back = DiscoveryRequest.model_validate_json(request.model_dump_json())
    assert back == request and back.inputs_masked is True and back.input_values == {}
    with pytest.raises(ValidationError):
        request.step_index = 4  # type: ignore[misc]
    reply = DecisionReply(
        decision=FinishDecision(observation_index=3, intent_summary="done"),
        call=CallMetadata(provider="openai", model_id_requested="gpt-x"),
    )
    assert DecisionReply.model_validate_json(reply.model_dump_json()) == reply
    assert set(CallMetadata.model_fields) == {
        "provider",
        "model_id_requested",
        "model_id_reported",
        "provider_response_id",
        "latency_ms",
        "input_tokens",
        "output_tokens",
    }
