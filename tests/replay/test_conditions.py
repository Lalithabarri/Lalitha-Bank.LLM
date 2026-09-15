"""The four conditions, ALL-checkpoint short-circuit, and known-outcome detection."""

import pytest
from pydantic import ValidationError

from cua.replay import (
    BoundDescriptor,
    BoundKnownOutcome,
    BoundStrategy,
    detect_known_outcome,
    evaluate,
    evaluate_all,
    observed_path,
)
from cua.replay.binding import (
    BoundElementPresent,
    BoundRouteMatches,
    BoundTextPresent,
    BoundValueEquals,
)
from tests.surface.test_query import snapshot as fixture_snapshot

SAVINGS = "table: Accounts > row: Savings"


def at(name: str, url: str):
    return fixture_snapshot(name).model_copy(update={"url": url})


def target(**fields) -> BoundDescriptor:
    return BoundDescriptor(strategies=[BoundStrategy(**fields)])


# --- route_matches ---------------------------------------------------------------------------


def test_route_matches_uses_the_observed_url_path():
    snap = at("B_detail_M1001", "http://127.0.0.1:8000/members/M1001")
    assert evaluate(BoundRouteMatches(route="/members/M1001"), snap).passed
    assert not evaluate(BoundRouteMatches(route="/members/M1002"), snap).passed
    assert not evaluate(BoundRouteMatches(route="/members"), snap).passed
    assert not evaluate(BoundRouteMatches(route="/members/M1001/transfer"), snap).passed


def test_route_matches_ignores_query_fragment_and_trailing_slash_and_decodes():
    for url in (
        "http://h:1/members/M1001?x=1",
        "http://h:1/members/M1001#top",
        "http://h:1/members/M1001/",
        "http://h:1/members/M%31001",
    ):
        snap = at("B_detail_M1001", url)
        assert evaluate(BoundRouteMatches(route="/members/M1001"), snap).passed, url
    assert observed_path(at("A_search", "http://h:1")) == "/"


def test_route_matches_is_literal_no_wildcard_can_even_be_expressed():
    snap = at("B_detail_M1001", "http://h:1/members/M1001")
    with pytest.raises(ValidationError):
        BoundRouteMatches(route="/members/{id}")  # a bound route never carries a placeholder
    result = evaluate(BoundRouteMatches(route="/members/*"), snap)
    assert not result.passed and result.observed == "route /members/M1001"


# --- element_present -------------------------------------------------------------------------


def test_element_present_via_a_later_strategy():
    snap = at("A_search", "http://h:1/members/search")
    descriptor = BoundDescriptor(
        strategies=[
            BoundStrategy(role="textbox", name="Nope"),
            BoundStrategy(role="textbox", scope="group: Look up member"),
        ]
    )
    result = evaluate(BoundElementPresent(target=descriptor), snap)
    assert result.passed and result.observed == "1 present"
    assert not evaluate(BoundElementPresent(target=target(role="alert")), snap).passed


def test_element_present_is_presence_two_matches_still_present():
    snap = at("G_detail_M1001_ambiguous", "http://h:1/members/M1001")
    result = evaluate(BoundElementPresent(target=target(role="cell", scope=SAVINGS)), snap)
    assert result.passed and result.observed == "2 present"


# --- text_present ----------------------------------------------------------------------------


def test_text_present_via_outline_and_via_element_value():
    snap = at("F_not_found", "http://h:1/members/search")
    assert evaluate(BoundTextPresent(text="No member found for M404."), snap).passed
    assert evaluate(BoundTextPresent(text="Member Search"), snap).passed  # heading name
    assert evaluate(BoundTextPresent(text="M404"), snap).passed  # textbox value
    assert not evaluate(BoundTextPresent(text="Alice"), snap).passed


# --- value_equals ----------------------------------------------------------------------------


def test_value_equals_requires_exactly_one_target():
    filled = at("F_not_found", "http://h:1/members/search")
    box = target(role="textbox", name="Member ID")
    assert evaluate(BoundValueEquals(target=box, value="M404"), filled).passed
    unequal = evaluate(BoundValueEquals(target=box, value="M1001"), filled)
    assert not unequal.passed and unequal.observed == "value 'M404'"

    missing = evaluate(BoundValueEquals(target=target(role="dialog"), value="x"), filled)
    assert not missing.passed and missing.observed == "no match"

    ambiguous = at("G_detail_M1001_ambiguous", "http://h:1/members/M1001")
    two = evaluate(
        BoundValueEquals(target=target(role="cell", scope=SAVINGS), value="$15,275.00"), ambiguous
    )
    assert not two.passed and two.observed == "ambiguous: 2 candidates"


# --- ALL semantics and detection --------------------------------------------------------------


def test_evaluate_all_is_ordered_and_short_circuits():
    snap = at("B_detail_M1001", "http://h:1/members/M1001")
    conditions = [
        BoundRouteMatches(route="/members/M1001"),
        BoundElementPresent(target=target(role="heading", text_contains="Member M1001")),
    ]
    passed, results, failing = evaluate_all(conditions, snap)
    assert passed and failing is None and len(results) == 2

    conditions = [
        BoundRouteMatches(route="/members/M1002"),  # false
        BoundElementPresent(target=target(role="heading", text_contains="Member M1001")),  # unseen
    ]
    passed, results, failing = evaluate_all(conditions, snap)
    assert not passed and failing == 0 and len(results) == 1
    assert results[0].expected == "route_matches /members/M1002"


def test_detect_known_outcome_first_declared_match_or_none():
    detector = BoundElementPresent(
        target=target(role="alert", text_contains="No member found for M404.")
    )
    outcomes = [
        BoundKnownOutcome(code="OTHER", description="", detector=BoundTextPresent(text="zzz")),
        BoundKnownOutcome(code="MEMBER_NOT_FOUND", description="d", detector=detector),
    ]
    hit = detect_known_outcome(outcomes, at("F_not_found", "http://h:1/members/search"))
    assert hit is not None and hit.code == "MEMBER_NOT_FOUND"
    assert detect_known_outcome(outcomes, at("B_detail_M1001", "http://h:1/members/M1001")) is None
