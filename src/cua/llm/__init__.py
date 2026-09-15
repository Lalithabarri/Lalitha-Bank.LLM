"""llm/ — the provider-neutral model client, structured output, one corrective retry.

Owns: ``LLMClient`` and the request / decision contract (ARCHITECTURE §3; D24 supersedes D06's
Gemini choice for the implemented V1 provider).
Must never: be imported by replay. Reachable ONLY from discovery/ (§4).

This package exports the contract and the prompt renderer only. The concrete adapter,
``cua.llm.openai_client.OpenAIResponsesClient``, is the single module in ``cua`` that imports the
OpenAI SDK and is imported explicitly by a composition root — ``import cua.llm`` never loads it.
"""

from cua.llm.contract import (
    ACT_ACTION_TYPES,
    FORBIDDEN_INTENT_PATTERNS,
    MAX_INTENT_SUMMARY_LEN,
    ActDecision,
    BlockedDecision,
    BlockedReason,
    CallMetadata,
    DecisionKind,
    DecisionReply,
    DeclaredInput,
    DeclaredOutput,
    DiscoveryDecision,
    DiscoveryRequest,
    FinishDecision,
    GoalStatement,
    HistoryEntry,
    HistoryTarget,
    LLMClient,
    LLMError,
    ModelOutputError,
    Observation,
    ObservedElement,
    ProviderError,
    ProviderErrorKind,
)
from cua.llm.prompt import INJECTION_NOTICE, render, render_instructions, render_user_text

__all__ = [
    "ACT_ACTION_TYPES",
    "FORBIDDEN_INTENT_PATTERNS",
    "INJECTION_NOTICE",
    "MAX_INTENT_SUMMARY_LEN",
    "ActDecision",
    "BlockedDecision",
    "BlockedReason",
    "CallMetadata",
    "DecisionKind",
    "DecisionReply",
    "DeclaredInput",
    "DeclaredOutput",
    "DiscoveryDecision",
    "DiscoveryRequest",
    "FinishDecision",
    "GoalStatement",
    "HistoryEntry",
    "HistoryTarget",
    "LLMClient",
    "LLMError",
    "ModelOutputError",
    "Observation",
    "ObservedElement",
    "ProviderError",
    "ProviderErrorKind",
    "render",
    "render_instructions",
    "render_user_text",
]
