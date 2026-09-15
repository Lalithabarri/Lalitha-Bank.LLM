"""Typed input binding: runtime inputs + artifact -> a fully bound, placeholder-free view.

Everything the engine will match, compare, type, or navigate to is bound here, once, before the
first observation. An ``INPUT_REF`` or ``{placeholder}`` that cannot be bound is an error —
never a wildcard, never a literal ``{name}`` left in a query. The ``CapabilityArtifact`` itself
is read, never modified or copied: the bound view is a separate set of models.

Routes get stricter treatment than free text: a placeholder inside a route must bind to exactly
one non-empty path segment (the same rule the policy's ``{param}`` patterns follow), and a
NAVIGATE destination is built from an origin-only base URL plus a validated absolute path —
never by permissive URL joining that would let a route replace the origin.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit

from pydantic import Field, field_validator

from cua.artifact import (
    PLACEHOLDER,
    CapabilityArtifact,
    Condition,
    ConditionType,
    ElementPresent,
    InputRef,
    KnownOutcome,
    LiteralValue,
    RouteMatches,
    Step,
    TargetDescriptor,
    TargetStrategy,
    TextPresent,
    ValueEquals,
)
from cua.domain import ActionType, DomainModel
from cua.policy import RiskTier, split_origin_and_path
from cua.replay.transforms import TransformError, apply_transform


class BindingError(ValueError):
    """An input is missing, unknown, mistyped, or a placeholder cannot be bound."""


# --- bound view types ----------------------------------------------------------------------------


def _no_placeholder(value: str | None) -> str | None:
    """Belt and braces: a bound string never carries ``{name}`` (fails closed on odd inputs)."""
    if value is not None and PLACEHOLDER.search(value):
        raise ValueError(f"unbound placeholder in {value!r}")
    return value


class BoundStrategy(DomainModel):
    role: str | None = None
    name: str | None = None
    scope: str | None = None
    text_contains: str | None = None

    @field_validator("role", "name", "scope", "text_contains")
    @classmethod
    def _bound(cls, value: str | None) -> str | None:
        return _no_placeholder(value)


class BoundDescriptor(DomainModel):
    strategies: list[BoundStrategy] = Field(min_length=1)


class BoundRouteMatches(DomainModel):
    type: Literal[ConditionType.ROUTE_MATCHES] = ConditionType.ROUTE_MATCHES
    route: str

    @field_validator("route")
    @classmethod
    def _bound(cls, value: str) -> str:
        return _no_placeholder(value) or value


class BoundElementPresent(DomainModel):
    type: Literal[ConditionType.ELEMENT_PRESENT] = ConditionType.ELEMENT_PRESENT
    target: BoundDescriptor


class BoundTextPresent(DomainModel):
    type: Literal[ConditionType.TEXT_PRESENT] = ConditionType.TEXT_PRESENT
    text: str

    @field_validator("text")
    @classmethod
    def _bound(cls, value: str) -> str:
        return _no_placeholder(value) or value


class BoundValueEquals(DomainModel):
    type: Literal[ConditionType.VALUE_EQUALS] = ConditionType.VALUE_EQUALS
    target: BoundDescriptor
    value: str

    @field_validator("value")
    @classmethod
    def _bound(cls, value: str) -> str:
        return _no_placeholder(value) or value


BoundCondition = Annotated[
    BoundRouteMatches | BoundElementPresent | BoundTextPresent | BoundValueEquals,
    Field(discriminator="type"),
]


class BoundKnownOutcome(DomainModel):
    code: str
    description: str
    detector: BoundCondition


class BoundStep(DomainModel):
    step_id: str
    action: ActionType
    declared_risk: RiskTier
    route: str | None = None  # NAVIGATE: bound path
    destination: str | None = None  # NAVIGATE: the one URL that will be authorized and visited
    target: BoundDescriptor | None = None
    value: str | None = None  # FILL / SELECT
    output: str | None = None  # READ
    postcondition: BoundCondition | None = None


class BoundArtifact(DomainModel):
    steps: list[BoundStep]
    success_checkpoint: list[BoundCondition]
    known_outcomes: list[BoundKnownOutcome]


# --- inputs ---------------------------------------------------------------------------------------


def bind_inputs(artifact: CapabilityArtifact, raw: Mapping[str, object]) -> dict[str, str]:
    """Validate runtime inputs against the artifact's declared ``inputs``.

    Unknown names are rejected (deny-by-default, catches typos); required names must be present;
    every value must be text that parses under its declared type. The bound value is the
    original text — the UI receives text.
    """
    unknown = set(raw) - set(artifact.inputs)
    if unknown:
        raise BindingError(f"unknown inputs: {sorted(unknown)}")
    bound: dict[str, str] = {}
    for name, spec in artifact.inputs.items():
        if name not in raw:
            if spec.required:
                raise BindingError(f"missing required input {name!r}")
            continue
        value = raw[name]
        if not isinstance(value, str):
            raise BindingError(f"input {name!r} must be text, got {type(value).__name__}")
        try:
            apply_transform(spec.type, value)
        except TransformError as exc:
            raise BindingError(f"input {name!r} is not a valid {spec.type.value}: {exc}") from exc
        bound[name] = value
    return bound


# --- text and routes ------------------------------------------------------------------------------


def bind_text(template: str, bound: Mapping[str, str]) -> str:
    """Substitute every ``{name}``; an unbound name is an error, never a wildcard."""

    def substitute(match) -> str:
        name = match.group(1)
        if name not in bound:
            raise BindingError(f"unbound placeholder {{{name}}} in {template!r}")
        return bound[name]

    return PLACEHOLDER.sub(substitute, template)


_FORBIDDEN_IN_SEGMENT = frozenset("/\\?#{}")


def _check_segment_value(name: str, value: str) -> None:
    if not value:
        raise BindingError(f"input {name!r} is empty; a route placeholder needs one segment")
    if any(c in _FORBIDDEN_IN_SEGMENT or c.isspace() or ord(c) < 32 for c in value):
        raise BindingError(
            f"input {name!r} is not a single path segment (contains a separator, "
            "whitespace, or a control character)"
        )


def bind_route(template: str, bound: Mapping[str, str]) -> str:
    """Bind a path template; each placeholder must become exactly one non-empty segment."""

    def substitute(match) -> str:
        name = match.group(1)
        if name not in bound:
            raise BindingError(f"unbound placeholder {{{name}}} in route {template!r}")
        _check_segment_value(name, bound[name])
        return bound[name]

    route = PLACEHOLDER.sub(substitute, template)
    _validate_path(route)
    return route


def _validate_path(route: str) -> None:
    if not route.startswith("/") or route.startswith("//"):
        raise BindingError(f"route {route!r} must be an absolute path (one leading '/')")
    if "://" in route or any(c in "\\?#{}" or c.isspace() or ord(c) < 32 for c in route):
        raise BindingError(f"route {route!r} may not carry a scheme, query, fragment, or spaces")
    parts = urlsplit(route)
    if parts.scheme or parts.netloc:
        raise BindingError(f"route {route!r} must not name an origin")


def normalize_base_url(base_url: str) -> str:
    """Origin only: ``scheme://host[:port]``. A single trailing slash is dropped; anything else
    (a path, query, fragment, credentials, a non-http scheme) is rejected."""
    if any(c.isspace() for c in base_url):
        raise ValueError(f"base_url {base_url!r} contains whitespace")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(f"base_url {base_url!r} must use http or https")
    if not parts.netloc or "@" in parts.netloc:
        raise ValueError(f"base_url {base_url!r} must be an origin without credentials")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ValueError(f"base_url {base_url!r} must be an origin only (no path/query/fragment)")
    return f"{parts.scheme}://{parts.netloc}"


def build_destination(base_url: str, bound_route: str) -> str:
    """The one URL a NAVIGATE step will be authorized for and sent to."""
    base = normalize_base_url(base_url)
    _validate_path(bound_route)
    segments = bound_route.split("/")[1:]
    if segments and segments[-1] == "":
        segments = segments[:-1]  # a trailing slash adds nothing
    if any(segment == "" for segment in segments):
        raise BindingError(f"route {bound_route!r} has an empty segment")
    destination = base + "/" + "/".join(quote(segment, safe="") for segment in segments)
    origin, _ = split_origin_and_path(destination)
    if origin != base:
        raise BindingError(f"destination {destination!r} left the origin {base!r}")
    return destination


# --- values, targets, conditions -----------------------------------------------------------------


def bind_value(value: LiteralValue | InputRef, bound: Mapping[str, str]) -> str:
    if isinstance(value, InputRef):
        if value.input_name not in bound:
            raise BindingError(f"INPUT_REF {value.input_name!r} is not bound")
        return bound[value.input_name]
    return bind_text(value.value, bound)


def bind_strategy(strategy: TargetStrategy, bound: Mapping[str, str]) -> BoundStrategy:
    try:
        return BoundStrategy(
            role=bind_text(strategy.role, bound) if strategy.role else None,
            name=bind_text(strategy.name, bound) if strategy.name else None,
            scope=bind_text(strategy.scope, bound) if strategy.scope else None,
            text_contains=(
                bind_text(strategy.text_contains, bound) if strategy.text_contains else None
            ),
        )
    except ValueError as exc:  # a bound value that itself looks like a placeholder
        raise BindingError(str(exc)) from exc


def bind_descriptor(descriptor: TargetDescriptor, bound: Mapping[str, str]) -> BoundDescriptor:
    return BoundDescriptor(strategies=[bind_strategy(s, bound) for s in descriptor.strategies])


def bind_condition(condition: Condition, bound: Mapping[str, str]):
    try:
        if isinstance(condition, RouteMatches):
            return BoundRouteMatches(route=bind_route(condition.route, bound))
        if isinstance(condition, ElementPresent):
            return BoundElementPresent(target=bind_descriptor(condition.target, bound))
        if isinstance(condition, TextPresent):
            return BoundTextPresent(text=bind_text(condition.text, bound))
        if isinstance(condition, ValueEquals):
            return BoundValueEquals(
                target=bind_descriptor(condition.target, bound),
                value=bind_value(condition.value, bound),
            )
    except BindingError:
        raise
    except ValueError as exc:
        raise BindingError(str(exc)) from exc
    raise BindingError(f"unknown condition {type(condition).__name__}")  # closed vocabulary


def bind_known_outcome(outcome: KnownOutcome, bound: Mapping[str, str]) -> BoundKnownOutcome:
    return BoundKnownOutcome(
        code=outcome.code,
        description=outcome.description,
        detector=bind_condition(outcome.detector, bound),
    )


def bind_step(step: Step, bound: Mapping[str, str], base_url: str) -> BoundStep:
    if step.action is ActionType.NAVIGATE:
        route = bind_route(step.route or "", bound)
        return BoundStep(
            step_id=step.step_id,
            action=step.action,
            declared_risk=step.risk,
            route=route,
            destination=build_destination(base_url, route),
            postcondition=bind_condition(step.postcondition, bound) if step.postcondition else None,
        )
    return BoundStep(
        step_id=step.step_id,
        action=step.action,
        declared_risk=step.risk,
        target=bind_descriptor(step.target, bound) if step.target else None,
        value=bind_value(step.value, bound) if step.value is not None else None,
        output=step.output,
        postcondition=bind_condition(step.postcondition, bound) if step.postcondition else None,
    )


def bind_artifact(
    artifact: CapabilityArtifact, bound: Mapping[str, str], base_url: str
) -> BoundArtifact:
    """The whole artifact, bound once. Raises ``BindingError`` before anything is observed."""
    return BoundArtifact(
        steps=[bind_step(step, bound, base_url) for step in artifact.steps],
        success_checkpoint=[bind_condition(c, bound) for c in artifact.success_checkpoint],
        known_outcomes=[bind_known_outcome(o, bound) for o in artifact.known_outcomes],
    )
