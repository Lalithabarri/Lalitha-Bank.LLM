"""Declarative condition evaluation over one observation (ARCHITECTURE §7, D14).

Four condition types, all pure functions of a ``SurfaceSnapshot``:

* ``route_matches``    — the *observed* URL path (percent-decoded, query/fragment ignored,
                         trailing slash ignored) equals the bound route, segment by segment.
                         Literal comparison: the policy's ``{param}`` wildcard matcher is not
                         used, and a bound route carries no placeholder.
* ``element_present``  — some strategy (declared order) matches at least one element.
* ``text_present``     — substring of the visible text outline or of any element's name/value.
* ``value_equals``     — exactly one element resolves and its ``value`` equals the bound text.

``success_checkpoint`` has ALL semantics: declared order, every condition must pass, stop at
the first that does not. Known outcomes are detected by the same evaluator, in declared order.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

from cua.domain import DomainModel, SurfaceSnapshot
from cua.replay.binding import (
    BoundElementPresent,
    BoundKnownOutcome,
    BoundRouteMatches,
    BoundTextPresent,
    BoundValueEquals,
)
from cua.replay.matching import match_strategy, resolve_on_snapshot

BoundConditionLike = BoundRouteMatches | BoundElementPresent | BoundTextPresent | BoundValueEquals


class ConditionResult(DomainModel):
    passed: bool
    expected: str
    observed: str


def _segments(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment != ""]


def observed_path(snapshot: SurfaceSnapshot) -> str:
    return unquote(urlsplit(snapshot.url).path or "/")


def describe(condition: BoundConditionLike) -> str:
    if isinstance(condition, BoundRouteMatches):
        return f"route_matches {condition.route}"
    if isinstance(condition, BoundElementPresent):
        return f"element_present {_describe_target(condition)}"
    if isinstance(condition, BoundTextPresent):
        return f"text_present {condition.text!r}"
    return f"value_equals {_describe_target(condition)} == {condition.value!r}"


def _describe_target(condition: BoundElementPresent | BoundValueEquals) -> str:
    parts = []
    for strategy in condition.target.strategies:
        fields = {k: v for k, v in strategy.model_dump().items() if v is not None}
        parts.append(" ".join(f"{k}={v!r}" for k, v in fields.items()))
    return " | ".join(parts)


def evaluate(condition: BoundConditionLike, snapshot: SurfaceSnapshot) -> ConditionResult:
    expected = describe(condition)
    if isinstance(condition, BoundRouteMatches):
        path = observed_path(snapshot)
        return ConditionResult(
            passed=_segments(path) == _segments(condition.route),
            expected=expected,
            observed=f"route {path}",
        )
    if isinstance(condition, BoundElementPresent):
        for strategy in condition.target.strategies:
            count = len(match_strategy(strategy, snapshot))
            if count:
                return ConditionResult(passed=True, expected=expected, observed=f"{count} present")
        return ConditionResult(passed=False, expected=expected, observed="absent")
    if isinstance(condition, BoundTextPresent):
        text = condition.text
        present = text in snapshot.visible_text_outline or any(
            text in e.accessible_name or text in (e.value or "") for e in snapshot.elements
        )
        return ConditionResult(
            passed=present, expected=expected, observed="present" if present else "absent"
        )
    if isinstance(condition, BoundValueEquals):
        on = resolve_on_snapshot(condition.target, snapshot)
        if on.ambiguous:
            return ConditionResult(
                passed=False,
                expected=expected,
                observed=f"ambiguous: {len(on.candidates)} candidates",
            )
        if not on.resolved:
            return ConditionResult(passed=False, expected=expected, observed="no match")
        value = on.candidates[0].value
        return ConditionResult(
            passed=value == condition.value, expected=expected, observed=f"value {value!r}"
        )
    raise TypeError(f"not a bound condition: {type(condition).__name__}")  # closed vocabulary


def evaluate_all(
    conditions: list[BoundConditionLike], snapshot: SurfaceSnapshot
) -> tuple[bool, list[ConditionResult], int | None]:
    """ALL semantics: declared order, short-circuit on the first false.

    Returns (passed, results so far, index of the failing condition or None).
    """
    results: list[ConditionResult] = []
    for index, condition in enumerate(conditions):
        result = evaluate(condition, snapshot)
        results.append(result)
        if not result.passed:
            return False, results, index
    return True, results, None


def detect_known_outcome(
    outcomes: list[BoundKnownOutcome], snapshot: SurfaceSnapshot
) -> BoundKnownOutcome | None:
    """The first declared outcome whose detector holds on this observation."""
    for outcome in outcomes:
        if evaluate(outcome.detector, snapshot).passed:
            return outcome
    return None
