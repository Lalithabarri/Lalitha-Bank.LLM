"""Shared fixtures for the replay tests: the checked-in artifact and policies built in code."""

from pathlib import Path

from cua.artifact import ArtifactStore, CapabilityArtifact
from cua.domain import ActionType
from cua.hitl import ControlOwner
from cua.policy import ActionGate, PolicyConfig, RiskRule, RiskTier
from cua.replay import ReplayConfig, ReplayDeps, ReplayEngine

ROOT = Path(__file__).resolve().parents[2]
FLAGSHIP_ID = "read_savings_balance@1.0.0"

ALL_ACTIONS = frozenset(
    {ActionType.NAVIGATE, ActionType.FILL, ActionType.SELECT, ActionType.CLICK, ActionType.READ}
)
LEGACY_BANK_ROUTES = (
    "/members/search",
    "/members/{member_id}",
    "/members/{member_id}/transfer",
    "/members/{member_id}/transfer/complete",
)
LEGACY_BANK_RULES = (
    RiskRule(
        action_type=ActionType.CLICK, route_pattern="/members/search", risk=RiskTier.SAFE_READ
    ),
    RiskRule(
        action_type=ActionType.CLICK,
        route_pattern="/members/{member_id}/transfer",
        target_name="Confirm transfer",
        risk=RiskTier.IRREVERSIBLE,
    ),
)


def flagship() -> CapabilityArtifact:
    """The one checked-in artifact, loaded through the store (validated)."""
    return ArtifactStore(ROOT / "capabilities").load_id(FLAGSHIP_ID)


def policy_for(origin: str, **overrides) -> PolicyConfig:
    """The committed legacy_bank policy, re-homed on ``origin`` (tests use ephemeral ports)."""
    base = dict(
        allowed_origins=frozenset({origin}),
        allowed_routes=LEGACY_BANK_ROUTES,
        allowed_action_types=ALL_ACTIONS,
        risk_rules=LEGACY_BANK_RULES,
    )
    base.update(overrides)
    return PolicyConfig(**base)


def engine_for(
    surface,
    clock,
    *,
    base_url: str,
    policy: PolicyConfig | None = None,
    owner: ControlOwner | None = None,
    **config_overrides,
) -> ReplayEngine:
    gate = ActionGate(policy or policy_for(base_url), owner or ControlOwner())
    config = ReplayConfig(base_url=base_url, **config_overrides)
    return ReplayEngine(ReplayDeps(surface=surface, action_gate=gate, clock=clock), config)
