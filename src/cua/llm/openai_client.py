"""OpenAIResponsesClient — the one module in ``cua`` that imports the OpenAI SDK (D24).

One ``decide`` == one Responses API call == one provider HTTP attempt:

    render(request) -> (instructions, user text)
    -> client.responses.parse(model, instructions, input, text_format=DecisionWire,
                              store=False, timeout=min(call timeout, seconds remaining))
    -> DecisionWire (strict Structured Outputs: every field required, nullable where not
       applicable, additionalProperties false) -> DiscoveryDecision
    -> DecisionReply(decision, CallMetadata)

Settings that are not negotiable in V1: ``store=False`` on every request (nothing is retained
server-side, so ``previous_response_id`` chaining is impossible and never used); no background
mode; no tools; no sampling parameters; ``TRANSPORT_RETRIES = 0`` — the SDK never retries a
429/5xx/connection failure on its own, so every ``MODEL_CALL`` evidence event is exactly one HTTP
attempt and the only retry in the system is the application's single, evidenced corrective retry.

The credential is read from ``OPENAI_API_KEY`` when the client is constructed and handed to the
SDK; it is never stored on this object, logged, rendered into a prompt, or included in an error.
Provider failures become ``ProviderError`` with a fixed-template message (kind, HTTP status) —
never the response body, which may echo the request. The original SDK exception is chained in
memory only. Free-form prose is never parsed as JSON: the SDK's typed ``parse`` path is the only
way a decision comes back. No SDK type appears in a public signature or return value, and the
response object is dropped after mapping.
"""

from __future__ import annotations

import os
import time
from typing import Literal

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    InternalServerError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, ValidationError

from cua.artifact import InputRef, LiteralValue
from cua.llm.contract import (
    ActDecision,
    BlockedDecision,
    BlockedReason,
    CallMetadata,
    DecisionReply,
    DiscoveryDecision,
    DiscoveryRequest,
    FinishDecision,
    ModelOutputError,
    ProviderError,
    ProviderErrorKind,
)
from cua.llm.prompt import render

PROVIDER = "openai"
DEFAULT_MODEL_ID = "gpt-5.6-sol"
MODEL_ENV = "CUA_OPENAI_MODEL"
KEY_ENV = "OPENAI_API_KEY"
TRANSPORT_RETRIES = 0  # one MODEL_CALL event == one HTTP attempt (A3); not a parameter
DEFAULT_CALL_TIMEOUT_S = 90.0
MIN_CALL_TIMEOUT_S = 1.0


class DecisionWire(BaseModel):
    """The strict output schema: flat, every field required, nullable where not applicable."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["ACT", "FINISH", "REPORT_BLOCKED"]
    observation_index: int
    action_type: Literal["NAVIGATE", "CLICK", "FILL", "SELECT", "READ"] | None
    ref: str | None
    value_kind: Literal["LITERAL", "INPUT_REF"] | None
    value_literal: str | None
    value_input_name: str | None
    route: str | None
    output_name: str | None
    blocked_reason: (
        Literal["BUSINESS_OUTCOME", "UI_AMBIGUOUS", "CONTROL_MISSING", "UNEXPECTED_STATE", "OTHER"]
        | None
    )
    evidence_ref: str | None
    intent_summary: str


def to_decision(wire: DecisionWire) -> DiscoveryDecision:
    """Wire -> domain. Inconsistent combinations and domain validation failures are
    ``ModelOutputError`` (they count against the corrective retry)."""
    try:
        if wire.kind == "ACT":
            if wire.action_type is None:
                raise ModelOutputError("ACT requires action_type")
            value: InputRef | LiteralValue | None = None
            if wire.value_kind == "INPUT_REF":
                if not wire.value_input_name:
                    raise ModelOutputError("INPUT_REF requires value_input_name")
                value = InputRef(input_name=wire.value_input_name)
            elif wire.value_kind == "LITERAL":
                if wire.value_literal is None:
                    raise ModelOutputError("LITERAL requires value_literal")
                value = LiteralValue(value=wire.value_literal)
            return ActDecision(
                observation_index=wire.observation_index,
                action_type=wire.action_type,
                ref=wire.ref,
                value=value,
                route=wire.route,
                output_name=wire.output_name,
                intent_summary=wire.intent_summary,
            )
        if wire.kind == "FINISH":
            return FinishDecision(
                observation_index=wire.observation_index, intent_summary=wire.intent_summary
            )
        if wire.blocked_reason is None:
            raise ModelOutputError("REPORT_BLOCKED requires blocked_reason")
        return BlockedDecision(
            observation_index=wire.observation_index,
            reason_code=BlockedReason(wire.blocked_reason),
            evidence_ref=wire.evidence_ref,
            intent_summary=wire.intent_summary,
        )
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ModelOutputError(f"decision rejected: {problems}") from exc


class OpenAIResponsesClient:
    def __init__(
        self,
        *,
        model_id: str | None = None,
        api_key: str | None = None,
        call_timeout_s: float = DEFAULT_CALL_TIMEOUT_S,
        max_output_tokens: int | None = None,
        http_client: object | None = None,  # tests: an httpx2.Client with a mock transport
        base_url: str | None = None,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get(KEY_ENV)
        if not key:
            raise ProviderError(ProviderErrorKind.CONFIG, detail=f"{KEY_ENV} is not set")
        if call_timeout_s <= 0:
            raise ValueError("call_timeout_s must be > 0")
        self._model_id = model_id or os.environ.get(MODEL_ENV) or DEFAULT_MODEL_ID
        self._call_timeout_s = float(call_timeout_s)
        self._max_output_tokens = max_output_tokens
        kwargs: dict[str, object] = {
            "api_key": key,
            "max_retries": TRANSPORT_RETRIES,
            "timeout": self._call_timeout_s,
        }
        if http_client is not None:
            kwargs["http_client"] = http_client
        if base_url is not None:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)  # type: ignore[arg-type]
        del key

    def __repr__(self) -> str:
        return f"OpenAIResponsesClient(model_id={self._model_id!r})"

    @property
    def provider(self) -> str:
        return PROVIDER

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def transport_retries(self) -> int:
        return int(self._client.max_retries)

    def decide(self, request: DiscoveryRequest) -> DecisionReply:
        instructions, user_text = render(request)
        timeout = self._call_timeout_s
        if request.seconds_remaining is not None:
            timeout = max(MIN_CALL_TIMEOUT_S, min(timeout, request.seconds_remaining))
        extra: dict[str, object] = {}
        if self._max_output_tokens is not None:
            extra["max_output_tokens"] = self._max_output_tokens
        started = time.monotonic()
        try:
            response = self._client.responses.parse(
                model=self._model_id,
                instructions=instructions,
                input=user_text,
                text_format=DecisionWire,
                store=False,
                timeout=timeout,
                **extra,  # type: ignore[arg-type]
            )
        except AuthenticationError as exc:
            raise _provider_error(ProviderErrorKind.AUTH, exc) from exc
        except PermissionDeniedError as exc:
            raise _provider_error(ProviderErrorKind.AUTH, exc) from exc
        except RateLimitError as exc:
            raise _provider_error(ProviderErrorKind.RATE_LIMIT, exc) from exc
        except InternalServerError as exc:
            raise _provider_error(ProviderErrorKind.SERVER, exc) from exc
        except APIStatusError as exc:
            raise _provider_error(ProviderErrorKind.OTHER, exc) from exc
        except APITimeoutError as exc:
            raise ProviderError(ProviderErrorKind.TIMEOUT) from exc
        except APIConnectionError as exc:
            raise ProviderError(ProviderErrorKind.CONNECTION) from exc
        except OpenAIError as exc:
            raise ProviderError(ProviderErrorKind.OTHER) from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        status = getattr(response, "status", None)
        if status == "incomplete":
            details = getattr(response, "incomplete_details", None)
            reason = getattr(details, "reason", None) or "unknown"
            raise ProviderError(ProviderErrorKind.INCOMPLETE, detail=f"reason={reason}")
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            if _refused(response):
                raise ProviderError(ProviderErrorKind.REFUSAL)
            raise ModelOutputError("the response carried no structured decision")
        decision = to_decision(parsed)
        usage = getattr(response, "usage", None)
        call = CallMetadata(
            provider=PROVIDER,
            model_id_requested=self._model_id,
            model_id_reported=getattr(response, "model", None),
            provider_response_id=getattr(response, "id", None),
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "input_tokens", None) if usage else None,
            output_tokens=getattr(usage, "output_tokens", None) if usage else None,
        )
        return DecisionReply(decision=decision, call=call)


def _provider_error(kind: ProviderErrorKind, exc: APIStatusError) -> ProviderError:
    """Status and request id only: the body may echo the request and is never surfaced."""
    return ProviderError(
        kind,
        status_code=getattr(exc, "status_code", None),
        provider_request_id=getattr(exc, "request_id", None),
    )


def _refused(response: object) -> bool:
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", None) or []:
            if getattr(content, "type", None) == "refusal":
                return True
    return False


__all__ = [
    "DEFAULT_CALL_TIMEOUT_S",
    "DEFAULT_MODEL_ID",
    "KEY_ENV",
    "MODEL_ENV",
    "PROVIDER",
    "TRANSPORT_RETRIES",
    "DecisionWire",
    "OpenAIResponsesClient",
    "to_decision",
]
