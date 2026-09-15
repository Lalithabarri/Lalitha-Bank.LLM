"""ActionGate — the single authorization chokepoint (ARCHITECTURE §8, D09).

    caller -> bind values -> resolve target -> ActionGate.authorize(action, resolved target)
           -> ALLOW | DENY | REQUIRE_INTERVENTION -> Surface.act ONLY on ALLOW

``dispatch()`` is the only production call site of ``Surface.act()`` in ``cua``; a structural
test enforces that. Authorization is evaluated on the *resolved execution target* — never on a
claim: ``dispatch()`` derives origin and route itself, from the NAVIGATE action's destination
URL or from the URL of the ``SurfaceSnapshot`` the caller observed, and builds the
``GateRequest`` internally. The decision is deterministic given the configuration and the
control owner.

DENY is a hard boundary: nothing here, and nothing above, converts it into a request for
human approval. ``REQUIRE_INTERVENTION`` is reserved for ``IRREVERSIBLE`` risk (V1 rule).

Evidence (ARCHITECTURE §10): ``dispatch()`` reports every decision it is about to act on to a
``GateObserver`` — after ``authorize``, before ``Surface.act``. The observer is observational: it
cannot change the decision, is a constructor dependency rather than a ``dispatch`` parameter,
and is a Protocol so the gate never depends on a writer or a path. An observer that raises
prevents the dispatch (nothing reaches the driver unrecorded).
"""

from enum import StrEnum
from typing import Protocol

from cua.domain import ActionType, DomainModel, SurfaceSnapshot
from cua.hitl.control import ControlOwner
from cua.policy.config import PolicyConfig
from cua.policy.risk import RiskTier, classify_risk, effective_risk
from cua.policy.routes import split_origin_and_path
from cua.surface.contract import ActResult, Surface, SurfaceAction


class GateDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_INTERVENTION = "REQUIRE_INTERVENTION"


class DenyReason(StrEnum):
    CONTROL_NOT_OWNED = "CONTROL_NOT_OWNED"
    ACTION_TYPE_NOT_ALLOWED = "ACTION_TYPE_NOT_ALLOWED"
    ORIGIN_NOT_ALLOWED = "ORIGIN_NOT_ALLOWED"
    ROUTE_NOT_ALLOWED = "ROUTE_NOT_ALLOWED"


class ResolvedTarget(DomainModel):
    """The element an action is about to touch, in semantic terms. Deliberately no ref."""

    role: str
    accessible_name: str
    context_hint: str | None = None
    value: str | None = None


class GateRequest(DomainModel):
    action_type: ActionType
    origin: str
    route: str  # NAVIGATE: the destination path; otherwise the current page's path
    target: ResolvedTarget | None = None
    declared_risk: RiskTier | None = None  # artifact metadata; may raise, never lower


class GateResult(DomainModel):
    decision: GateDecision
    risk: RiskTier
    deny_reason: DenyReason | None = None
    explanation: str


class GateObserver(Protocol):
    def on_decision(self, request: GateRequest, result: GateResult) -> None:
        """Called by ``dispatch()`` after the decision, before any driver call. Cannot alter it."""
        ...


class NullGateObserver:
    def on_decision(self, request: GateRequest, result: GateResult) -> None:
        return None


class ActionGate:
    def __init__(
        self,
        config: PolicyConfig,
        control_owner: ControlOwner,
        observer: GateObserver | None = None,
    ) -> None:
        self._config = config
        self._control_owner = control_owner
        self._observer: GateObserver = observer or NullGateObserver()

    def authorize(self, request: GateRequest) -> GateResult:
        target_name = request.target.accessible_name if request.target else None
        policy_risk = classify_risk(
            self._config.risk_rules, request.action_type, request.route, target_name
        )
        risk = effective_risk(policy_risk, request.declared_risk)

        if not self._control_owner.is_automation:
            return _deny(
                risk,
                DenyReason.CONTROL_NOT_OWNED,
                f"control owner is {self._control_owner.state.value}, not AUTOMATION",
            )
        if not self._config.action_type_allowed(request.action_type):
            return _deny(
                risk,
                DenyReason.ACTION_TYPE_NOT_ALLOWED,
                f"action type {request.action_type.value} is not allowlisted",
            )
        if not self._config.origin_allowed(request.origin):
            return _deny(
                risk, DenyReason.ORIGIN_NOT_ALLOWED, f"origin {request.origin} is not allowlisted"
            )
        if not self._config.route_allowed(request.route):
            return _deny(
                risk, DenyReason.ROUTE_NOT_ALLOWED, f"route {request.route} is not allowlisted"
            )
        if risk is RiskTier.IRREVERSIBLE:
            return GateResult(
                decision=GateDecision.REQUIRE_INTERVENTION,
                risk=risk,
                explanation="IRREVERSIBLE actions are never dispatched automatically (V1)",
            )
        return GateResult(
            decision=GateDecision.ALLOW,
            risk=risk,
            explanation=f"{request.action_type.value} on {request.route} allowed as {risk.value}",
        )

    def dispatch(
        self,
        surface: Surface,
        action: SurfaceAction,
        *,
        snapshot: SurfaceSnapshot | None = None,
        target: ResolvedTarget | None = None,
        declared_risk: RiskTier | None = None,
    ) -> tuple[GateResult, ActResult | None]:
        """Authorize on observed state, then touch the driver only on ALLOW.

        NAVIGATE is authorized on the destination it will actually go to (``action.url``).
        Every other action is authorized on the page the caller actually observed
        (``snapshot.url``). Neither can be substituted by a caller-supplied route.
        """
        request = self.request_for(
            action, snapshot=snapshot, target=target, declared_risk=declared_risk
        )
        result = self.authorize(request)
        self._observer.on_decision(request, result)  # evidence; a raising observer stops here
        if result.decision is not GateDecision.ALLOW:
            return result, None
        return result, surface.act(action)

    @staticmethod
    def request_for(
        action: SurfaceAction,
        *,
        snapshot: SurfaceSnapshot | None = None,
        target: ResolvedTarget | None = None,
        declared_risk: RiskTier | None = None,
    ) -> GateRequest:
        """Build the request from the only trusted sources: the destination URL or the snapshot."""
        if action.action_type is ActionType.NAVIGATE:
            if not action.url:
                raise ValueError("NAVIGATE requires a destination url")
            origin, route = split_origin_and_path(action.url)
        else:
            if snapshot is None:
                raise ValueError(f"{action.action_type.value} requires the observed snapshot")
            origin, route = split_origin_and_path(snapshot.url)
        return GateRequest(
            action_type=action.action_type,
            origin=origin,
            route=route,
            target=target,
            declared_risk=declared_risk,
        )


def _deny(risk: RiskTier, reason: DenyReason, explanation: str) -> GateResult:
    return GateResult(
        decision=GateDecision.DENY, risk=risk, deny_reason=reason, explanation=explanation
    )
