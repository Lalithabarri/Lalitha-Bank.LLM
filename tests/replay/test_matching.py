"""Strategy matching over real captures: exact scope, text_contains on name/value, all matches."""

from cua.replay import BoundDescriptor, BoundStrategy, match_strategy, resolve_on_snapshot
from tests.surface.test_query import snapshot

SAVINGS = "table: Accounts > row: Savings"


def test_role_and_name_exact():
    snap = snapshot("A_search")
    assert len(match_strategy(BoundStrategy(role="textbox", name="Member ID"), snap)) == 1
    assert match_strategy(BoundStrategy(role="textbox", name="Member"), snap) == []
    assert match_strategy(BoundStrategy(role="textbox", name="member id"), snap) == []


def test_scope_is_exact_context_hint_equality_not_a_prefix():
    snap = snapshot("B_detail_M1001")
    [cell] = match_strategy(BoundStrategy(role="cell", scope=SAVINGS), snap)
    assert cell.value == "$15,275.00"
    assert match_strategy(BoundStrategy(role="cell", scope="table: Accounts"), snap) == []
    assert match_strategy(BoundStrategy(role="cell", scope="row: Savings"), snap) == []
    assert match_strategy(BoundStrategy(role="heading", scope="anything"), snap) == []  # None


def test_text_contains_searches_value_for_the_alert_and_name_for_the_heading():
    not_found = snapshot("F_not_found")
    [alert] = match_strategy(
        BoundStrategy(role="alert", text_contains="No member found for M404."), not_found
    )
    assert alert.accessible_name == "" and alert.value == "No member found for M404."
    assert match_strategy(BoundStrategy(role="alert", text_contains="M1001"), not_found) == []
    detail = snapshot("B_detail_M1001")
    [heading] = match_strategy(BoundStrategy(role="heading", text_contains="Member M1001"), detail)
    assert heading.accessible_name == "Member M1001 — Alice Morgan"


def test_fields_are_anded():
    snap = snapshot("B_detail_M1001")
    assert len(match_strategy(BoundStrategy(role="cell"), snap)) > 2
    assert len(match_strategy(BoundStrategy(role="cell", scope=SAVINGS), snap)) == 1
    assert match_strategy(BoundStrategy(role="link", scope=SAVINGS), snap) == []


def test_every_match_is_returned_never_a_first():
    snap = snapshot("G_detail_M1001_ambiguous")
    matches = match_strategy(BoundStrategy(role="cell", scope=SAVINGS), snap)
    assert [m.value for m in matches] == ["$15,275.00", "$250.00"]


def test_resolve_on_snapshot_strategy_order_and_immediate_ambiguity():
    ambiguous_first = BoundDescriptor(
        strategies=[
            BoundStrategy(role="cell", scope=SAVINGS),  # 2 matches on the ambiguous page
            BoundStrategy(role="cell", name="$15,275.00"),  # would be unique — never consulted
        ]
    )
    on = resolve_on_snapshot(ambiguous_first, snapshot("G_detail_M1001_ambiguous"))
    assert on.ambiguous and not on.resolved
    assert on.strategy_index == 0 and len(on.candidates) == 2

    fallthrough = BoundDescriptor(
        strategies=[
            BoundStrategy(role="cell", name="nope"),  # 0 -> next strategy
            BoundStrategy(role="cell", scope=SAVINGS),
        ]
    )
    on = resolve_on_snapshot(fallthrough, snapshot("B_detail_M1001"))
    assert on.resolved and on.strategy_index == 1
    assert on.candidates[0].value == "$15,275.00"

    none = resolve_on_snapshot(
        BoundDescriptor(strategies=[BoundStrategy(role="dialog")]), snapshot("A_search")
    )
    assert not none.resolved and not none.ambiguous and none.strategy_index is None
