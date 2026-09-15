import pytest

from cua.domain import ActionType
from cua.policy import (
    DEFAULT_RISK,
    RiskRule,
    RiskTier,
    classify_risk,
    effective_risk,
    route_matches,
    split_origin_and_path,
)


def test_default_risk_is_conservative_for_every_dispatchable_action():
    assert DEFAULT_RISK == {
        ActionType.NAVIGATE: RiskTier.SAFE_READ,
        ActionType.READ: RiskTier.SAFE_READ,
        ActionType.FILL: RiskTier.REVERSIBLE_WRITE,
        ActionType.SELECT: RiskTier.REVERSIBLE_WRITE,
        ActionType.CLICK: RiskTier.REVERSIBLE_WRITE,
    }
    for action_type, tier in DEFAULT_RISK.items():
        assert classify_risk((), action_type, "/anything") is tier


def test_non_dispatchable_actions_have_no_risk():
    for action_type in (ActionType.WAIT, ActionType.FINISH, ActionType.REPORT_BLOCKED):
        with pytest.raises(ValueError):
            classify_risk((), action_type, "/x")


def test_tiers_are_ordered():
    assert RiskTier.SAFE_READ.severity < RiskTier.REVERSIBLE_WRITE.severity
    assert RiskTier.REVERSIBLE_WRITE.severity < RiskTier.IRREVERSIBLE.severity


BROAD_TRANSFER_CLICK = RiskRule(
    action_type=ActionType.CLICK, route_pattern="/members/{id}/transfer", risk=RiskTier.SAFE_READ
)
CONFIRM_TRANSFER = RiskRule(
    action_type=ActionType.CLICK,
    route_pattern="/members/{id}/transfer",
    target_name="Confirm transfer",
    risk=RiskTier.IRREVERSIBLE,
)


@pytest.mark.parametrize(
    "rules",
    [(BROAD_TRANSFER_CLICK, CONFIRM_TRANSFER), (CONFIRM_TRANSFER, BROAD_TRANSFER_CLICK)],
    ids=["broad-first", "specific-first"],
)
def test_highest_matching_risk_wins_regardless_of_rule_order(rules):
    """Reordering rules can never downgrade a commit button to SAFE_READ."""
    assert (
        classify_risk(rules, ActionType.CLICK, "/members/M1001/transfer", "Confirm transfer")
        is RiskTier.IRREVERSIBLE
    )
    # The broad rule still applies to other buttons on that page.
    assert (
        classify_risk(rules, ActionType.CLICK, "/members/M1001/transfer", "Review transfer")
        is RiskTier.SAFE_READ
    )


def test_a_rule_can_only_raise_the_default_never_lower_another_match():
    rules = (
        RiskRule(
            action_type=ActionType.CLICK, route_pattern="/members/search", risk=RiskTier.SAFE_READ
        ),
        RiskRule(
            action_type=ActionType.CLICK,
            route_pattern="/members/search",
            risk=RiskTier.IRREVERSIBLE,
        ),
    )
    assert classify_risk(rules, ActionType.CLICK, "/members/search") is RiskTier.IRREVERSIBLE
    assert classify_risk(tuple(reversed(rules)), ActionType.CLICK, "/members/search") is (
        RiskTier.IRREVERSIBLE
    )


def test_rule_requires_action_type_and_route_to_match():
    rules = (
        RiskRule(
            action_type=ActionType.CLICK, route_pattern="/members/search", risk=RiskTier.SAFE_READ
        ),
    )
    assert classify_risk(rules, ActionType.FILL, "/members/search") is RiskTier.REVERSIBLE_WRITE
    assert classify_risk(rules, ActionType.CLICK, "/members/M1001") is RiskTier.REVERSIBLE_WRITE


def test_target_name_narrows_a_rule_to_one_control():
    rules = (
        RiskRule(
            action_type=ActionType.CLICK,
            route_pattern="/members/{id}/transfer",
            target_name="Confirm transfer",
            risk=RiskTier.IRREVERSIBLE,
        ),
    )
    assert (
        classify_risk(rules, ActionType.CLICK, "/members/M1001/transfer", "Confirm transfer")
        is RiskTier.IRREVERSIBLE
    )
    assert (
        classify_risk(rules, ActionType.CLICK, "/members/M1001/transfer", "Review transfer")
        is RiskTier.REVERSIBLE_WRITE
    )
    assert (
        classify_risk(rules, ActionType.CLICK, "/members/M1001/transfer", None)
        is RiskTier.REVERSIBLE_WRITE
    )


@pytest.mark.parametrize(
    "pattern, path, expected",
    [
        ("/members/search", "/members/search", True),
        ("/members/search", "/members/search/", True),
        ("/members/{id}", "/members/M1001", True),
        ("/members/{id}", "/members/M1001/transfer", False),
        ("/members/{id}", "/members/", False),
        ("/members/{id}/transfer", "/members/M1001/transfer", True),
        ("/members/search", "/members/M1001", False),
        ("/", "/", True),
        ("/", "/members", False),
    ],
)
def test_route_pattern_semantics(pattern, path, expected):
    assert route_matches(pattern, path) is expected


def test_split_origin_and_path():
    assert split_origin_and_path("http://127.0.0.1:8000/members/M1001?x=1#f") == (
        "http://127.0.0.1:8000",
        "/members/M1001",
    )
    assert split_origin_and_path("http://127.0.0.1:8000") == ("http://127.0.0.1:8000", "/")


@pytest.mark.parametrize(
    "policy, declared, expected",
    [
        (RiskTier.SAFE_READ, None, RiskTier.SAFE_READ),
        (RiskTier.SAFE_READ, RiskTier.IRREVERSIBLE, RiskTier.IRREVERSIBLE),
        (RiskTier.IRREVERSIBLE, RiskTier.SAFE_READ, RiskTier.IRREVERSIBLE),
        (RiskTier.REVERSIBLE_WRITE, RiskTier.REVERSIBLE_WRITE, RiskTier.REVERSIBLE_WRITE),
        (RiskTier.REVERSIBLE_WRITE, RiskTier.SAFE_READ, RiskTier.REVERSIBLE_WRITE),
    ],
)
def test_effective_risk_never_lowers_policy_risk(policy, declared, expected):
    assert effective_risk(policy, declared) is expected
