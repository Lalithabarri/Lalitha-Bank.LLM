"""The flagship's declared contract: the goal's projection plus the declared MEMBER_NOT_FOUND
outcome, whose text is traceable to a Milestone 2 capture (not to the handwritten artifact)."""

from pathlib import Path

from cua.artifact import ElementPresent
from cua.discovery.capabilities import (
    MEMBER_NOT_FOUND,
    READ_SAVINGS_BALANCE_DECLARATION,
    capability_declaration,
)
from cua.discovery.goal import READ_SAVINGS_BALANCE_GOAL

ARIA_FIXTURES = Path(__file__).resolve().parents[1] / "surface" / "fixtures"


def test_declaration_is_the_goal_without_its_verifier():
    goal = READ_SAVINGS_BALANCE_GOAL
    declaration = READ_SAVINGS_BALANCE_DECLARATION
    assert declaration.name == goal.name
    assert declaration.description == goal.description
    assert declaration.inputs == goal.inputs and declaration.outputs == goal.outputs
    assert not hasattr(declaration, "verifier")
    assert capability_declaration(goal).known_outcomes == []
    assert declaration.known_outcomes == [MEMBER_NOT_FOUND]


def test_declared_not_found_outcome_is_observed_on_the_not_found_capture():
    detector = MEMBER_NOT_FOUND.detector
    assert isinstance(detector, ElementPresent)
    (strategy,) = detector.target.strategies
    assert strategy.role == "alert" and "{member_id}" in strategy.text_contains
    capture = (ARIA_FIXTURES / "F_not_found.aria.txt").read_text()
    assert strategy.text_contains.replace("{member_id}", "M404") in capture
    assert MEMBER_NOT_FOUND.terminal_status == "BUSINESS_OUTCOME"
