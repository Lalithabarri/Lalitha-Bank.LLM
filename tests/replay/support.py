"""Shared fixtures for the replay tests: the checked-in artifact, policies built in code, and
the evidence composition (one recorder wired to the surface, the gate and the engine)."""

from datetime import UTC, datetime
from pathlib import Path

from cua.artifact import ArtifactStore, CapabilityArtifact
from cua.domain import ActionType
from cua.evidence import EvidenceRecorder, Redactor, SinkOpener
from cua.hitl import ControlOwner
from cua.policy import ActionGate, PolicyConfig, RiskRule, RiskTier
from cua.replay import ReplayConfig, ReplayDeps, ReplayEngine
from tests.evidence.memory_sink import MemoryEvidence

ROOT = Path(__file__).resolve().parents[2]
FLAGSHIP_ID = "read_savings_balance@1.0.0"
FIXED_TS = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)

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


def recorder_for(
    open_sink: SinkOpener | None = None, *, redactor: Redactor | None = None
) -> EvidenceRecorder:
    """A recorder over an in-memory sink with a fixed wall clock (deterministic events)."""
    return EvidenceRecorder(
        open_sink or MemoryEvidence(), redactor=redactor, wall_clock=lambda: FIXED_TS
    )


def engine_for(
    surface,
    clock,
    *,
    base_url: str,
    policy: PolicyConfig | None = None,
    owner: ControlOwner | None = None,
    recorder: EvidenceRecorder | None = None,
    intervention=None,
    **config_overrides,
) -> ReplayEngine:
    """The production composition: the gate observes into the same recorder the engine holds,
    and the gate and the engine share ONE ``ControlOwner`` (Milestone 8, A1).

    The *surface* must be wired to that recorder too (``listener=recorder``); the engine's
    dispatch-count invariant fails loudly otherwise.
    """
    recorder = recorder or recorder_for()
    owner = owner or ControlOwner()
    gate = ActionGate(policy or policy_for(base_url), owner, observer=recorder)
    config = ReplayConfig(base_url=base_url, **config_overrides)
    return ReplayEngine(
        ReplayDeps(
            surface=surface,
            action_gate=gate,
            clock=clock,
            evidence=recorder,
            control_owner=owner,
            intervention=intervention,
        ),
        config,
    )
