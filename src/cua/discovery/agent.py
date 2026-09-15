"""DiscoveryAgent — the genuine observe → decide → validate → gate → act → observe loop
(ARCHITECTURE §5, REQUIREMENTS §3).

``DiscoveryDeps`` is the one dependency set in ``cua`` that holds a model: surface, gate, clock,
evidence recorder, ``LLMClient``. Every step:

    fresh RAW SurfaceSnapshot            -> OBSERVATION evidence; no-progress check
    -> model-safe DiscoveryRequest       (bound input values templated to <input:name>; A1)
    -> LLMClient.decide                  (one corrective retry on an invalid decision; A3: one
                                          MODEL_CALL event == one provider HTTP attempt)
    -> DecisionValidator                 (against the exact RAW snapshot; ambiguity fails closed)
    -> ActionGate.dispatch               (the ONLY path to Surface.act; declared_risk is never
                                          supplied, so the model cannot touch risk or policy)
    -> raw step record                   (normalized trace and model history derive from it)

Stop reasons: ``GOAL_REACHED | MAX_STEPS | TIMEOUT | DEAD_END | BLOCKED_BY_POLICY | MODEL_ERROR``.
``GOAL_REACHED`` exists only when the deterministic ``GoalVerifier`` accepts the trace; the
model's FINISH is a proposal. A ``REPORT_BLOCKED`` is a ``DEAD_END`` and authorizes nothing.
Policy ``DENY`` and ``REQUIRE_INTERVENTION`` both stop the run as ``BLOCKED_BY_POLICY`` with zero
dispatch (HITL suspension arrives at ARCHITECTURE §15 step 16). The dead-end rule is small and
deterministic: ``no_progress_limit`` consecutive dispatched actions after which the page digest is
unchanged, a reported block, or an ambiguous target refused twice.

Time: the loop deadline uses the injected ``Clock``; the remaining time is passed to the model
client so a provider call cannot outlive the run by more than one call timeout.

Evidence failure (fail closed, never repeat): the first ``EvidenceError`` ends the run as
``DEAD_END / EVIDENCE_ERROR``; nothing is written afterwards, no action is repeated, and a run
whose terminal event could not be written is returned without its outputs — a proof that could
not be recorded is not a proof (A4).

Error boundary: only ``SurfaceDriverError`` / ``UnknownRefError`` (driver), ``EvidenceError``
(evidence) and ``ProviderError`` / ``ModelOutputError`` (model) are handled. Contract violations,
the gate's ``ValueError``s, a request that would leak a bound value, and every other exception are
programming errors and propagate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import field_validator

from cua.artifact import InputRef, LiteralValue
from cua.discovery.goal import GoalSpec, GoalVerdict, bind_goal_inputs
from cua.discovery.observation import (
    MAX_ELEMENTS,
    digest,
    input_evidence,
    to_observation,
)
from cua.discovery.result import DiscoveryResult, StopCode, StopDetail, StopReason
from cua.discovery.summaries import (
    decision_summary,
    discovery_ended_payload,
    discovery_started_payload,
    model_call_payload,
    observation_payload,
)
from cua.discovery.templating import InputTemplater, PlaceholderStyle
from cua.discovery.trace import NormalizedTrace, OutcomeCandidate, SemanticTarget, TraceStep
from cua.discovery.validator import (
    DecisionValidator,
    Invalid,
    ValidAct,
    ValidationCode,
    ValidBlocked,
    ValidFinish,
)
from cua.domain import ActionType, DomainModel, SurfaceElement, SurfaceSnapshot
from cua.domain.ids import new_run_id
from cua.evidence import EvidenceError, EvidenceRecorder, RunKind
from cua.llm import (
    DeclaredInput,
    DeclaredOutput,
    DiscoveryRequest,
    GoalStatement,
    HistoryEntry,
    HistoryTarget,
    LLMClient,
    ModelOutputError,
    ProviderError,
)
from cua.policy import ActionGate, GateDecision, PolicyConfig, any_route_matches
from cua.replay.binding import normalize_base_url
from cua.replay.clock import Clock
from cua.surface.contract import ActResult, Surface, SurfaceDriverError, UnknownRefError

_SURFACE_RUNTIME_ERRORS = (SurfaceDriverError, UnknownRefError)

PURPOSE_DECIDE = "DECIDE"
PURPOSE_CORRECTIVE_RETRY = "CORRECTIVE_RETRY"


class DiscoveryConfig(DomainModel):
    base_url: str  # origin only
    max_steps: int = 25
    timeout_s: float = 300.0
    no_progress_limit: int = 3
    navigation_routes: tuple[str, ...]  # the narrow entry-route list shown to the model (A2)
    model_call_timeout_s: float = 90.0
    mask_bound_inputs: bool = True  # False = DIAGNOSTIC run; never an official E01 (A7)

    @field_validator("base_url")
    @classmethod
    def _origin_only(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("max_steps", "no_progress_limit")
    @classmethod
    def _at_least_one(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be >= 1")
        return value

    @field_validator("timeout_s", "model_call_timeout_s")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be > 0")
        return value

    @field_validator("navigation_routes")
    @classmethod
    def _paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for route in value:
            if not route.startswith("/") or "://" in route:
                raise ValueError(f"navigation route {route!r} must be a path")
        return value


def assert_navigation_routes_within_policy(routes: tuple[str, ...], policy: PolicyConfig) -> None:
    """Composition-time check: the model-navigation allowlist is a subset of the policy allowlist.

    The gate remains the authority either way; this makes a misconfiguration loud at wiring time.
    """
    outside = [r for r in routes if not any_route_matches(policy.allowed_routes, r)]
    if outside:
        raise ValueError(f"navigation routes outside the policy allowlist: {outside}")


@dataclass(frozen=True)
class DiscoveryDeps:
    surface: Surface
    action_gate: ActionGate
    clock: Clock
    evidence: EvidenceRecorder
    llm: LLMClient


class _Stop(Exception):
    """Internal unwind carrying the stop reason. Never leaves ``DiscoveryAgent.run``."""

    def __init__(
        self, reason: StopReason, detail: StopDetail, verdict: GoalVerdict | None = None
    ) -> None:
        super().__init__(detail.code.value)
        self.reason = reason
        self.detail = detail
        self.verdict = verdict


@dataclass
class _StepRaw:
    """One dispatched, completed action, with raw observed values (in memory only)."""

    step_index: int
    action_type: ActionType
    element: SurfaceElement | None
    semantic_target: SemanticTarget | None  # already TRACE-templated by the validator
    identity_matches: int | None
    value_binding: InputRef | LiteralValue | None
    route_template: str | None
    output_name: str | None
    read_text: str | None
    input_evidence: dict[str, str]
    intent_summary: str
    gate_decision: str
    effective_risk: str
    url_before: str
    url_after: str
    page_title_after: str | None  # filled by the next observation


@dataclass
class _RunState:
    run_id: str
    session_id: str
    bound: dict[str, str]
    steps: list[_StepRaw] = field(default_factory=list)
    model_calls: int = 0
    corrective_retries: int = 0
    unchanged: int = 0
    pending_digest: str | None = None  # digest before the last dispatched action
    outcome_candidate: OutcomeCandidate | None = None
    last_snapshot: SurfaceSnapshot | None = None


class DiscoveryAgent:
    def __init__(self, deps: DiscoveryDeps, config: DiscoveryConfig) -> None:
        self._deps = deps
        self._config = config

    # --- entry point -------------------------------------------------------------------------

    def run(self, goal: GoalSpec, inputs: Mapping[str, str]) -> DiscoveryResult:
        bound = bind_goal_inputs(goal, inputs)  # a caller error raises before anything happens
        state = _RunState(
            run_id=new_run_id(), session_id=self._deps.surface.session_id, bound=bound
        )
        trace_templater = InputTemplater(bound, PlaceholderStyle.TRACE)
        model_templater = (
            InputTemplater(bound, PlaceholderStyle.MODEL)
            if self._config.mask_bound_inputs
            else None
        )
        validator = DecisionValidator(
            goal=goal,
            bound_inputs=bound,
            base_url=self._config.base_url,
            navigation_routes=self._config.navigation_routes,
            trace_templater=trace_templater,
        )
        try:
            try:
                self._begin_evidence(goal, state)
                stop = self._loop(goal, state, validator, trace_templater, model_templater)
            except _Stop as unwind:
                stop = unwind
            result = self._result(goal, state, stop, trace_templater)
            return self._record_terminal(result, state)
        finally:
            self._deps.evidence.end_run()

    # --- the loop ------------------------------------------------------------------------------

    def _loop(
        self,
        goal: GoalSpec,
        state: _RunState,
        validator: DecisionValidator,
        trace_templater: InputTemplater,
        model_templater: InputTemplater | None,
    ) -> _Stop:
        clock = self._deps.clock
        deadline = clock.now() + self._config.timeout_s
        for step_index in range(1, self._config.max_steps + 1):
            if clock.now() >= deadline:
                return _Stop(StopReason.TIMEOUT, _detail(StopCode.TIMEOUT, "run deadline reached"))
            self._deps.evidence.begin_step(f"d{step_index}", step_index)
            snapshot = self._observe(state)
            if state.steps and state.steps[-1].page_title_after is None:
                state.steps[-1].page_title_after = snapshot.page_title
            self._check_progress(state, snapshot)
            validated = self._decide(
                goal,
                state,
                validator,
                trace_templater,
                model_templater,
                snapshot,
                step_index,
                deadline,
            )
            if isinstance(validated, ValidFinish):
                return _Stop(
                    StopReason.GOAL_REACHED,
                    _detail(StopCode.GOAL_VERIFIED, validated.verdict.reason),
                    validated.verdict,
                )
            if isinstance(validated, ValidBlocked):
                state.outcome_candidate = validated.outcome_candidate
                return _Stop(
                    StopReason.DEAD_END,
                    _detail(
                        StopCode.REPORTED_BLOCKED,
                        f"the model reported it is blocked ({validated.reason_code}): "
                        f"{trace_templater.text(validated.intent_summary)}",
                    ),
                )
            assert isinstance(validated, ValidAct)
            state.pending_digest = digest(snapshot)
            self._dispatch(state, snapshot, validated, step_index)
        return _Stop(
            StopReason.MAX_STEPS,
            _detail(
                StopCode.MAX_STEPS, f"{self._config.max_steps} steps without reaching the goal"
            ),
        )

    def _check_progress(self, state: _RunState, snapshot: SurfaceSnapshot) -> None:
        """The small deterministic dead-end rule: N dispatched actions that changed nothing."""
        if state.pending_digest is None:
            return
        if digest(snapshot) == state.pending_digest:
            state.unchanged += 1
            if state.unchanged >= self._config.no_progress_limit:
                raise _Stop(
                    StopReason.DEAD_END,
                    _detail(
                        StopCode.NO_PROGRESS,
                        f"{state.unchanged} consecutive actions left the page unchanged",
                    ),
                )
        else:
            state.unchanged = 0
        state.pending_digest = None

    # --- observe ---------------------------------------------------------------------------------

    def _observe(self, state: _RunState) -> SurfaceSnapshot:
        try:
            snapshot = self._deps.surface.observe()
        except _SURFACE_RUNTIME_ERRORS as exc:
            raise _Stop(
                StopReason.DEAD_END,
                _detail(StopCode.SURFACE_ERROR, f"observation failed: {type(exc).__name__}: {exc}"),
            ) from exc
        state.last_snapshot = snapshot
        try:
            self._deps.evidence.observation(
                observation_payload(
                    snapshot,
                    digest=digest(snapshot),
                    truncated=len(snapshot.elements) > MAX_ELEMENTS,
                )
            )
        except EvidenceError as exc:
            raise _Stop(StopReason.DEAD_END, _evidence_detail(exc)) from exc
        return snapshot

    # --- decide (one corrective retry) -----------------------------------------------------------

    def _decide(
        self,
        goal: GoalSpec,
        state: _RunState,
        validator: DecisionValidator,
        trace_templater: InputTemplater,
        model_templater: InputTemplater | None,
        snapshot: SurfaceSnapshot,
        step_index: int,
        deadline: float,
    ) -> ValidAct | ValidFinish | ValidBlocked:
        feedback: str | None = None
        first_code: ValidationCode | None = None
        trace = self._trace(goal, state, trace_templater)
        for purpose in (PURPOSE_DECIDE, PURPOSE_CORRECTIVE_RETRY):
            if self._deps.clock.now() >= deadline:
                raise _Stop(StopReason.TIMEOUT, _detail(StopCode.TIMEOUT, "run deadline reached"))
            request = self._build_request(
                goal, state, model_templater, snapshot, step_index, deadline, feedback
            )
            retries = state.corrective_retries
            state.model_calls += 1
            try:
                reply = self._deps.llm.decide(request)
            except ProviderError as exc:
                self._record_model_call(
                    state,
                    step_index,
                    purpose,
                    call=None,
                    outcome="PROVIDER_ERROR",
                    decision=None,
                    validation=None,
                    corrective_retries=retries,
                    error_kind=exc.kind.value,
                    status_code=exc.status_code,
                )
                raise _Stop(
                    StopReason.MODEL_ERROR,
                    StopDetail(
                        code=StopCode.PROVIDER_ERROR,
                        message=str(exc),  # fixed template: never a body, never a credential
                        provider_error_kind=exc.kind.value,
                    ),
                ) from exc
            except ModelOutputError as exc:
                invalid = Invalid(ValidationCode.SHAPE, exc.feedback)
                self._record_model_call(
                    state,
                    step_index,
                    purpose,
                    call=None,
                    outcome="OUTPUT_ERROR",
                    decision=None,
                    validation=invalid,
                    corrective_retries=retries,
                )
            else:
                validated = validator.validate(reply.decision, snapshot, trace)
                element = _element_of(snapshot, reply.decision)
                self._record_model_call(
                    state,
                    step_index,
                    purpose,
                    call=reply.call,
                    outcome="DECISION",
                    decision=decision_summary(reply.decision, element),
                    validation=validated if isinstance(validated, Invalid) else None,
                    corrective_retries=retries,
                )
                if not isinstance(validated, Invalid):
                    return validated
                invalid = validated
            if purpose == PURPOSE_CORRECTIVE_RETRY:
                assert first_code is not None
                codes = [first_code.value, invalid.code.value]
                if invalid.code is ValidationCode.AMBIGUOUS_TARGET:
                    # A property of the UI, not of the model's output: the same condition replay
                    # reports as AMBIGUOUS_TARGET. Nothing was dispatched.
                    raise _Stop(
                        StopReason.DEAD_END,
                        StopDetail(
                            code=StopCode.AMBIGUOUS_TARGET,
                            message=trace_templater.text(invalid.feedback) or "",
                            validation_codes=codes,
                        ),
                    )
                raise _Stop(
                    StopReason.MODEL_ERROR,
                    StopDetail(
                        code=StopCode.INVALID_DECISION,
                        message=(
                            "the decision was still invalid after one corrective retry: "
                            f"{trace_templater.text(invalid.feedback)}"
                        ),
                        validation_codes=codes,
                    ),
                )
            first_code = invalid.code
            state.corrective_retries += 1
            feedback = f"INVALID({invalid.code.value}): {invalid.feedback}"
        raise AssertionError("unreachable")  # pragma: no cover

    def _record_model_call(
        self,
        state: _RunState,
        step_index: int,
        purpose: str,
        *,
        call,
        outcome: str,
        decision,
        validation: Invalid | None,
        corrective_retries: int,
        error_kind: str | None = None,
        status_code: int | None = None,
    ) -> None:
        if outcome == "DECISION":
            status = "INVALID" if validation else "VALID"
        else:
            status = "INVALID" if validation else None
        payload = model_call_payload(
            call_index=state.model_calls,
            step_index=step_index,
            purpose=purpose,
            provider=self._deps.llm.provider,
            model_id=self._deps.llm.model_id,
            call=call,
            outcome=outcome,
            decision=decision,
            validation_status=status,
            validation_code=validation.code.value if validation else None,
            validation_feedback=validation.feedback if validation else None,
            corrective_retry_count=corrective_retries,
            error_kind=error_kind,
            status_code=status_code,
        )
        try:
            self._deps.evidence.model_call(payload)
        except EvidenceError as exc:
            raise _Stop(StopReason.DEAD_END, _evidence_detail(exc)) from exc

    # --- request building (model-safe) -----------------------------------------------------------

    def _build_request(
        self,
        goal: GoalSpec,
        state: _RunState,
        model_templater: InputTemplater | None,
        snapshot: SurfaceSnapshot,
        step_index: int,
        deadline: float,
        feedback: str | None,
    ) -> DiscoveryRequest:
        masked = model_templater is not None

        def safe(text: str | None) -> str | None:
            return model_templater.text(text) if model_templater else text

        def safe_url(url: str) -> str:
            return model_templater.url(url) if model_templater else url

        history = [
            HistoryEntry(
                step_index=s.step_index,
                action_type=s.action_type.value,
                target=(
                    HistoryTarget(
                        role=s.element.role,
                        accessible_name=safe(s.element.accessible_name),
                        context_hint=safe(s.element.context_hint),
                    )
                    if s.element
                    else None
                ),
                value_binding=_binding_text(s.value_binding),
                route=s.route_template,
                url_after=safe_url(s.url_after),
                read_text=safe(s.read_text),
                output_name=s.output_name,
            )
            for s in state.steps
        ]
        request = DiscoveryRequest(
            goal=GoalStatement(
                name=goal.name,
                description=goal.description,
                inputs=[
                    DeclaredInput(
                        name=name,
                        type=spec.type.value,
                        description=spec.description,
                        placeholder=f"<input:{name}>",
                    )
                    for name, spec in goal.inputs.items()
                ],
                outputs=[
                    DeclaredOutput(name=name, type=spec.type.value, description=spec.description)
                    for name, spec in goal.outputs.items()
                ],
            ),
            inputs_masked=masked,
            input_values={} if masked else dict(state.bound),
            observation=to_observation(snapshot, model_templater),
            history=history,
            step_index=step_index,
            steps_remaining=self._config.max_steps - step_index,
            seconds_remaining=max(0.0, deadline - self._deps.clock.now()),
            navigation_routes=list(self._config.navigation_routes),
            feedback=safe(feedback),
        )
        if model_templater is not None and model_templater.contains_bound_value(
            request.model_dump_json()
        ):
            raise RuntimeError(
                "model-safe invariant violated: a bound input value survived in the request"
            )
        return request

    # --- dispatch (through the gate only) --------------------------------------------------------

    def _dispatch(
        self, state: _RunState, snapshot: SurfaceSnapshot, valid: ValidAct, step_index: int
    ) -> None:
        gate = self._deps.action_gate
        surface = self._deps.surface
        recorded_before = self._deps.evidence.dispatch_count
        action = valid.action
        try:
            if action.action_type is ActionType.NAVIGATE:
                gate_result, act_result = gate.dispatch(surface, action)
            else:
                gate_result, act_result = gate.dispatch(
                    surface, action, snapshot=snapshot, target=valid.target
                )
        except EvidenceError as exc:
            raise _Stop(StopReason.DEAD_END, _evidence_detail(exc)) from exc
        except _SURFACE_RUNTIME_ERRORS as exc:
            attempted = self._deps.evidence.dispatch_count > recorded_before
            raise _Stop(
                StopReason.DEAD_END,
                _detail(
                    StopCode.SURFACE_ERROR,
                    f"{action.action_type.value} failed: {type(exc).__name__}: {exc}; the driver "
                    f"action {'WAS' if attempted else 'was NOT'} attempted; it is never repeated",
                ),
            ) from exc
        if gate_result.decision is GateDecision.DENY:
            raise _Stop(
                StopReason.BLOCKED_BY_POLICY,
                StopDetail(
                    code=StopCode.POLICY_DENIED,
                    message=gate_result.explanation,
                    gate_decision=gate_result.decision.value,
                    deny_reason=gate_result.deny_reason.value if gate_result.deny_reason else None,
                ),
            )
        if gate_result.decision is GateDecision.REQUIRE_INTERVENTION:
            raise _Stop(
                StopReason.BLOCKED_BY_POLICY,
                StopDetail(
                    code=StopCode.INTERVENTION_REQUIRED,
                    message=gate_result.explanation,
                    gate_decision=gate_result.decision.value,
                ),
            )
        assert act_result is not None  # ALLOW: the gate dispatched exactly once
        self._assert_dispatch_recorded(state)
        state.steps.append(
            self._step_raw(state, snapshot, valid, step_index, gate_result, act_result)
        )

    def _assert_dispatch_recorded(self, state: _RunState) -> None:
        """Every dispatched step has an ACTION_DISPATCHED behind it (same wiring invariant as
        the replay engine): the surface must be the one wired to ``deps.evidence``."""
        recorded = self._deps.evidence.dispatch_count
        expected = len(state.steps) + 1
        if recorded != expected:
            raise RuntimeError(
                f"discovery invariant violated: {expected} action(s) dispatched but {recorded} "
                "ACTION_DISPATCHED event(s) recorded — the surface is not wired to the run's "
                "evidence recorder"
            )

    def _step_raw(
        self,
        state: _RunState,
        snapshot: SurfaceSnapshot,
        valid: ValidAct,
        step_index: int,
        gate_result,
        act_result: ActResult,
    ) -> _StepRaw:
        is_read = valid.action.action_type is ActionType.READ
        return _StepRaw(
            step_index=step_index,
            action_type=valid.action.action_type,
            element=valid.element,
            semantic_target=valid.semantic_target,
            identity_matches=valid.identity_matches,
            value_binding=valid.value_binding,
            route_template=valid.route_template,
            output_name=valid.output_name,
            read_text=act_result.value if is_read else None,
            input_evidence=input_evidence(snapshot, state.bound) if is_read else {},
            intent_summary=valid.intent_summary,
            gate_decision=gate_result.decision.value,
            effective_risk=gate_result.risk.value,
            url_before=snapshot.url,
            url_after=act_result.url_after,
            page_title_after=None,  # known only from the next observation
        )

    # --- trace and result ------------------------------------------------------------------------

    def _trace(
        self, goal: GoalSpec, state: _RunState, templater: InputTemplater
    ) -> NormalizedTrace:
        return NormalizedTrace(
            goal_name=goal.name,
            inputs_declared={name: spec.type.value for name, spec in goal.inputs.items()},
            outputs_declared={name: spec.type.value for name, spec in goal.outputs.items()},
            steps=[_trace_step(s, templater) for s in state.steps],
            outcome_candidate=state.outcome_candidate,
            provider=self._deps.llm.provider,
            model_id=self._deps.llm.model_id,
            discovery_run_id=state.run_id,
            session_id=state.session_id,
        )

    def _result(
        self, goal: GoalSpec, state: _RunState, stop: _Stop, templater: InputTemplater
    ) -> DiscoveryResult:
        verdict = stop.verdict if stop.reason is StopReason.GOAL_REACHED else None
        return DiscoveryResult(
            run_id=state.run_id,
            session_id=state.session_id,
            goal_name=goal.name,
            stop_reason=stop.reason,
            detail=stop.detail,
            outputs=dict(verdict.outputs) if verdict else {},
            trace=self._trace(goal, state, templater),
            verdict=verdict,
            model_calls=state.model_calls,
            corrective_retries=state.corrective_retries,
            provider=self._deps.llm.provider,
            model_id=self._deps.llm.model_id,
        )

    # --- evidence bracket ------------------------------------------------------------------------

    def _begin_evidence(self, goal: GoalSpec, state: _RunState) -> None:
        """DISCOVERY_STARTED before anything else; the bound inputs are registered with the
        redactor here, before any event that could carry runtime state."""
        try:
            self._deps.evidence.begin_run(
                run_id=state.run_id,
                run_kind=RunKind.DISCOVERY,
                session_id=state.session_id,
                artifact_id=None,
                inputs=state.bound,
                payload=discovery_started_payload(
                    goal,
                    provider=self._deps.llm.provider,
                    model_id=self._deps.llm.model_id,
                    base_url=self._config.base_url,
                    max_steps=self._config.max_steps,
                    timeout_s=self._config.timeout_s,
                    navigation_routes=self._config.navigation_routes,
                    mask_bound_inputs=self._config.mask_bound_inputs,
                ),
            )
        except EvidenceError as exc:
            raise _Stop(StopReason.DEAD_END, _evidence_detail(exc)) from exc

    def _record_terminal(self, result: DiscoveryResult, state: _RunState) -> DiscoveryResult:
        """Persist DISCOVERY_ENDED. Once evidence has failed, nothing more is written; a run whose
        terminal event cannot be written is returned without its outputs (A4)."""
        recorder = self._deps.evidence
        if not recorder.active or recorder.evidence_failed:
            return result
        payload = discovery_ended_payload(result, dispatched_actions=recorder.dispatch_count)
        try:
            recorder.discovery_ended(payload)
        except EvidenceError as exc:
            attempted = recorder.dispatch_count
            return DiscoveryResult(
                run_id=result.run_id,
                session_id=result.session_id,
                goal_name=result.goal_name,
                stop_reason=StopReason.DEAD_END,
                detail=StopDetail(
                    code=StopCode.EVIDENCE_ERROR,
                    message=(
                        "terminal evidence could not be recorded; the run's in-memory stop reason "
                        f"was {result.stop_reason.value} and its outputs are discarded; "
                        f"{attempted} driver action(s) were attempted and are in evidence up to "
                        f"the last persisted event; none is repeated: {exc}"
                    ),
                ),
                outputs={},
                trace=result.trace,
                verdict=None,
                model_calls=result.model_calls,
                corrective_retries=result.corrective_retries,
                provider=result.provider,
                model_id=result.model_id,
            )
        return result


# --- helpers ---------------------------------------------------------------------------------


def _detail(code: StopCode, message: str) -> StopDetail:
    return StopDetail(code=code, message=message)


def _evidence_detail(exc: EvidenceError) -> StopDetail:
    event = exc.event_type.value if exc.event_type else "evidence"
    attempted = (
        "the driver action WAS attempted and its outcome is not in evidence; it is never repeated"
        if exc.after_dispatch
        else "the driver action was NOT attempted"
    )
    return StopDetail(
        code=StopCode.EVIDENCE_ERROR,
        message=f"evidence could not be recorded ({event}); {attempted}: {exc}",
    )


def _element_of(snapshot: SurfaceSnapshot, decision) -> SurfaceElement | None:
    ref = getattr(decision, "ref", None) or getattr(decision, "evidence_ref", None)
    if not ref:
        return None
    return next((e for e in snapshot.elements if e.ref == ref), None)


def _binding_text(binding: InputRef | LiteralValue | None) -> str | None:
    if binding is None:
        return None
    if isinstance(binding, InputRef):
        return f"INPUT_REF({binding.input_name})"
    return f"LITERAL({binding.value})"


def _trace_step(raw: _StepRaw, templater: InputTemplater) -> TraceStep:
    binding = raw.value_binding
    return TraceStep(
        step_index=raw.step_index,
        action_type=raw.action_type,
        target=raw.semantic_target,
        identity_matches=raw.identity_matches,
        value_binding_kind=binding.kind.value if binding else None,
        input_name=binding.input_name if isinstance(binding, InputRef) else None,
        literal_value=binding.value if isinstance(binding, LiteralValue) else None,
        route_template=raw.route_template,
        output_name=raw.output_name,
        read_text=templater.text(raw.read_text),
        input_evidence=dict(raw.input_evidence),
        intent_summary=templater.text(raw.intent_summary) or "",
        gate_decision=raw.gate_decision,
        effective_risk=raw.effective_risk,
        url_before_template=templater.url(raw.url_before),
        url_after_template=templater.url(raw.url_after),
        page_title_after_template=templater.text(raw.page_title_after),
    )
