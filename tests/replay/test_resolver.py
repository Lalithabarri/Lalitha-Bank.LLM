"""TargetResolver: one deadline, fresh observations, detectors first, ambiguity immediate."""

import pytest

from cua.replay import (
    Ambiguous,
    BoundDescriptor,
    BoundKnownOutcome,
    BoundStrategy,
    NotFound,
    OutcomeDetected,
    Resolved,
    TargetResolver,
)
from cua.replay.binding import BoundElementPresent
from tests.replay.fake_clock import FakeClock
from tests.surface.test_query import snapshot

SAVINGS = "table: Accounts > row: Savings"
NOT_FOUND = BoundKnownOutcome(
    code="MEMBER_NOT_FOUND",
    description="",
    detector=BoundElementPresent(
        target=BoundDescriptor(
            strategies=[BoundStrategy(role="alert", text_contains="No member found for M404.")]
        )
    ),
)


class Pages:
    """A sequence of observations; the last one repeats forever."""

    def __init__(self, *names: str) -> None:
        self.names = list(names)
        self.calls = 0

    def observe(self):
        name = self.names[min(self.calls, len(self.names) - 1)]
        self.calls += 1
        return snapshot(name)


def resolver(clock, timeout=2.0, poll=0.5):
    return TargetResolver(clock, timeout_s=timeout, poll_interval_s=poll)


def descriptor(*strategies: BoundStrategy) -> BoundDescriptor:
    return BoundDescriptor(strategies=list(strategies))


def test_resolves_on_first_observation_with_strategy_index():
    clock = FakeClock()
    pages = Pages("B_detail_M1001")
    r = resolver(clock).resolve(
        descriptor(
            BoundStrategy(role="cell", name="nope"), BoundStrategy(role="cell", scope=SAVINGS)
        ),
        pages.observe,
        [NOT_FOUND],
    )
    assert isinstance(r, Resolved)
    assert r.element.value == "$15,275.00" and r.strategy_index == 1
    assert r.observations == 1 and pages.calls == 1 and clock.sleeps == []
    assert r.snapshot.elements  # the exact snapshot the ref belongs to is returned with it
    assert r.element in r.snapshot.elements


def test_ambiguity_is_immediate_and_never_falls_through_to_a_weaker_strategy():
    clock = FakeClock()
    pages = Pages("G_detail_M1001_ambiguous")
    r = resolver(clock).resolve(
        descriptor(
            BoundStrategy(role="cell", scope=SAVINGS),  # 2 matches
            BoundStrategy(role="cell", name="$15,275.00"),  # unique, must never be consulted
        ),
        pages.observe,
        [NOT_FOUND],
    )
    assert isinstance(r, Ambiguous)
    assert r.strategy_index == 0 and [c.value for c in r.candidates] == ["$15,275.00", "$250.00"]
    assert pages.calls == 1 and clock.sleeps == []  # no re-observation, no retry


def test_zero_matches_polls_until_the_element_appears():
    clock = FakeClock()
    pages = Pages("A_search", "A_search", "B_detail_M1001")
    r = resolver(clock, timeout=5.0, poll=0.5).resolve(
        descriptor(BoundStrategy(role="cell", scope=SAVINGS)), pages.observe, []
    )
    assert isinstance(r, Resolved) and r.observations == 3 and pages.calls == 3
    assert clock.sleeps == [0.5, 0.5]


def test_not_found_after_the_deadline_with_the_last_snapshot():
    clock = FakeClock(start=100.0)
    pages = Pages("A_search")
    r = resolver(clock, timeout=1.0, poll=0.4).resolve(
        descriptor(BoundStrategy(role="cell", scope=SAVINGS)), pages.observe, []
    )
    assert isinstance(r, NotFound)
    assert r.observations == pages.calls == 4  # t=0, .4, .8, 1.2 (>= deadline after the 4th)
    assert clock.now() - 100.0 == pytest.approx(1.2)  # bounded: deadline + at most one poll
    assert r.snapshot.page_title == "A_search"


def test_zero_timeout_still_takes_exactly_one_observation():
    clock = FakeClock()
    pages = Pages("A_search")
    r = resolver(clock, timeout=0.0).resolve(
        descriptor(BoundStrategy(role="cell", scope=SAVINGS)), pages.observe, []
    )
    assert isinstance(r, NotFound) and pages.calls == 1 and clock.sleeps == []


def test_known_outcome_detected_while_polling_beats_not_found():
    clock = FakeClock()
    pages = Pages("A_search", "F_not_found")
    r = resolver(clock, timeout=10.0).resolve(
        descriptor(BoundStrategy(role="cell", scope=SAVINGS)), pages.observe, [NOT_FOUND]
    )
    assert isinstance(r, OutcomeDetected)
    assert r.outcome.code == "MEMBER_NOT_FOUND" and r.observations == 2


def test_detectors_run_before_strategies_on_the_same_observation():
    """A page that is both 'not found' and would resolve/ambiguate reports the outcome."""
    clock = FakeClock()
    pages = Pages("F_not_found")
    r = resolver(clock).resolve(
        descriptor(BoundStrategy(role="textbox", name="Member ID")),  # resolves on this page
        pages.observe,
        [NOT_FOUND],
    )
    assert isinstance(r, OutcomeDetected)
    r = resolver(clock).resolve(
        descriptor(BoundStrategy(role="link")),  # 3 links on this page: would be ambiguous
        pages.observe,
        [NOT_FOUND],
    )
    assert isinstance(r, OutcomeDetected)


def test_resolver_rejects_nonsense_bounds():
    with pytest.raises(ValueError):
        TargetResolver(FakeClock(), timeout_s=-1, poll_interval_s=0.1)
    with pytest.raises(ValueError):
        TargetResolver(FakeClock(), timeout_s=1, poll_interval_s=0)
