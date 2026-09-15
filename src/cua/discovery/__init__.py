"""discovery/ — Observe -> decide -> act loop, stopping reasons.

Owns: ``DiscoveryAgent`` and the normalized trace (ARCHITECTURE §3, §5).
Must never: call ``Surface.act()`` directly — every action goes through ActionGate.

Implemented at ARCHITECTURE §15 step 11: the goal and its deterministic verifier, the model-safe
observation, the decision validator with its ambiguity guard, the normalized trace, the result
vocabulary and the agent. This is the only package that reaches ``cua.llm`` (§4).
"""

from cua.discovery.agent import (
    PURPOSE_CORRECTIVE_RETRY,
    PURPOSE_DECIDE,
    DiscoveryAgent,
    DiscoveryConfig,
    DiscoveryDeps,
    assert_navigation_routes_within_policy,
)
from cua.discovery.goal import (
    READ_SAVINGS_BALANCE_GOAL,
    GoalSpec,
    GoalVerdict,
    GoalVerifier,
    InputEvidence,
    ReadSavingsBalanceVerifier,
    bind_goal_inputs,
)
from cua.discovery.observation import (
    MAX_ELEMENTS,
    derive_semantic_target,
    digest,
    identity_matches,
    input_evidence,
    to_observation,
)
from cua.discovery.result import DiscoveryResult, StopCode, StopDetail, StopReason
from cua.discovery.templating import MIN_TOKEN_LEN, InputTemplater, PlaceholderStyle, placeholder
from cua.discovery.trace import NormalizedTrace, OutcomeCandidate, SemanticTarget, TraceStep
from cua.discovery.validator import (
    CLICK_ROLES,
    FILL_ROLES,
    MAX_LITERAL_LEN,
    SELECT_ROLES,
    DecisionValidator,
    Invalid,
    ValidAct,
    Validated,
    ValidationCode,
    ValidBlocked,
    ValidFinish,
    normalize_route,
)

__all__ = [
    "CLICK_ROLES",
    "FILL_ROLES",
    "MAX_ELEMENTS",
    "MAX_LITERAL_LEN",
    "MIN_TOKEN_LEN",
    "PURPOSE_CORRECTIVE_RETRY",
    "PURPOSE_DECIDE",
    "READ_SAVINGS_BALANCE_GOAL",
    "SELECT_ROLES",
    "DecisionValidator",
    "DiscoveryAgent",
    "DiscoveryConfig",
    "DiscoveryDeps",
    "DiscoveryResult",
    "GoalSpec",
    "GoalVerdict",
    "GoalVerifier",
    "InputEvidence",
    "InputTemplater",
    "Invalid",
    "NormalizedTrace",
    "OutcomeCandidate",
    "PlaceholderStyle",
    "ReadSavingsBalanceVerifier",
    "SemanticTarget",
    "StopCode",
    "StopDetail",
    "StopReason",
    "TraceStep",
    "ValidAct",
    "ValidBlocked",
    "ValidFinish",
    "Validated",
    "ValidationCode",
    "assert_navigation_routes_within_policy",
    "bind_goal_inputs",
    "derive_semantic_target",
    "digest",
    "identity_matches",
    "input_evidence",
    "normalize_route",
    "placeholder",
    "to_observation",
]
