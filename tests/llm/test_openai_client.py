"""OpenAIResponsesClient against a mocked HTTP transport — zero network.

Proves what actually leaves the process: the outbound body uses the intended model, strict
Structured Outputs, ``store: false``, no background mode, no ``previous_response_id``; the SDK is
configured with zero transport retries (a 429 is exactly one HTTP request); provider errors map to
fixed-template ``ProviderError``s without the key or the body; a missing credential fails before
any request; and — the provider-boundary privacy proof — a request built from a raw UI state that
contains the bound member id never puts that id on the wire.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

import httpx2
import pytest

from cua.discovery import (
    READ_SAVINGS_BALANCE_GOAL,
    DiscoveryAgent,
    DiscoveryConfig,
    DiscoveryDeps,
    InputTemplater,
    PlaceholderStyle,
    StopReason,
    to_observation,
)
from cua.hitl import ControlOwner
from cua.llm import (
    ActDecision,
    BlockedDecision,
    DeclaredInput,
    DeclaredOutput,
    DiscoveryRequest,
    FinishDecision,
    GoalStatement,
    HistoryEntry,
    HistoryTarget,
    ModelOutputError,
    ProviderError,
    ProviderErrorKind,
)
from cua.llm.openai_client import (
    DEFAULT_MODEL_ID,
    TRANSPORT_RETRIES,
    DecisionWire,
    OpenAIResponsesClient,
    to_decision,
)
from cua.policy import ActionGate
from tests.discovery.support import (
    BASE_URL,
    ENTRY_ROUTES,
    detail_snapshot,
    scripted_surface,
    search_snapshot,
)
from tests.replay.fake_clock import FakeClock
from tests.replay.recording_surface import RecordingSurface
from tests.replay.support import policy_for, recorder_for

KEY = "sk-test-SENTINEL-KEY-0123456789"
MEMBER = "M1001"
ARTIFACT_STRINGS = ("s1_open_search", "s4_read_savings", "success_checkpoint", "known_outcomes")


# --- a mocked provider ----------------------------------------------------------------------------


def wire(**fields) -> dict:
    base = dict(
        kind="FINISH",
        observation_index=1,
        action_type=None,
        ref=None,
        value_kind=None,
        value_literal=None,
        value_input_name=None,
        route=None,
        output_name=None,
        blocked_reason=None,
        evidence_ref=None,
        intent_summary="done",
    )
    base.update(fields)
    return base


def response_json(
    decision: dict | None, *, status="completed", refusal=False, model="gpt-5.6-sol-2026-01-01"
):
    content = (
        [{"type": "refusal", "refusal": "no"}]
        if refusal
        else [{"type": "output_text", "text": json.dumps(decision), "annotations": []}]
    )
    return {
        "id": "resp_mock_1",
        "object": "response",
        "created_at": 1700000000,
        "model": model,
        "status": status,
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": content,
            }
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "total_tokens": 150,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        },
        "parallel_tool_calls": True,
        "tool_choice": "auto",
        "tools": [],
        "error": None,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "instructions": None,
        "metadata": {},
        "temperature": 1.0,
        "top_p": 1.0,
    }


class MockProvider:
    """Records every outbound HTTP request and answers with scripted responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        request.read()
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("mock provider ran out of responses")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        status, body = response
        return httpx2.Response(status, json=body)

    @property
    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


def client_for(provider: MockProvider, **kwargs) -> OpenAIResponsesClient:
    kwargs.setdefault("api_key", KEY)
    return OpenAIResponsesClient(
        http_client=httpx2.Client(transport=httpx2.MockTransport(provider)), **kwargs
    )


def goal_statement() -> GoalStatement:
    return GoalStatement(
        name="read_savings_balance",
        description="Find the member identified by member_id and read the Savings balance.",
        inputs=[DeclaredInput(name="member_id", type="STRING", placeholder="<input:member_id>")],
        outputs=[DeclaredOutput(name="savings_balance", type="DECIMAL")],
    )


def request_for(snapshot, *, history=(), seconds_remaining=250.0) -> DiscoveryRequest:
    return DiscoveryRequest(
        goal=goal_statement(),
        observation=to_observation(
            snapshot, InputTemplater({"member_id": MEMBER}, PlaceholderStyle.MODEL)
        ),
        history=list(history),
        step_index=snapshot.step_index,
        steps_remaining=25 - snapshot.step_index,
        seconds_remaining=seconds_remaining,
        navigation_routes=list(ENTRY_ROUTES),
    )


# --- request settings -----------------------------------------------------------------------------


def test_outbound_body_uses_the_intended_model_strict_schema_and_store_false():
    provider = MockProvider([(200, response_json(wire()))])
    client = client_for(provider, model_id="gpt-5.6-sol")
    reply = client.decide(request_for(search_snapshot()))
    assert isinstance(reply.decision, FinishDecision)
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.method == "POST" and str(request.url).endswith("/v1/responses")
    body = provider.bodies[0]
    assert body["model"] == "gpt-5.6-sol"
    assert body["store"] is False
    fmt = body["text"]["format"]
    assert fmt["type"] == "json_schema" and fmt["strict"] is True
    schema = fmt["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"]) == set(DecisionWire.model_fields)
    assert "previous_response_id" not in body and "background" not in body
    assert "tools" not in body and "temperature" not in body and "metadata" not in body
    assert set(body) == {"model", "instructions", "input", "store", "text"}


def test_metadata_is_provenance_only_and_the_response_object_is_dropped():
    provider = MockProvider([(200, response_json(wire()))])
    reply = client_for(provider).decide(request_for(search_snapshot()))
    call = reply.call
    assert call.provider == "openai" and call.model_id_requested == DEFAULT_MODEL_ID
    assert call.model_id_reported == "gpt-5.6-sol-2026-01-01"
    assert call.provider_response_id == "resp_mock_1"
    assert call.input_tokens == 120 and call.output_tokens == 30
    assert call.latency_ms is not None and call.latency_ms >= 0
    assert set(reply.model_dump()) == {"decision", "call"}


def test_transport_retries_are_zero_and_a_429_is_exactly_one_http_request():
    assert TRANSPORT_RETRIES == 0
    provider = MockProvider(
        [
            (429, {"error": {"message": "slow down", "type": "rate_limit"}}),
            (200, response_json(wire())),
        ]
    )
    client = client_for(provider)
    assert client.transport_retries == 0
    with pytest.raises(ProviderError) as info:
        client.decide(request_for(search_snapshot()))
    assert info.value.kind is ProviderErrorKind.RATE_LIMIT and info.value.status_code == 429
    assert len(provider.requests) == 1  # no hidden retry consumed the second response
    assert provider.requests[0].headers.get("x-stainless-retry-count") == "0"


@pytest.mark.parametrize(
    "status, kind",
    [
        (401, ProviderErrorKind.AUTH),
        (403, ProviderErrorKind.AUTH),
        (404, ProviderErrorKind.OTHER),
        (500, ProviderErrorKind.SERVER),
        (503, ProviderErrorKind.SERVER),
        (400, ProviderErrorKind.OTHER),
    ],
)
def test_status_errors_map_to_kinds_without_the_key_or_the_body(status, kind):
    body_secret = "BODY-ECHO-OF-REQUEST-M1001"
    provider = MockProvider([(status, {"error": {"message": body_secret, "type": "x"}})])
    with pytest.raises(ProviderError) as info:
        client_for(provider).decide(request_for(search_snapshot()))
    exc = info.value
    assert exc.kind is kind and exc.status_code == status
    assert str(exc) == f"provider call failed: {kind.value} (HTTP {status})"
    assert KEY not in str(exc) and body_secret not in str(exc)
    assert len(provider.requests) == 1


def test_connection_and_timeout_errors():
    provider = MockProvider([httpx2.ConnectError("refused")])
    with pytest.raises(ProviderError) as info:
        client_for(provider).decide(request_for(search_snapshot()))
    assert info.value.kind is ProviderErrorKind.CONNECTION and info.value.status_code is None
    provider = MockProvider([httpx2.ReadTimeout("slow")])
    with pytest.raises(ProviderError) as info:
        client_for(provider).decide(request_for(search_snapshot()))
    assert info.value.kind is ProviderErrorKind.TIMEOUT
    assert str(info.value) == "provider call failed: TIMEOUT"


def test_refusal_and_incomplete_are_provider_errors_not_decisions():
    provider = MockProvider([(200, response_json(None, refusal=True))])
    with pytest.raises(ProviderError) as info:
        client_for(provider).decide(request_for(search_snapshot()))
    assert info.value.kind is ProviderErrorKind.REFUSAL
    provider = MockProvider([(200, response_json(wire(), status="incomplete"))])
    with pytest.raises(ProviderError) as info:
        client_for(provider).decide(request_for(search_snapshot()))
    assert info.value.kind is ProviderErrorKind.INCOMPLETE
    assert "max_output_tokens" in str(info.value)


def test_missing_credential_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider = MockProvider([])
    with pytest.raises(ProviderError) as info:
        OpenAIResponsesClient(http_client=httpx2.Client(transport=httpx2.MockTransport(provider)))
    assert info.value.kind is ProviderErrorKind.CONFIG
    assert provider.requests == []
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(ProviderError):
        OpenAIResponsesClient(http_client=httpx2.Client(transport=httpx2.MockTransport(provider)))


def test_model_id_is_runtime_configurable_and_the_repr_holds_no_key(monkeypatch):
    provider = MockProvider([])
    monkeypatch.setenv("CUA_OPENAI_MODEL", "gpt-other")
    client = client_for(provider)
    assert client.model_id == "gpt-other"
    assert client_for(provider, model_id="gpt-explicit").model_id == "gpt-explicit"
    monkeypatch.delenv("CUA_OPENAI_MODEL")
    assert client_for(provider).model_id == DEFAULT_MODEL_ID == "gpt-5.6-sol"
    assert KEY not in repr(client) and "sk-" not in repr(client)
    assert not any(KEY in str(v) for v in vars(client).values() if isinstance(v, str))


def test_call_timeout_is_bounded_by_the_remaining_run_time():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx2.Response(200, json=response_json(wire()))

    client = OpenAIResponsesClient(
        api_key=KEY,
        call_timeout_s=90.0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    client.decide(request_for(search_snapshot(), seconds_remaining=12.5))
    assert seen["timeout"]["read"] == 12.5
    client.decide(request_for(search_snapshot(), seconds_remaining=0.0))
    assert (
        seen["timeout"]["read"] == 1.0
    )  # never zero: a call is either made with a floor or not at all


# --- wire -> domain -------------------------------------------------------------------------------


def test_wire_to_domain_mapping_and_its_rejections():
    act = to_decision(
        DecisionWire(
            **wire(
                kind="ACT",
                action_type="FILL",
                ref="e10",
                value_kind="INPUT_REF",
                value_input_name="member_id",
            )
        )
    )
    assert isinstance(act, ActDecision) and act.value.input_name == "member_id"
    lit = to_decision(
        DecisionWire(
            **wire(
                kind="ACT",
                action_type="FILL",
                ref="e10",
                value_kind="LITERAL",
                value_literal="500.00",
            )
        )
    )
    assert lit.value.value == "500.00"
    blocked = to_decision(
        DecisionWire(
            **wire(kind="REPORT_BLOCKED", blocked_reason="BUSINESS_OUTCOME", evidence_ref="f6e7")
        )
    )
    assert isinstance(blocked, BlockedDecision) and blocked.evidence_ref == "f6e7"
    for bad in (
        wire(kind="ACT"),  # no action_type
        wire(kind="ACT", action_type="FILL", ref="e10", value_kind="INPUT_REF"),  # no input name
        wire(kind="ACT", action_type="FILL", ref="e10", value_kind="LITERAL"),  # no literal
        wire(kind="REPORT_BLOCKED"),  # no reason
        wire(intent_summary="css=#go"),  # domain validation: selector syntax
    ):
        with pytest.raises(ModelOutputError):
            to_decision(DecisionWire(**bad))


def test_inconsistent_wire_from_the_provider_is_a_model_output_error_not_a_crash():
    provider = MockProvider([(200, response_json(wire(kind="ACT")))])
    with pytest.raises(ModelOutputError, match="ACT requires action_type"):
        client_for(provider).decide(request_for(search_snapshot()))


# --- the provider-boundary privacy proof ----------------------------------------------------------


def test_the_actual_outbound_http_body_never_carries_the_bound_member_id():
    """A later-step request built from a raw UI state full of M1001 (URL, title, heading, cell)
    goes over the wire with placeholders only; the key rides only in the Authorization header."""
    raw = detail_snapshot(step_index=4)
    assert MEMBER in raw.url and MEMBER in raw.page_title
    assert any(MEMBER in e.accessible_name for e in raw.elements)
    history = [
        HistoryEntry(
            step_index=1,
            action_type="NAVIGATE",
            route="/members/search",
            url_after=f"{BASE_URL}/members/search",
        ),
        HistoryEntry(
            step_index=2,
            action_type="FILL",
            target=HistoryTarget(role="textbox", accessible_name="Member ID"),
            value_binding="INPUT_REF(member_id)",
            url_after=f"{BASE_URL}/members/search",
        ),
        HistoryEntry(
            step_index=3,
            action_type="CLICK",
            target=HistoryTarget(role="button", accessible_name="Search"),
            url_after=f"{BASE_URL}/members/<input:member_id>",
        ),
    ]
    provider = MockProvider([(200, response_json(wire(observation_index=4)))])
    client_for(provider).decide(request_for(raw, history=history))
    request = provider.requests[0]
    body_text = request.content.decode("utf-8")
    assert MEMBER not in body_text
    assert "<input:member_id>" in body_text
    assert "/members/<input:member_id>" in body_text
    assert "Member <input:member_id>" in body_text
    assert KEY not in body_text and KEY not in str(request.url)
    assert (
        request.headers["authorization"] == f"Bearer {KEY}"
    )  # the credential's one legitimate place
    for needle in ARTIFACT_STRINGS:
        assert needle not in body_text, needle
    # the raw snapshot's title/url (which carry the id) did not bypass the model-safe view
    assert raw.url not in body_text and raw.page_title not in body_text
    # refs did go out — the model needs them for this turn
    body = json.loads(body_text)
    payload = json.loads(body["input"])
    assert all(e["ref"] for e in payload["observation"]["elements"])
    assert payload["inputs_masked"] is True and payload["input_values"] == {}


def test_the_agent_with_the_real_adapter_over_a_mocked_provider_reaches_the_goal():
    """End to end through the real adapter (mocked transport): every outbound body is model-safe
    and the discovery reaches GOAL_REACHED with a typed output."""
    recorder = recorder_for()
    inner = scripted_surface(recorder)
    surface = RecordingSurface(inner)

    def scripted(handler_request: httpx2.Request) -> httpx2.Response:
        handler_request.read()
        outbound.append(handler_request)
        payload = json.loads(json.loads(handler_request.content)["input"])
        idx = payload["observation"]["observation_index"]
        elements = payload["observation"]["elements"]

        def ref(role, name=None, context=None):
            for e in elements:
                if (
                    e["role"] == role
                    and (name is None or e["accessible_name"] == name)
                    and (context is None or context in (e["context_hint"] or ""))
                ):
                    return e["ref"]
            raise AssertionError((role, name, context))

        if idx == 1:
            turn = wire(
                kind="ACT",
                action_type="NAVIGATE",
                route="/members/search",
                observation_index=idx,
                intent_summary="open the member search",
            )
        elif idx == 2:
            turn = wire(
                kind="ACT",
                action_type="FILL",
                ref=ref("textbox", "Member ID"),
                value_kind="INPUT_REF",
                value_input_name="member_id",
                observation_index=idx,
                intent_summary="enter the member id",
            )
        elif idx == 3:
            turn = wire(
                kind="ACT",
                action_type="CLICK",
                ref=ref("button", "Search"),
                observation_index=idx,
                intent_summary="search",
            )
        elif idx == 4:
            turn = wire(
                kind="ACT",
                action_type="READ",
                ref=ref("cell", context="row: Savings"),
                output_name="savings_balance",
                observation_index=idx,
                intent_summary="read the savings balance",
            )
        else:
            turn = wire(kind="FINISH", observation_index=idx, intent_summary="the balance was read")
        return httpx2.Response(200, json=response_json(turn))

    outbound: list[httpx2.Request] = []
    client = OpenAIResponsesClient(
        api_key=KEY, http_client=httpx2.Client(transport=httpx2.MockTransport(scripted))
    )
    gate = ActionGate(policy_for(BASE_URL), ControlOwner(), observer=recorder)
    agent = DiscoveryAgent(
        DiscoveryDeps(
            surface=surface, action_gate=gate, clock=FakeClock(), evidence=recorder, llm=client
        ),
        DiscoveryConfig(base_url=BASE_URL, navigation_routes=ENTRY_ROUTES),
    )
    result = agent.run(READ_SAVINGS_BALANCE_GOAL, {"member_id": MEMBER})
    assert result.stop_reason is StopReason.GOAL_REACHED
    assert result.outputs == {"savings_balance": Decimal("15275.00")}
    assert result.provider == "openai" and result.model_id == DEFAULT_MODEL_ID
    assert surface.act_types == ["NAVIGATE", "FILL", "CLICK", "READ"]
    assert len(outbound) == 5
    for request in outbound:
        text = request.content.decode("utf-8")
        assert MEMBER not in text and KEY not in text
        assert json.loads(request.content)["store"] is False
    assert any("<input:member_id>" in r.content.decode("utf-8") for r in outbound)
    env_key = os.environ.get("OPENAI_API_KEY")
    if env_key:
        assert all(env_key not in r.content.decode("utf-8") for r in outbound)
