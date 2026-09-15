"""Fake ``LLMClient``s for the discovery tests. Zero network, zero SDK.

``ScriptedLLM`` answers each ``decide`` with the next scripted turn: a ``DiscoveryDecision``, a
callable ``(request) -> DiscoveryDecision``, or an exception to raise. It records every request it
received — the proof that each turn saw a fresh, model-safe observation.

``SemanticLLM`` plays a careless-but-plausible model: it holds semantic *intents* ("fill the
textbox named Member ID with INPUT_REF member_id", "read the cell in a row: Savings context") and
resolves each against the model-safe observation it is given, picking the **first** matching ref.
That is exactly the behaviour the application's ambiguity guard must catch: on the ambiguous
page it happily picks one of two equivalent Savings cells. On validation feedback it re-runs the
same intent (the default) or delegates to ``on_feedback``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from cua.artifact import InputRef, LiteralValue
from cua.llm import (
    ActDecision,
    BlockedDecision,
    BlockedReason,
    CallMetadata,
    DecisionReply,
    DiscoveryDecision,
    DiscoveryRequest,
    FinishDecision,
    Observation,
)

Turn = DiscoveryDecision | Callable[[DiscoveryRequest], DiscoveryDecision] | Exception


def metadata(provider: str = "fake", model_id: str = "fake-1", n: int = 1) -> CallMetadata:
    return CallMetadata(
        provider=provider,
        model_id_requested=model_id,
        model_id_reported=model_id,
        provider_response_id=f"fake_resp_{n}",
        latency_ms=1,
        input_tokens=10,
        output_tokens=5,
    )


class ScriptedLLM:
    def __init__(self, turns: Sequence[Turn], *, model_id: str = "fake-1") -> None:
        self._turns = list(turns)
        self._model_id = model_id
        self.requests: list[DiscoveryRequest] = []
        self.calls = 0

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return self._model_id

    def decide(self, request: DiscoveryRequest) -> DecisionReply:
        self.requests.append(request)
        self.calls += 1
        if not self._turns:
            raise AssertionError("scripted LLM ran out of turns")
        turn = self._turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        decision = turn(request) if callable(turn) else turn
        return DecisionReply(decision=decision, call=metadata(n=self.calls))


# --- semantic intents ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Nav:
    route: str
    intent: str = "open the entry route"


@dataclass(frozen=True)
class Fill:
    role: str
    name: str
    value: InputRef | LiteralValue
    intent: str = "enter the declared input"


@dataclass(frozen=True)
class Click:
    role: str
    name: str
    intent: str = "activate the control"


@dataclass(frozen=True)
class Read:
    role: str
    context_contains: str
    output_name: str
    intent: str = "read the requested value"


@dataclass(frozen=True)
class Finish:
    intent: str = "the requested value has been read"


@dataclass(frozen=True)
class Blocked:
    reason: BlockedReason
    evidence_role: str | None = None
    intent: str = "cannot proceed from this screen"


Intent = Nav | Fill | Click | Read | Finish | Blocked


def _first_ref(observation: Observation, role: str, name: str | None, context: str | None):
    for element in observation.elements:
        if element.role != role:
            continue
        if name is not None and element.accessible_name != name:
            continue
        if context is not None and context not in (element.context_hint or ""):
            continue
        return element.ref
    return None


def decision_for(intent: Intent, request: DiscoveryRequest) -> DiscoveryDecision:
    """Resolve one semantic intent against the request's (model-safe) observation."""
    obs = request.observation
    idx = obs.observation_index
    if isinstance(intent, Nav):
        return ActDecision(
            observation_index=idx,
            action_type="NAVIGATE",
            route=intent.route,
            intent_summary=intent.intent,
        )
    if isinstance(intent, Fill):
        ref = _first_ref(obs, intent.role, intent.name, None)
        if ref is None:
            return BlockedDecision(
                observation_index=idx,
                reason_code=BlockedReason.CONTROL_MISSING,
                intent_summary=f"no {intent.role} named {intent.name}",
            )
        return ActDecision(
            observation_index=idx,
            action_type="FILL",
            ref=ref,
            value=intent.value,
            intent_summary=intent.intent,
        )
    if isinstance(intent, Click):
        ref = _first_ref(obs, intent.role, intent.name, None)
        if ref is None:
            return BlockedDecision(
                observation_index=idx,
                reason_code=BlockedReason.CONTROL_MISSING,
                intent_summary=f"no {intent.role} named {intent.name}",
            )
        return ActDecision(
            observation_index=idx, action_type="CLICK", ref=ref, intent_summary=intent.intent
        )
    if isinstance(intent, Read):
        ref = _first_ref(obs, intent.role, None, intent.context_contains)
        if ref is None:
            return BlockedDecision(
                observation_index=idx,
                reason_code=BlockedReason.CONTROL_MISSING,
                intent_summary=f"no {intent.role} in {intent.context_contains}",
            )
        return ActDecision(
            observation_index=idx,
            action_type="READ",
            ref=ref,
            output_name=intent.output_name,
            intent_summary=intent.intent,
        )
    if isinstance(intent, Finish):
        return FinishDecision(observation_index=idx, intent_summary=intent.intent)
    assert isinstance(intent, Blocked)
    evidence_ref = (
        _first_ref(obs, intent.evidence_role, None, None) if intent.evidence_role else None
    )
    return BlockedDecision(
        observation_index=idx,
        reason_code=intent.reason,
        evidence_ref=evidence_ref,
        intent_summary=intent.intent,
    )


FLAGSHIP_INTENTS: tuple[Intent, ...] = (
    Nav("/members/search"),
    Fill("textbox", "Member ID", InputRef(input_name="member_id")),
    Click("button", "Search"),
    Read("cell", "row: Savings", "savings_balance"),
    Finish(),
)


@dataclass
class SemanticLLM:
    """A fake model that decides from the observation it is shown, one intent per step."""

    intents: list[Intent] = field(default_factory=lambda: list(FLAGSHIP_INTENTS))
    on_feedback: Callable[[DiscoveryRequest, Intent], DiscoveryDecision] | None = None
    model_id_value: str = "fake-semantic-1"
    requests: list[DiscoveryRequest] = field(default_factory=list)
    calls: int = 0
    _current: Intent | None = None

    @property
    def provider(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str:
        return self.model_id_value

    def decide(self, request: DiscoveryRequest) -> DecisionReply:
        self.requests.append(request)
        self.calls += 1
        if request.feedback is not None and self._current is not None:
            if self.on_feedback is not None:
                decision = self.on_feedback(request, self._current)
            else:
                decision = decision_for(self._current, request)  # careless: same intent again
        else:
            if not self.intents:
                raise AssertionError("semantic LLM ran out of intents")
            self._current = self.intents.pop(0)
            decision = decision_for(self._current, request)
        return DecisionReply(
            decision=decision, call=metadata(model_id=self.model_id_value, n=self.calls)
        )
