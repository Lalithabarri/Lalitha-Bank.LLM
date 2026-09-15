"""Deterministic action risk classification (D10, ARCHITECTURE §6/§8).

Risk describes an action's effect on world state — nothing else. Data sensitivity is a
separate axis and is not modelled here. Risk is assigned by static configuration over
``(action_type, route[, target name])``; the model or an artifact can declare a risk, but a
declaration can only ever *raise* the effective tier, never lower it.
"""

from enum import StrEnum

from cua.domain import ActionType, DomainModel
from cua.policy.routes import route_matches


class RiskTier(StrEnum):
    SAFE_READ = "SAFE_READ"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    IRREVERSIBLE = "IRREVERSIBLE"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]


_SEVERITY = {RiskTier.SAFE_READ: 0, RiskTier.REVERSIBLE_WRITE: 1, RiskTier.IRREVERSIBLE: 2}

# Conservative defaults when no rule matches: anything that submits or edits is a write.
DEFAULT_RISK: dict[ActionType, RiskTier] = {
    ActionType.NAVIGATE: RiskTier.SAFE_READ,
    ActionType.READ: RiskTier.SAFE_READ,
    ActionType.FILL: RiskTier.REVERSIBLE_WRITE,
    ActionType.SELECT: RiskTier.REVERSIBLE_WRITE,
    ActionType.CLICK: RiskTier.REVERSIBLE_WRITE,
}


class RiskRule(DomainModel):
    """One classification rule. ``target_name`` narrows it to one control (e.g. a commit button)."""

    action_type: ActionType
    route_pattern: str
    target_name: str | None = None
    risk: RiskTier

    def matches(self, action_type: ActionType, route: str, target_name: str | None) -> bool:
        if self.action_type is not action_type:
            return False
        if not route_matches(self.route_pattern, route):
            return False
        return self.target_name is None or self.target_name == target_name


def classify_risk(
    rules: tuple[RiskRule, ...] | list[RiskRule],
    action_type: ActionType,
    route: str,
    target_name: str | None = None,
) -> RiskTier:
    """The most severe risk among all matching rules; the action-type default if none match.

    Order-independent by design: reordering rules can never lower the result, so a broad
    SAFE_READ rule cannot shadow a narrower IRREVERSIBLE one. A rule can only ever raise.
    """
    matched = [rule.risk for rule in rules if rule.matches(action_type, route, target_name)]
    if matched:
        return max(matched, key=lambda tier: tier.severity)
    try:
        return DEFAULT_RISK[action_type]
    except KeyError:
        raise ValueError(f"{action_type.value} is not a dispatchable action") from None


def effective_risk(policy_risk: RiskTier, declared_risk: RiskTier | None) -> RiskTier:
    """The more severe of policy's classification and the artifact's declaration."""
    if declared_risk is None or declared_risk.severity <= policy_risk.severity:
        return policy_risk
    return declared_risk
