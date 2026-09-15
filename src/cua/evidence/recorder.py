"""EvidenceRecorder — turns boundary notifications into a redacted, ordered chronology.

One recorder per composition; one run at a time, bracketed by ``begin_run`` … ``end_run``. It is
the ``DispatchListener`` the Surface notifies and the ``GateObserver`` the ActionGate notifies, and
it is the only thing that knows the run id, the artifact, the current step and the ``seq``
counter. It stamps that context onto every event and hands the event to an ``EvidenceSink``
opened for the run (a ``JsonlEvidenceWriter`` in production).

The recorder is observational. It never authorizes, dispatches, retries, resolves a target, or
touches ``ControlOwner``; nothing it returns can be branched on by the engine except two facts the
engine needs to stay safe: ``dispatch_count`` (how many ``ACTION_DISPATCHED`` events were actually
persisted — it increments only after the write succeeded) and ``evidence_failed``.

Failure discipline (one failure per run, no recursion): the first ``EvidenceError`` from the sink
is remembered, re-raised with the event type and whether a driver action had already been
attempted, and from then on every further write for that run is refused with an ``EvidenceError``
(so a Surface still wired to this recorder cannot dispatch unrecorded) while nothing more is
written — not even a ``RUN_FAILED`` describing the evidence failure. The in-memory ``RunResult``
carries that story instead.

Runtime inputs are used for exactly one thing: registering their values with the ``Redactor`` as
``input:<name>`` placeholders. Only their names are persisted.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from cua.domain import DomainModel
from cua.domain.ids import new_event_id
from cua.evidence.events import (
    ActionCompletedPayload,
    ActionDispatchedPayload,
    ActionFailedPayload,
    BusinessOutcomePayload,
    EventType,
    EvidenceError,
    EvidenceEvent,
    GateDecisionPayload,
    RunKind,
    RunStartedPayload,
    RunTerminalPayload,
    Severity,
    TargetSummary,
)
from cua.evidence.redaction import Redactor
from cua.evidence.writer import EvidenceSink
from cua.policy import GateDecision, GateRequest, GateResult
from cua.surface.contract import ActResult, DispatchRecord, SurfaceDriverError

SinkOpener = Callable[[RunKind, str, Redactor], EvidenceSink]
"""``(run_kind, run_id, redactor) -> sink``; ``EvidenceStore.open_run`` in production."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _ActiveRun:
    run_id: str
    run_kind: RunKind
    session_id: str
    artifact_id: str
    sink: EvidenceSink
    seq: int = 0
    step_id: str | None = None
    step_index: int | None = None
    dispatch_count: int = 0
    failure: EvidenceError | None = None
    terminal_written: bool = False


class EvidenceRecorder:
    def __init__(
        self,
        open_sink: SinkOpener,
        *,
        redactor: Redactor | None = None,
        wall_clock: Callable[[], datetime] = _utc_now,
        event_ids: Callable[[], str] = new_event_id,
    ) -> None:
        self._open_sink = open_sink
        self._redactor = redactor or Redactor()
        self._wall_clock = wall_clock
        self._event_ids = event_ids
        self._run: _ActiveRun | None = None

    # --- facts the engine may read ---------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._run is not None

    @property
    def run_id(self) -> str | None:
        return self._run.run_id if self._run else None

    @property
    def dispatch_count(self) -> int:
        """``ACTION_DISPATCHED`` events persisted for the active run."""
        return self._run.dispatch_count if self._run else 0

    @property
    def evidence_failed(self) -> bool:
        return self._run is not None and self._run.failure is not None

    @property
    def failure(self) -> EvidenceError | None:
        return self._run.failure if self._run else None

    # --- run lifecycle ---------------------------------------------------------------------------

    def begin_run(
        self,
        *,
        run_id: str,
        run_kind: RunKind,
        session_id: str,
        artifact_id: str,
        inputs: Mapping[str, str],
        payload: RunStartedPayload,
    ) -> None:
        """Open the run's sink and write ``RUN_STARTED``. Raises ``EvidenceError`` on failure."""
        if self._run is not None:
            raise EvidenceError(
                f"run {self._run.run_id} is still being recorded; cannot begin {run_id}",
                event_type=EventType.RUN_STARTED,
            )
        redactor = self._redactor.with_values(
            {f"input:{name}": value for name, value in inputs.items()}
        )
        try:
            sink = self._open_sink(run_kind, run_id, redactor)
        except EvidenceError as exc:
            if exc.event_type is None:
                exc.event_type = EventType.RUN_STARTED
            raise
        self._run = _ActiveRun(
            run_id=run_id,
            run_kind=run_kind,
            session_id=session_id,
            artifact_id=artifact_id,
            sink=sink,
        )
        self._emit(EventType.RUN_STARTED, Severity.INFO, payload)

    def begin_step(self, step_id: str, step_index: int) -> None:
        run = self._require_run_for_context()
        run.step_id = step_id
        run.step_index = step_index

    def clear_step(self) -> None:
        run = self._require_run_for_context()
        run.step_id = None
        run.step_index = None

    def business_outcome(self, code: str, description: str, step_id: str | None) -> None:
        run = self._require_run_for_context()
        run.step_id = step_id
        self._emit(
            EventType.BUSINESS_OUTCOME,
            Severity.INFO,
            BusinessOutcomePayload(code=code, description=description),
        )

    def run_completed(self, payload: RunTerminalPayload) -> None:
        """Terminal event for SUCCESS and BUSINESS_OUTCOME; closes the run's sink."""
        self._terminal(EventType.RUN_COMPLETED, Severity.INFO, payload)

    def run_failed(self, payload: RunTerminalPayload) -> None:
        """Terminal event for FAILURE; closes the run's sink."""
        self._terminal(EventType.RUN_FAILED, Severity.ERROR, payload)

    def end_run(self) -> None:
        """Release the run. Idempotent; never raises ``EvidenceError``.

        A healthy run closed its sink when the terminal event was written. Reaching here with the
        sink still open means the run's evidence already failed, or a non-evidence exception is
        propagating — in both cases the outcome is decided and every persisted event was already
        fsynced, so a failing close adds nothing and must not mask the original error.
        """
        run = self._run
        if run is None:
            return
        self._run = None
        if not run.terminal_written:
            try:
                run.sink.close()
            except EvidenceError:
                pass

    # --- GateObserver ----------------------------------------------------------------------------

    def on_decision(self, request: GateRequest, result: GateResult) -> None:
        target = request.target
        payload = GateDecisionPayload(
            action_type=request.action_type.value,
            origin=request.origin,
            route=request.route,
            decision=result.decision.value,
            risk=result.risk.value,
            declared_risk=request.declared_risk.value if request.declared_risk else None,
            deny_reason=result.deny_reason.value if result.deny_reason else None,
            explanation=result.explanation,
            target=(
                TargetSummary(
                    role=target.role,
                    accessible_name=target.accessible_name,
                    context_hint=target.context_hint,
                    value=target.value,
                )
                if target
                else None
            ),
        )
        severity = Severity.INFO if result.decision is GateDecision.ALLOW else Severity.WARNING
        self._emit(EventType.GATE_DECISION, severity, payload)

    # --- DispatchListener ------------------------------------------------------------------------

    def on_dispatched(self, record: DispatchRecord) -> None:
        payload = ActionDispatchedPayload(
            dispatch_seq=record.dispatch_seq,
            observation_index=record.observation_index,
            action_type=record.action_type.value,
            url_before=record.url_before,
            destination=record.destination,
            target_role=record.target_role,
            target_name=record.target_name,
            value=record.value,
        )
        run = self._emit(EventType.ACTION_DISPATCHED, Severity.INFO, payload)
        run.dispatch_count += 1  # only once the write succeeded

    def on_completed(self, record: DispatchRecord, result: ActResult) -> None:
        payload = ActionCompletedPayload(
            dispatch_seq=record.dispatch_seq,
            action_type=record.action_type.value,
            url_after=result.url_after,
            value=result.value,
        )
        self._emit(EventType.ACTION_COMPLETED, Severity.INFO, payload, after_dispatch=True)

    def on_failed(self, record: DispatchRecord, error: SurfaceDriverError) -> None:
        payload = ActionFailedPayload(
            dispatch_seq=record.dispatch_seq,
            action_type=record.action_type.value,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        self._emit(EventType.ACTION_FAILED, Severity.ERROR, payload, after_dispatch=True)

    # --- internals -----------------------------------------------------------------------------

    def _require_run_for_context(self) -> _ActiveRun:
        if self._run is None:
            raise RuntimeError("no run is being recorded (begin_run was not called)")
        return self._run

    def _terminal(
        self, event_type: EventType, severity: Severity, payload: RunTerminalPayload
    ) -> None:
        run = self._require_run_for_context()
        run.step_id = None
        run.step_index = None
        if payload.kind != event_type:
            raise ValueError(f"terminal payload kind {payload.kind} does not match {event_type}")
        self._emit(event_type, severity, payload)
        run.terminal_written = True
        try:
            run.sink.close()
        except EvidenceError as exc:
            exc.event_type = event_type
            run.failure = exc
            raise

    def _emit(
        self,
        event_type: EventType,
        severity: Severity,
        payload: DomainModel,
        *,
        after_dispatch: bool = False,
    ) -> _ActiveRun:
        run = self._run
        if run is None:
            raise EvidenceError(
                f"{event_type.value} outside a recorded run; refusing to proceed unrecorded",
                event_type=event_type,
                after_dispatch=after_dispatch,
            )
        if run.failure is not None:
            raise EvidenceError(
                f"evidence for run {run.run_id} already failed at "
                f"{run.failure.event_type.value if run.failure.event_type else '?'}; "
                f"refusing {event_type.value}",
                event_type=event_type,
                after_dispatch=after_dispatch,
            )
        if run.terminal_written:
            raise EvidenceError(
                f"run {run.run_id} evidence is complete; refusing {event_type.value}",
                event_type=event_type,
                after_dispatch=after_dispatch,
            )
        run.seq += 1
        event = EvidenceEvent(
            event_id=self._event_ids(),
            seq=run.seq,
            ts=self._wall_clock(),
            run_id=run.run_id,
            run_kind=run.run_kind,
            session_id=run.session_id,
            event_type=event_type,
            severity=severity,
            artifact_id=run.artifact_id,
            step_id=run.step_id,
            step_index=run.step_index,
            payload=payload,
        )
        try:
            run.sink.write(event)
        except EvidenceError as exc:
            failure = EvidenceError(
                f"could not record {event_type.value} (seq {run.seq}) for run {run.run_id}: {exc}",
                event_type=event_type,
                after_dispatch=after_dispatch,
            )
            run.failure = failure
            raise failure from exc
        return run
