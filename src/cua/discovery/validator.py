"""DecisionValidator — deterministic rejection of anything that cannot be dispatched safely.

Strict structured output guarantees a decision's *shape*; it cannot know the page. Before the
policy gate, application code judges every decision against the exact RAW observation it
answers, in this order (closed ``ValidationCode`` vocabulary):

    STALE_OBSERVATION      the decision does not echo the current observation
    UNKNOWN_REF            the ref is not in this observation (invented, or from an earlier one)
    SHAPE                  fields that do not fit the action (route on a CLICK, value on a READ, …)
    ILLEGAL_TARGET         action / element combination (FILL a button, CLICK a cell, disabled …)
    VALUE                  a LITERAL that stands for a bound input, or is not plain text
    NAVIGATION_NOT_ALLOWED NAVIGATE to a route outside the narrow model-navigation allowlist
    ROUTE                  a route that does not bind to one well-formed destination
    OUTPUT                 READ for an undeclared or already-produced output
    AMBIGUOUS_TARGET       the descriptor to be persisted resolves to != 1 element (fail closed)
    PREMATURE_FINISH       the goal verifier is not satisfied by the trace so far

Only a ``ValidAct`` carries a ``SurfaceAction``; an ``Invalid`` cannot be dispatched by
construction. The validator never calls the gate, the surface or the model, never picks a first
match, never rewrites a decision, and never consults policy — the gate remains the authority and
may be stricter (the model-navigation allowlist is a subset of it by composition).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from cua.artifact import InputRef, LiteralValue
from cua.discovery.goal import GoalSpec, GoalVerdict
from cua.discovery.observation import derive_semantic_target, identity_matches
from cua.discovery.templating import InputTemplater
from cua.discovery.trace import NormalizedTrace, OutcomeCandidate, SemanticTarget
from cua.domain import ActionType, SurfaceElement, SurfaceSnapshot
from cua.llm import ActDecision, BlockedDecision, DiscoveryDecision, FinishDecision
from cua.policy import ResolvedTarget
from cua.replay.binding import BindingError, bind_route, build_destination
from cua.surface import SurfaceAction

FILL_ROLES = frozenset({"textbox", "searchbox", "spinbutton"})
SELECT_ROLES = frozenset({"combobox", "listbox"})
CLICK_ROLES = frozenset(
    {
        "button",
        "link",
        "checkbox",
        "radio",
        "menuitem",
        "menuitemcheckbox",
        "menuitemradio",
        "tab",
        "option",
        "switch",
    }
)
MAX_LITERAL_LEN = 200


class ValidationCode(StrEnum):
    STALE_OBSERVATION = "STALE_OBSERVATION"
    UNKNOWN_REF = "UNKNOWN_REF"
    SHAPE = "SHAPE"
    ILLEGAL_TARGET = "ILLEGAL_TARGET"
    VALUE = "VALUE"
    NAVIGATION_NOT_ALLOWED = "NAVIGATION_NOT_ALLOWED"
    ROUTE = "ROUTE"
    OUTPUT = "OUTPUT"
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"
    PREMATURE_FINISH = "PREMATURE_FINISH"


@dataclass(frozen=True)
class ValidAct:
    action: SurfaceAction  # the only place a dispatchable action is created
    target: ResolvedTarget | None  # what the gate judges (None for NAVIGATE)
    semantic_target: SemanticTarget | None  # what the trace persists
    identity_matches: int | None
    element: SurfaceElement | None
    value_binding: InputRef | LiteralValue | None
    route_template: str | None
    output_name: str | None
    intent_summary: str


@dataclass(frozen=True)
class ValidFinish:
    verdict: GoalVerdict
    intent_summary: str


@dataclass(frozen=True)
class ValidBlocked:
    reason_code: str
    outcome_candidate: OutcomeCandidate | None
    intent_summary: str


@dataclass(frozen=True)
class Invalid:
    code: ValidationCode
    feedback: str  # raw; the loop templates it before it reaches the model


Validated = ValidAct | ValidFinish | ValidBlocked | Invalid


def normalize_route(route: str) -> str:
    return route if route == "/" else route.rstrip("/")


class DecisionValidator:
    def __init__(
        self,
        *,
        goal: GoalSpec,
        bound_inputs: Mapping[str, str],
        base_url: str,
        navigation_routes: tuple[str, ...],
        trace_templater: InputTemplater,
    ) -> None:
        self._goal = goal
        self._bound = dict(bound_inputs)
        self._base_url = base_url
        self._routes = frozenset(normalize_route(r) for r in navigation_routes)
        self._trace_templater = trace_templater
        self._value_templater = InputTemplater(self._bound, trace_templater.style)

    def validate(
        self, decision: DiscoveryDecision, snapshot: SurfaceSnapshot, trace: NormalizedTrace
    ) -> Validated:
        if decision.observation_index != snapshot.step_index:
            return Invalid(
                ValidationCode.STALE_OBSERVATION,
                f"decision answers observation {decision.observation_index}; the current "
                f"observation is {snapshot.step_index}",
            )
        if isinstance(decision, FinishDecision):
            return self._finish(decision, trace)
        if isinstance(decision, BlockedDecision):
            return self._blocked(decision, snapshot)
        assert isinstance(decision, ActDecision)
        if decision.action_type is ActionType.NAVIGATE:
            return self._navigate(decision)
        return self._targeted(decision, snapshot, trace)

    # --- FINISH / REPORT_BLOCKED ---------------------------------------------------------------

    def _finish(self, decision: FinishDecision, trace: NormalizedTrace) -> Validated:
        verdict = self._goal.verifier.verify(trace, self._bound)
        if not verdict.satisfied:
            return Invalid(ValidationCode.PREMATURE_FINISH, f"goal not satisfied: {verdict.reason}")
        return ValidFinish(verdict=verdict, intent_summary=decision.intent_summary)

    def _blocked(self, decision: BlockedDecision, snapshot: SurfaceSnapshot) -> Validated:
        candidate: OutcomeCandidate | None = None
        if decision.evidence_ref is not None:
            element = _element(snapshot, decision.evidence_ref)
            if element is None:
                return Invalid(
                    ValidationCode.UNKNOWN_REF,
                    f"evidence_ref {decision.evidence_ref!r} is not in the current observation",
                )
            text = element.value if element.value is not None else element.accessible_name
            candidate = OutcomeCandidate(
                reason_code=decision.reason_code.value,
                target=self._trace_target(derive_semantic_target(element)),
                text_template=self._trace_templater.text(text) if text else None,
            )
        return ValidBlocked(
            reason_code=decision.reason_code.value,
            outcome_candidate=candidate,
            intent_summary=decision.intent_summary,
        )

    # --- NAVIGATE -------------------------------------------------------------------------------

    def _navigate(self, decision: ActDecision) -> Validated:
        if decision.ref is not None or decision.value is not None or decision.output_name:
            return Invalid(ValidationCode.SHAPE, "NAVIGATE takes a route only: no ref/value/output")
        if not decision.route:
            return Invalid(ValidationCode.SHAPE, "NAVIGATE requires route")
        route = normalize_route(decision.route)
        if route not in self._routes:
            return Invalid(
                ValidationCode.NAVIGATION_NOT_ALLOWED,
                f"route {decision.route!r} is not an entry route; allowed: {sorted(self._routes)}",
            )
        for name, value in self._bound.items():
            if value in route.split("/"):
                return Invalid(
                    ValidationCode.ROUTE,
                    f"route carries the value of input {name!r} literally; use a placeholder",
                )
        try:
            bound_route = bind_route(route, self._bound)
            destination = build_destination(self._base_url, bound_route)
        except BindingError as exc:
            return Invalid(ValidationCode.ROUTE, f"route cannot be bound: {exc}")
        return ValidAct(
            action=SurfaceAction(action_type=ActionType.NAVIGATE, url=destination),
            target=None,
            semantic_target=None,
            identity_matches=None,
            element=None,
            value_binding=None,
            route_template=route,
            output_name=None,
            intent_summary=decision.intent_summary,
        )

    # --- CLICK / FILL / SELECT / READ -------------------------------------------------------------

    def _targeted(
        self, decision: ActDecision, snapshot: SurfaceSnapshot, trace: NormalizedTrace
    ) -> Validated:
        action = decision.action_type
        if decision.route is not None:
            return Invalid(ValidationCode.SHAPE, f"{action.value} must not carry a route")
        if not decision.ref:
            return Invalid(ValidationCode.SHAPE, f"{action.value} requires a ref")
        element = _element(snapshot, decision.ref)
        if element is None:
            return Invalid(
                ValidationCode.UNKNOWN_REF,
                f"ref {decision.ref!r} is not in observation {snapshot.step_index}; choose a ref "
                "from the current observation",
            )
        needs_value = action in (ActionType.FILL, ActionType.SELECT)
        if needs_value and decision.value is None:
            return Invalid(ValidationCode.SHAPE, f"{action.value} requires a value binding")
        if not needs_value and decision.value is not None:
            return Invalid(ValidationCode.SHAPE, f"{action.value} must not carry a value")
        if action is ActionType.READ and not decision.output_name:
            return Invalid(ValidationCode.SHAPE, "READ requires output_name")
        if action is not ActionType.READ and decision.output_name:
            return Invalid(ValidationCode.SHAPE, f"{action.value} must not declare an output")

        illegal = _illegal_target(action, element)
        if illegal:
            return Invalid(ValidationCode.ILLEGAL_TARGET, illegal)

        bound_value: str | None = None
        if needs_value:
            assert decision.value is not None
            problem = self._check_value(decision.value)
            if problem:
                return Invalid(ValidationCode.VALUE, problem)
            bound_value = (
                self._bound[decision.value.input_name]
                if isinstance(decision.value, InputRef)
                else decision.value.value
            )
        if action is ActionType.READ:
            assert decision.output_name is not None
            if decision.output_name not in self._goal.outputs:
                return Invalid(
                    ValidationCode.OUTPUT,
                    f"{decision.output_name!r} is not a declared output; declared: "
                    f"{sorted(self._goal.outputs)}",
                )
            if any(s.output_name == decision.output_name for s in trace.steps):
                return Invalid(
                    ValidationCode.OUTPUT,
                    f"output {decision.output_name!r} was already read; FINISH or read another",
                )

        semantic = derive_semantic_target(element)
        matches = identity_matches(snapshot, semantic)
        if matches != 1:
            return Invalid(
                ValidationCode.AMBIGUOUS_TARGET,
                f"{matches} equivalent candidates for {_describe(semantic)}; the UI cannot be "
                "targeted unambiguously by ref — choose a different target or REPORT_BLOCKED "
                "(UI_AMBIGUOUS)",
            )
        return ValidAct(
            action=SurfaceAction(action_type=action, ref=element.ref, value=bound_value),
            target=ResolvedTarget(
                role=element.role,
                accessible_name=element.accessible_name,
                context_hint=element.context_hint,
                value=element.value,
            ),
            semantic_target=self._trace_target(semantic),
            identity_matches=matches,
            element=element,
            value_binding=decision.value,
            route_template=None,
            output_name=decision.output_name,
            intent_summary=decision.intent_summary,
        )

    def _check_value(self, value: InputRef | LiteralValue) -> str | None:
        if isinstance(value, InputRef):
            if value.input_name not in self._bound:
                return f"INPUT_REF {value.input_name!r} is not a declared input"
            return None
        text = value.value
        if len(text) > MAX_LITERAL_LEN:
            return f"LITERAL longer than {MAX_LITERAL_LEN} characters"
        if any(ord(c) < 32 for c in text):
            return "LITERAL must not contain control characters"
        if "<input:" in text:
            return "LITERAL must not copy an <input:...> token; use INPUT_REF"
        for name, bound in self._bound.items():
            if text == bound or self._value_templater.contains_bound_value(text):
                return f"LITERAL stands for the declared input {name!r}; use INPUT_REF"
        return None

    def _trace_target(self, target: SemanticTarget) -> SemanticTarget:
        return SemanticTarget(
            role=target.role,
            accessible_name=self._trace_templater.text(target.accessible_name),
            context_hint=self._trace_templater.text(target.context_hint),
        )


# --- helpers -------------------------------------------------------------------------------------


def _element(snapshot: SurfaceSnapshot, ref: str) -> SurfaceElement | None:
    return next((e for e in snapshot.elements if e.ref == ref), None)


def _illegal_target(action: ActionType, element: SurfaceElement) -> str | None:
    role = element.role
    if action is ActionType.FILL and role not in FILL_ROLES:
        return f"FILL needs a text input, not a {role}"
    if action is ActionType.SELECT and role not in SELECT_ROLES:
        return f"SELECT needs a combobox/listbox, not a {role}"
    if action is ActionType.CLICK and role not in CLICK_ROLES:
        return f"CLICK needs a control (button, link, …), not a {role}"
    if action is ActionType.READ and not (element.value or element.accessible_name):
        return "READ needs an element with text"
    if action is not ActionType.READ and not element.enabled:
        return f"the {role} is disabled"
    return None


def _describe(target: SemanticTarget) -> str:
    parts = [target.role]
    if target.accessible_name:
        parts.append(repr(target.accessible_name))
    if target.context_hint:
        parts.append(f"in {target.context_hint!r}")
    return " ".join(parts)


__all__ = [
    "CLICK_ROLES",
    "FILL_ROLES",
    "MAX_LITERAL_LEN",
    "SELECT_ROLES",
    "DecisionValidator",
    "Invalid",
    "Validated",
    "ValidAct",
    "ValidBlocked",
    "ValidFinish",
    "ValidationCode",
    "normalize_route",
]
