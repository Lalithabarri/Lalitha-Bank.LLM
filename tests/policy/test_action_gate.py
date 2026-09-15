"""ActionGate: deny-by-default, hard DENY, IRREVERSIBLE never dispatched, sole caller of act()."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cua.domain import ActionType, SurfaceSnapshot
from cua.hitl import ControlOwner, ControlOwnerState
from cua.policy import (
    ActionGate,
    DenyReason,
    GateDecision,
    GateRequest,
    GateResult,
    PolicyConfig,
    ResolvedTarget,
    RiskRule,
    RiskTier,
)
from cua.surface import SurfaceAction
from tests.policy.fake_surface import FakeSurface

ORIGIN = "http://127.0.0.1:8000"
POLICY_FILE = Path(__file__).resolve().parents[2] / "policy" / "legacy_bank.json"


def full_config(**overrides) -> PolicyConfig:
    base = dict(
        allowed_origins=frozenset({ORIGIN}),
        allowed_routes=("/members/search", "/members/{id}", "/members/{id}/transfer"),
        allowed_action_types=frozenset(
            {
                ActionType.NAVIGATE,
                ActionType.FILL,
                ActionType.SELECT,
                ActionType.CLICK,
                ActionType.READ,
            }
        ),
        risk_rules=(
            RiskRule(
                action_type=ActionType.CLICK,
                route_pattern="/members/search",
                risk=RiskTier.SAFE_READ,
            ),
            RiskRule(
                action_type=ActionType.CLICK,
                route_pattern="/members/{id}/transfer",
                target_name="Confirm transfer",
                risk=RiskTier.IRREVERSIBLE,
            ),
        ),
    )
    base.update(overrides)
    return PolicyConfig(**base)


def request(
    action_type=ActionType.READ, origin=ORIGIN, route="/members/M1001", target=None, declared=None
):
    return GateRequest(
        action_type=action_type, origin=origin, route=route, target=target, declared_risk=declared
    )


SEARCH_BUTTON = ResolvedTarget(
    role="button", accessible_name="Search", context_hint="group: Look up member"
)
CONFIRM_BUTTON = ResolvedTarget(role="button", accessible_name="Confirm transfer")
SAVINGS_CELL = ResolvedTarget(
    role="cell",
    accessible_name="$15,275.00",
    context_hint="table: Accounts > row: Savings",
    value="$15,275.00",
)


# --- deny by default ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action_type", [ActionType.NAVIGATE, ActionType.FILL, ActionType.CLICK, ActionType.READ]
)
def test_empty_config_denies_everything(action_type):
    gate = ActionGate(PolicyConfig(), ControlOwner())
    result = gate.authorize(request(action_type=action_type, route="/members/search"))
    assert result.decision is GateDecision.DENY
    assert result.deny_reason is DenyReason.ACTION_TYPE_NOT_ALLOWED


# --- each deny reason from exactly its condition ------------------------------------------------


def test_allowed_request_is_allowed_with_effective_risk():
    gate = ActionGate(full_config(), ControlOwner())
    result = gate.authorize(request(action_type=ActionType.READ, target=SAVINGS_CELL))
    assert result == GateResult(
        decision=GateDecision.ALLOW,
        risk=RiskTier.SAFE_READ,
        deny_reason=None,
        explanation="READ on /members/M1001 allowed as SAFE_READ",
    )


def test_action_type_not_allowed():
    gate = ActionGate(
        full_config(allowed_action_types=frozenset({ActionType.READ})), ControlOwner()
    )
    result = gate.authorize(request(action_type=ActionType.FILL, route="/members/search"))
    assert (result.decision, result.deny_reason) == (
        GateDecision.DENY,
        DenyReason.ACTION_TYPE_NOT_ALLOWED,
    )


def test_origin_not_allowed():
    gate = ActionGate(full_config(), ControlOwner())
    result = gate.authorize(request(origin="http://127.0.0.1:9999"))
    assert (result.decision, result.deny_reason) == (
        GateDecision.DENY,
        DenyReason.ORIGIN_NOT_ALLOWED,
    )


def test_route_not_allowed():
    gate = ActionGate(full_config(), ControlOwner())
    for route in ("/admin", "/members/M1001/transfer/complete", "/"):
        result = gate.authorize(request(route=route))
        assert (result.decision, result.deny_reason) == (
            GateDecision.DENY,
            DenyReason.ROUTE_NOT_ALLOWED,
        ), route


@pytest.mark.parametrize(
    "state", [ControlOwnerState.PENDING_HUMAN, ControlOwnerState.HUMAN, ControlOwnerState.RETURNING]
)
def test_control_not_owned_denies_even_when_everything_else_is_allowed(state):
    gate = ActionGate(full_config(), ControlOwner(state))
    result = gate.authorize(request(action_type=ActionType.READ, target=SAVINGS_CELL))
    assert (result.decision, result.deny_reason) == (
        GateDecision.DENY,
        DenyReason.CONTROL_NOT_OWNED,
    )


def test_control_owner_is_checked_before_everything_else():
    gate = ActionGate(PolicyConfig(), ControlOwner(ControlOwnerState.HUMAN))
    result = gate.authorize(request(origin="http://nope", route="/nope"))
    assert result.deny_reason is DenyReason.CONTROL_NOT_OWNED


# --- risk --------------------------------------------------------------------------------------


def test_irreversible_requires_intervention_never_allow():
    gate = ActionGate(full_config(), ControlOwner())
    result = gate.authorize(
        request(
            action_type=ActionType.CLICK, route="/members/M1001/transfer", target=CONFIRM_BUTTON
        )
    )
    assert result.decision is GateDecision.REQUIRE_INTERVENTION
    assert result.risk is RiskTier.IRREVERSIBLE
    assert result.deny_reason is None


def test_search_click_is_safe_read_by_rule():
    gate = ActionGate(full_config(), ControlOwner())
    result = gate.authorize(
        request(action_type=ActionType.CLICK, route="/members/search", target=SEARCH_BUTTON)
    )
    assert (result.decision, result.risk) == (GateDecision.ALLOW, RiskTier.SAFE_READ)


def test_declared_risk_can_raise_but_never_lower():
    gate = ActionGate(full_config(), ControlOwner())
    raised = gate.authorize(
        request(action_type=ActionType.READ, target=SAVINGS_CELL, declared=RiskTier.IRREVERSIBLE)
    )
    assert raised.decision is GateDecision.REQUIRE_INTERVENTION
    lowered = gate.authorize(
        request(
            action_type=ActionType.CLICK,
            route="/members/M1001/transfer",
            target=CONFIRM_BUTTON,
            declared=RiskTier.SAFE_READ,
        )
    )
    assert lowered.decision is GateDecision.REQUIRE_INTERVENTION
    assert lowered.risk is RiskTier.IRREVERSIBLE


def test_denied_result_carries_no_approval_path():
    with pytest.raises(ValidationError):
        GateResult(
            decision=GateDecision.DENY,
            risk=RiskTier.SAFE_READ,
            explanation="x",
            approved_by="human",
        )
    assert "approve" not in " ".join(GateResult.model_fields).lower()


# --- dispatch: the only place act() is called ----------------------------------------------


def snapshot_at(url: str) -> SurfaceSnapshot:
    return SurfaceSnapshot(
        url=url, page_title="t", step_index=1, elements=[], visible_text_outline=""
    )


DETAIL = snapshot_at(f"{ORIGIN}/members/M1001")
SEARCH = snapshot_at(f"{ORIGIN}/members/search")
TRANSFER = snapshot_at(f"{ORIGIN}/members/M1001/transfer")


def test_dispatch_on_deny_never_touches_the_surface():
    surface = FakeSurface()
    gate = ActionGate(
        full_config(allowed_action_types=frozenset({ActionType.READ})), ControlOwner()
    )
    action = SurfaceAction(action_type=ActionType.FILL, ref="e10", value="M1001")
    result, act_result = gate.dispatch(surface, action, snapshot=SEARCH)
    assert result.decision is GateDecision.DENY
    assert act_result is None
    assert surface.act_calls == []


def test_dispatch_on_require_intervention_never_touches_the_surface():
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner())
    action = SurfaceAction(action_type=ActionType.CLICK, ref="f4e24")
    result, act_result = gate.dispatch(surface, action, snapshot=TRANSFER, target=CONFIRM_BUTTON)
    assert result.decision is GateDecision.REQUIRE_INTERVENTION
    assert act_result is None
    assert surface.act_calls == []


@pytest.mark.parametrize(
    "state", [ControlOwnerState.PENDING_HUMAN, ControlOwnerState.HUMAN, ControlOwnerState.RETURNING]
)
def test_dispatch_on_control_not_owned_never_touches_the_surface(state):
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner(state))
    action = SurfaceAction(action_type=ActionType.READ, ref="e30")
    result, act_result = gate.dispatch(surface, action, snapshot=DETAIL, target=SAVINGS_CELL)
    assert result.deny_reason is DenyReason.CONTROL_NOT_OWNED
    assert act_result is None
    assert surface.act_calls == []


def test_dispatch_on_allow_calls_act_exactly_once():
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner())
    action = SurfaceAction(action_type=ActionType.READ, ref="e30")
    result, act_result = gate.dispatch(surface, action, snapshot=DETAIL, target=SAVINGS_CELL)
    assert result.decision is GateDecision.ALLOW
    assert act_result is not None and act_result.action_type is ActionType.READ
    assert surface.act_calls == [action]


# --- origin/route come from reality, not from a claim ------------------------------------------


def test_non_navigate_actions_require_the_observed_snapshot():
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner())
    with pytest.raises(ValueError, match="observed snapshot"):
        gate.dispatch(surface, SurfaceAction(action_type=ActionType.CLICK, ref="e1"))
    assert surface.act_calls == []


def test_route_is_taken_from_the_snapshot_url_not_a_caller_claim():
    """Observed page is the transfer screen; the search rule must not apply."""
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner())
    request = ActionGate.request_for(
        SurfaceAction(action_type=ActionType.CLICK, ref="e1"),
        snapshot=TRANSFER,
        target=CONFIRM_BUTTON,
    )
    assert (request.origin, request.route) == (ORIGIN, "/members/M1001/transfer")
    result, _ = gate.dispatch(
        surface,
        SurfaceAction(action_type=ActionType.CLICK, ref="e1"),
        snapshot=TRANSFER,
        target=CONFIRM_BUTTON,
    )
    assert result.decision is GateDecision.REQUIRE_INTERVENTION
    assert surface.act_calls == []


def test_dispatch_has_no_parameter_for_a_precomputed_decision_or_route():
    import inspect

    params = set(inspect.signature(ActionGate.dispatch).parameters)
    assert params == {"self", "surface", "action", "snapshot", "target", "declared_risk"}


@pytest.mark.parametrize(
    "destination, reason",
    [
        ("https://evil.example/", DenyReason.ORIGIN_NOT_ALLOWED),
        ("http://127.0.0.1:8000.evil.example/members/search", DenyReason.ORIGIN_NOT_ALLOWED),
        ("http://127.0.0.1:9999/members/search", DenyReason.ORIGIN_NOT_ALLOWED),  # port
        ("https://127.0.0.1:8000/members/search", DenyReason.ORIGIN_NOT_ALLOWED),  # scheme
        ("http://localhost:8000/members/search", DenyReason.ORIGIN_NOT_ALLOWED),  # host
        (f"{ORIGIN}/admin", DenyReason.ROUTE_NOT_ALLOWED),
        (f"{ORIGIN}/members/M1001/transfer/complete", DenyReason.ROUTE_NOT_ALLOWED),
    ],
)
def test_navigate_is_authorized_on_its_destination_and_goto_is_never_called(destination, reason):
    surface = FakeSurface(snapshot_at(f"{ORIGIN}/members/search"))  # currently on an allowed page
    gate = ActionGate(full_config(), ControlOwner())
    action = SurfaceAction(action_type=ActionType.NAVIGATE, url=destination)
    result, act_result = gate.dispatch(surface, action, snapshot=SEARCH)
    assert (result.decision, result.deny_reason) == (GateDecision.DENY, reason), destination
    assert act_result is None
    assert surface.act_calls == []


def test_navigate_to_allowlisted_destination_is_dispatched_once():
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner())
    action = SurfaceAction(action_type=ActionType.NAVIGATE, url=f"{ORIGIN}/members/M1002")
    result, act_result = gate.dispatch(surface, action)
    assert (result.decision, result.risk) == (GateDecision.ALLOW, RiskTier.SAFE_READ)
    assert act_result is not None
    assert surface.act_calls == [action]


def test_navigate_without_url_is_rejected_before_any_dispatch():
    surface = FakeSurface()
    gate = ActionGate(full_config(), ControlOwner())
    with pytest.raises(ValueError, match="destination url"):
        gate.dispatch(surface, SurfaceAction(action_type=ActionType.NAVIGATE))
    assert surface.act_calls == []


# --- configuration -----------------------------------------------------------------------------


def test_config_round_trips_through_json():
    config = full_config()
    assert PolicyConfig.model_validate_json(config.model_dump_json()) == config


def test_committed_legacy_bank_policy_loads_and_classifies_commit_as_irreversible():
    config = PolicyConfig.from_json_file(POLICY_FILE)
    gate = ActionGate(config, ControlOwner())
    assert config.origin_allowed("http://127.0.0.1:8000")
    assert config.route_allowed("/members/M1001") and not config.route_allowed("/admin")
    confirm = gate.authorize(
        request(
            action_type=ActionType.CLICK, route="/members/M1001/transfer", target=CONFIRM_BUTTON
        )
    )
    assert confirm.decision is GateDecision.REQUIRE_INTERVENTION
    search = gate.authorize(
        request(action_type=ActionType.CLICK, route="/members/search", target=SEARCH_BUTTON)
    )
    assert (search.decision, search.risk) == (GateDecision.ALLOW, RiskTier.SAFE_READ)


def test_config_rejects_unknown_fields_and_action_types():
    with pytest.raises(ValidationError):
        PolicyConfig(allowed_action_types=frozenset({"EVAL"}))
    with pytest.raises(ValidationError):
        PolicyConfig(approvals=["anything"])
