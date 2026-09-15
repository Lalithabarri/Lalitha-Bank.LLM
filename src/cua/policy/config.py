"""Deny-by-default policy configuration (REQUIREMENTS §6, ARCHITECTURE §8, D09).

Everything not explicitly listed is denied: an empty ``PolicyConfig`` permits no origin, no
route, and no action type. The configuration is static data loaded once; nothing at runtime —
least of all a model — can modify it.
"""

from pathlib import Path

from cua.domain import ActionType, DomainModel
from cua.policy.risk import RiskRule
from cua.policy.routes import any_route_matches


class PolicyConfig(DomainModel):
    allowed_origins: frozenset[str] = frozenset()
    allowed_routes: tuple[str, ...] = ()
    allowed_action_types: frozenset[ActionType] = frozenset()
    risk_rules: tuple[RiskRule, ...] = ()

    def origin_allowed(self, origin: str) -> bool:
        return origin in self.allowed_origins

    def route_allowed(self, path: str) -> bool:
        return any_route_matches(self.allowed_routes, path)

    def action_type_allowed(self, action_type: ActionType) -> bool:
        return action_type in self.allowed_action_types

    @classmethod
    def from_json_file(cls, path: Path) -> "PolicyConfig":
        return cls.model_validate_json(path.read_text())
