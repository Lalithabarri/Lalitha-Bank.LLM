"""ReplayEngine — deterministic execution of a CapabilityArtifact (ARCHITECTURE §7, D15, D16).

``ReplayDeps`` has no field for a model, a client, or anything that decides: surface, gate,
clock. "No LLM in the decision loop" is a property of the type, not a convention.

For every ordered step:

    NAVIGATE:  destination (built at bind time)      -> ActionGate.dispatch(action.url)
    otherwise: fresh SurfaceSnapshot -> TargetResolver -> ephemeral ref
               -> ActionGate.dispatch with THAT snapshot -> Surface.act only inside the gate
    then:      READ  -> transform + type-check + record the output once
               else  -> fresh observations until the postcondition holds (bounded)
    always:    declared known outcomes are checked on every observation, before any
               generic resolution / postcondition / checkpoint failure is declared

Terminal result: exactly SUCCESS | BUSINESS_OUTCOME | FAILURE. Outputs are authoritative only
on SUCCESS. There is no automatic re-dispatch of any action in M4: the only "retries" are
observation polls inside one bounded window (resolution, postcondition, checkpoint).

Error boundary: only ``SurfaceDriverError`` and ``UnknownRefError`` — runtime failures of the
surface — become ``FAILURE / SURFACE_ERROR``. Contract violations (``StaleObservationError``,
``UnsupportedActionError``), the gate's ``ValueError``s, and every other exception are
programming errors and propagate.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from pydantic import field_validator

from cua.artifact import CapabilityArtifact
from cua.domain import ActionType, DomainModel, SurfaceSnapshot
from cua.domain.ids import new_run_id
from cua.policy import ActionGate, GateDecision, GateResult, ResolvedTarget, RiskTier
from cua.replay.binding import (
    BindingError,
    BoundArtifact,
    BoundKnownOutcome,
    BoundStep,
    bind_artifact,
    bind_inputs,
    normalize_base_url,
)
from cua.replay.clock import Clock
from cua.replay.conditions import BoundConditionLike, detect_known_outcome, evaluate_all
from cua.replay.resolver import (
    Ambiguous,
    NotFound,
    OutcomeDetected,
    Resolved,
    TargetResolver,
)
from cua.replay.result import (
    FailureCode,
    FailureDetail,
    OutcomeDetail,
    OutputValue,
    RunResult,
    SnapshotSummary,
    StepRecord,
    StepStatus,
    TerminalStatus,
)
from cua.replay.transforms import TransformError, apply_transform, validate_output
from cua.surface.contract import (
    ActResult,
    Surface,
    SurfaceAction,
    SurfaceDriverError,
    UnknownRefError,
)

_SURFACE_RUNTIME_ERRORS = (SurfaceDriverError, UnknownRefError)


class ReplayConfig(DomainModel):
    base_url: str  # origin only; a NAVIGATE destination is base_url + bound route
    resolve_timeout_s: float = 5.0
    condition_timeout_s: float = 5.0
    poll_interval_s: float = 0.1

    @field_validator("base_url")
    @classmethod
    def _origin_only(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("resolve_timeout_s", "condition_timeout_s")
    @classmethod
    def _non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("timeouts must be >= 0")
        return value

    @field_validator("poll_interval_s")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("poll interval must be > 0")
        return value


@dataclass(frozen=True)
class ReplayDeps:
    """Everything the engine needs. Deliberately no LLM, no model, no client (D15)."""

    surface: Surface
    action_gate: ActionGate
    clock: Clock


class _Stop(Exception):
    """Internal unwind carrying the terminal detail. Never leaves ``ReplayEngine.run``."""

    def __init__(
        self, *, failure: FailureDetail | None = None, outcome: OutcomeDetail | None = None
    ) -> None:
        super().__init__(failure.code if failure else outcome.code if outcome else "")
        self.failure = failure
        self.outcome = outcome


@dataclass
class _StepState:
    step: BoundStep
    status: StepStatus = StepStatus.NOT_REACHED
    observations: int = 0
    gate_decision: GateDecision | None = None
    effective_risk: RiskTier | None = None
    dispatched: bool = False

    def record(self) -> StepRecord:
        return StepRecord(
            step_id=self.step.step_id,
            action=self.step.action,
            declared_risk=self.step.declared_risk,
            status=self.status,
            observations=self.observations,
            gate_decision=self.gate_decision,
            effective_risk=self.effective_risk,
            dispatched=self.dispatched,
        )


@dataclass
class _RunState:
    run_id: str
    session_id: str
    steps: list[_StepState] = field(default_factory=list)
    outputs: dict[str, OutputValue] = field(default_factory=dict)
    current: _StepState | None = None  # whose observation counter is being advanced


class ReplayEngine:
    def __init__(self, deps: ReplayDeps, config: ReplayConfig) -> None:
        self._deps = deps
        self._config = config
        self._resolver = TargetResolver(
            deps.clock,
            timeout_s=config.resolve_timeout_s,
            poll_interval_s=config.poll_interval_s,
        )

    # --- entry point -------------------------------------------------------------------------

    def run(self, artifact: CapabilityArtifact, inputs: Mapping[str, str]) -> RunResult:
        state = _RunState(run_id=new_run_id(), session_id=self._deps.surface.session_id)
        try:
            view = self._bind(artifact, inputs)
            state.steps = [_StepState(step) for step in view.steps]
            for step_state in state.steps:
                self._execute(artifact, view, state, step_state)
            state.current = None
            self._wait_for(
                state, None, view.success_checkpoint, view.known_outcomes, "success_checkpoint"
            )
        except _Stop as stop:
            return self._terminal(artifact, state, stop)

        # Cross-object proof the result model cannot make on its own: every declared output was
        # produced (exactly once — enforced at record time), nothing undeclared exists.
        if set(state.outputs) != set(artifact.outputs):
            raise RuntimeError(
                "engine invariant violated: outputs "
                f"{sorted(state.outputs)} != declared {sorted(artifact.outputs)}"
            )
        return RunResult(
            run_id=state.run_id,
            artifact_id=artifact.artifact_id,
            capability_version=artifact.capability_version,
            session_id=state.session_id,
            status=TerminalStatus.SUCCESS,
            outputs=dict(state.outputs),
            steps=[s.record() for s in state.steps],
        )

    def _terminal(self, artifact: CapabilityArtifact, state: _RunState, stop: _Stop) -> RunResult:
        if state.current is not None:
            state.current.status = StepStatus.OUTCOME if stop.outcome else StepStatus.FAILED
        # Partial outputs are discarded: they are authoritative only on SUCCESS.
        return RunResult(
            run_id=state.run_id,
            artifact_id=artifact.artifact_id,
            capability_version=artifact.capability_version,
            session_id=state.session_id,
            status=TerminalStatus.BUSINESS_OUTCOME if stop.outcome else TerminalStatus.FAILURE,
            outcome=stop.outcome,
            failure=stop.failure,
            steps=[s.record() for s in state.steps],
        )

    # --- binding -----------------------------------------------------------------------------

    def _bind(self, artifact: CapabilityArtifact, inputs: Mapping[str, str]) -> BoundArtifact:
        try:
            bound = bind_inputs(artifact, inputs)
            return bind_artifact(artifact, bound, self._config.base_url)
        except BindingError as exc:
            raise _Stop(
                failure=FailureDetail(
                    code=FailureCode.INVALID_INPUT,
                    step_id=None,
                    message=str(exc),
                    expected=f"inputs {sorted(artifact.inputs)} bound to every reference",
                )
            ) from exc

    # --- one step ----------------------------------------------------------------------------

    def _execute(
        self,
        artifact: CapabilityArtifact,
        view: BoundArtifact,
        state: _RunState,
        step_state: _StepState,
    ) -> None:
        step = step_state.step
        state.current = step_state
        if step.action is ActionType.NAVIGATE:
            assert step.destination is not None
            action = SurfaceAction(action_type=ActionType.NAVIGATE, url=step.destination)
            act_result = self._dispatch(state, step_state, action, snapshot=None, target=None)
        else:
            assert step.target is not None
            resolution = self._resolver.resolve(
                step.target, lambda: self._observe(state, step.step_id), view.known_outcomes
            )
            if isinstance(resolution, OutcomeDetected):
                raise _Stop(outcome=_outcome_detail(resolution.outcome, step.step_id))
            if isinstance(resolution, Ambiguous):
                raise _Stop(
                    failure=FailureDetail(
                        code=FailureCode.AMBIGUOUS_TARGET,
                        step_id=step.step_id,
                        message=(
                            f"strategy {resolution.strategy_index} matched "
                            f"{len(resolution.candidates)} elements; refusing to choose"
                        ),
                        expected="exactly one element for the target",
                        observed=SnapshotSummary.of(resolution.snapshot),
                        candidates=[_as_target(e) for e in resolution.candidates],
                    )
                )
            if isinstance(resolution, NotFound):
                raise _Stop(
                    failure=FailureDetail(
                        code=FailureCode.TARGET_NOT_FOUND,
                        step_id=step.step_id,
                        message=(
                            f"no strategy matched within {self._config.resolve_timeout_s}s "
                            f"({resolution.observations} observations)"
                        ),
                        expected="exactly one element for the target",
                        observed=SnapshotSummary.of(resolution.snapshot),
                    )
                )
            assert isinstance(resolution, Resolved)
            element = resolution.element
            action = SurfaceAction(
                action_type=step.action,
                ref=element.ref,  # ephemeral: valid only with resolution.snapshot
                value=step.value if step.action in (ActionType.FILL, ActionType.SELECT) else None,
            )
            act_result = self._dispatch(
                state,
                step_state,
                action,
                snapshot=resolution.snapshot,  # the exact observation the ref came from
                target=_as_target(element),
            )

        if step.action is ActionType.READ:
            self._record_output(artifact, state, step, act_result)
        else:
            assert step.postcondition is not None
            self._wait_for(
                state, step.step_id, [step.postcondition], view.known_outcomes, "postcondition"
            )
        step_state.status = StepStatus.COMPLETED

    # --- surface boundary --------------------------------------------------------------------

    def _observe(self, state: _RunState, step_id: str | None) -> SurfaceSnapshot:
        try:
            snapshot = self._deps.surface.observe()
        except _SURFACE_RUNTIME_ERRORS as exc:
            raise _Stop(failure=_surface_failure(step_id, "observation", exc)) from exc
        if state.current is not None:
            state.current.observations += 1
        return snapshot

    def _dispatch(
        self,
        state: _RunState,
        step_state: _StepState,
        action: SurfaceAction,
        *,
        snapshot: SurfaceSnapshot | None,
        target: ResolvedTarget | None,
    ) -> ActResult:
        step = step_state.step
        gate = self._deps.action_gate
        try:
            if action.action_type is ActionType.NAVIGATE:
                gate_result, act_result = gate.dispatch(
                    self._deps.surface, action, declared_risk=step.declared_risk
                )
            else:
                gate_result, act_result = gate.dispatch(
                    self._deps.surface,
                    action,
                    snapshot=snapshot,
                    target=target,
                    declared_risk=step.declared_risk,
                )
        except _SURFACE_RUNTIME_ERRORS as exc:
            # act() runs only on ALLOW, so the driver failing means the gate had allowed it.
            step_state.gate_decision = GateDecision.ALLOW
            step_state.dispatched = True
            raise _Stop(
                failure=_surface_failure(
                    step.step_id, f"{action.action_type.value} to succeed", exc
                )
            ) from exc
        step_state.gate_decision = gate_result.decision
        step_state.effective_risk = gate_result.risk
        if gate_result.decision is GateDecision.DENY:
            raise _Stop(failure=_gate_failure(step, gate_result, FailureCode.POLICY_DENIED))
        if gate_result.decision is GateDecision.REQUIRE_INTERVENTION:
            raise _Stop(failure=_gate_failure(step, gate_result, FailureCode.INTERVENTION_REQUIRED))
        assert act_result is not None  # ALLOW: the gate dispatched exactly once
        step_state.dispatched = True
        return act_result

    # --- outputs and waits -------------------------------------------------------------------

    def _record_output(
        self,
        artifact: CapabilityArtifact,
        state: _RunState,
        step: BoundStep,
        act_result: ActResult,
    ) -> None:
        assert step.output is not None
        if act_result.value is None:
            raise _Stop(
                failure=FailureDetail(
                    code=FailureCode.SURFACE_ERROR,
                    step_id=step.step_id,
                    message="READ returned no value",
                    expected=f"text for output {step.output!r}",
                )
            )
        transform = artifact.outputs[step.output].type
        try:
            value = apply_transform(transform, act_result.value)
            validate_output(transform, value)
        except TransformError as exc:
            raise _Stop(
                failure=FailureDetail(
                    code=FailureCode.TRANSFORM_ERROR,
                    step_id=step.step_id,
                    message=f"{exc} (observed text {act_result.value[:80]!r})",
                    expected=f"{transform.value} for output {step.output!r}",
                )
            ) from exc
        if step.output in state.outputs:
            raise RuntimeError(f"engine invariant violated: output {step.output!r} produced twice")
        state.outputs[step.output] = value

    def _wait_for(
        self,
        state: _RunState,
        step_id: str | None,
        conditions: list[BoundConditionLike],
        known_outcomes: list[BoundKnownOutcome],
        label: str,
    ) -> None:
        clock = self._deps.clock
        deadline = clock.now() + self._config.condition_timeout_s
        while True:
            snapshot = self._observe(state, step_id)
            outcome = detect_known_outcome(known_outcomes, snapshot)
            if outcome is not None:
                raise _Stop(outcome=_outcome_detail(outcome, step_id))
            passed, results, failing = evaluate_all(conditions, snapshot)
            if passed:
                return
            if clock.now() >= deadline:
                assert failing is not None
                result = results[failing]
                raise _Stop(
                    failure=FailureDetail(
                        code=FailureCode.POSTCONDITION_FAILED,
                        step_id=step_id,
                        message=(
                            f"{label}[{failing}] did not hold within "
                            f"{self._config.condition_timeout_s}s; observed {result.observed}"
                        ),
                        expected=result.expected,
                        observed=SnapshotSummary.of(snapshot),
                    )
                )
            clock.sleep(self._config.poll_interval_s)


# --- helpers ---------------------------------------------------------------------------------


def _as_target(element) -> ResolvedTarget:
    return ResolvedTarget(
        role=element.role,
        accessible_name=element.accessible_name,
        context_hint=element.context_hint,
        value=element.value,
    )


def _outcome_detail(outcome: BoundKnownOutcome, step_id: str | None) -> OutcomeDetail:
    return OutcomeDetail(code=outcome.code, description=outcome.description, step_id=step_id)


def _surface_failure(step_id: str | None, expected: str, exc: Exception) -> FailureDetail:
    return FailureDetail(
        code=FailureCode.SURFACE_ERROR,
        step_id=step_id,
        message=f"{type(exc).__name__}: {exc}",
        expected=expected,
    )


def _gate_failure(step: BoundStep, gate_result: GateResult, code: FailureCode) -> FailureDetail:
    return FailureDetail(
        code=code,
        step_id=step.step_id,
        message=gate_result.explanation,
        expected=f"{step.action.value} to be allowed by policy",
        deny_reason=gate_result.deny_reason,
    )
