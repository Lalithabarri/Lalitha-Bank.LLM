"""policy/ — ActionGate, allowlist, risk tiers.

Owns: ``ActionGate`` — the single authorization chokepoint (ARCHITECTURE §3, §8, D09, D10).
Must never: accept LLM input. The model proposes; it never modifies policy or risk.
"""

from cua.policy.action_gate import (
    ActionGate,
    DenyReason,
    GateDecision,
    GateRequest,
    GateResult,
    ResolvedTarget,
)
from cua.policy.config import PolicyConfig
from cua.policy.risk import DEFAULT_RISK, RiskRule, RiskTier, classify_risk, effective_risk
from cua.policy.routes import any_route_matches, route_matches, split_origin_and_path

__all__ = [
    "DEFAULT_RISK",
    "ActionGate",
    "DenyReason",
    "GateDecision",
    "GateRequest",
    "GateResult",
    "PolicyConfig",
    "ResolvedTarget",
    "RiskRule",
    "RiskTier",
    "any_route_matches",
    "classify_risk",
    "effective_risk",
    "route_matches",
    "split_origin_and_path",
]
