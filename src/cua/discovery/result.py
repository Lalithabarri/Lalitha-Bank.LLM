"""The caller-facing result of a discovery run (ARCHITECTURE §5).

Exactly six stop reasons: ``GOAL_REACHED | MAX_STEPS | TIMEOUT | DEAD_END | BLOCKED_BY_POLICY |
MODEL_ERROR``. ``StopDetail.code`` names the sub-cause. Outputs are authoritative only on
``GOAL_REACHED`` and only when the deterministic verifier said so — the model's FINISH is a
proposal. A run whose terminal evidence could not be written is not a proven run: it is returned
as ``DEAD_END / EVIDENCE_ERROR`` with its outputs discarded, exactly like replay.

Discovery stop reasons and replay failure codes are different vocabularies by design
(PROJECT_STATUS "Decision corrections"); nothing here is a ``FailureCode``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import model_validator

from cua.discovery.goal import GoalVerdict
from cua.discovery.trace import NormalizedTrace
from cua.domain import DomainModel

OutputValue = Decimal | int | bool | str


class StopReason(StrEnum):
    GOAL_REACHED = "GOAL_REACHED"
    MAX_STEPS = "MAX_STEPS"
    TIMEOUT = "TIMEOUT"
    DEAD_END = "DEAD_END"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    MODEL_ERROR = "MODEL_ERROR"


class StopCode(StrEnum):
    GOAL_VERIFIED = "GOAL_VERIFIED"
    MAX_STEPS = "MAX_STEPS"
    TIMEOUT = "TIMEOUT"
    NO_PROGRESS = "NO_PROGRESS"  # DEAD_END: the page did not change after repeated actions
    REPORTED_BLOCKED = "REPORTED_BLOCKED"  # DEAD_END: the model reported it cannot proceed
    AMBIGUOUS_TARGET = "AMBIGUOUS_TARGET"  # DEAD_END: equivalent candidates, refused twice
    SURFACE_ERROR = "SURFACE_ERROR"  # DEAD_END: the driver failed
    EVIDENCE_ERROR = "EVIDENCE_ERROR"  # DEAD_END: the run's proof could not be written
    POLICY_DENIED = "POLICY_DENIED"  # BLOCKED_BY_POLICY
    INTERVENTION_REQUIRED = "INTERVENTION_REQUIRED"  # BLOCKED_BY_POLICY (HITL suspension: step 16)
    INVALID_DECISION = "INVALID_DECISION"  # MODEL_ERROR: invalid after the corrective retry
    PROVIDER_ERROR = "PROVIDER_ERROR"  # MODEL_ERROR


class StopDetail(DomainModel):
    code: StopCode
    message: str
    gate_decision: str | None = None
    deny_reason: str | None = None
    validation_codes: list[str] = []
    provider_error_kind: str | None = None


class DiscoveryResult(DomainModel):
    run_id: str
    session_id: str
    goal_name: str
    stop_reason: StopReason
    detail: StopDetail
    outputs: dict[str, OutputValue] = {}
    trace: NormalizedTrace
    verdict: GoalVerdict | None = None
    model_calls: int
    corrective_retries: int
    provider: str
    model_id: str

    @model_validator(mode="after")
    def _terminal_invariants(self) -> DiscoveryResult:
        if self.stop_reason is StopReason.GOAL_REACHED:
            if self.verdict is None or not self.verdict.satisfied:
                raise ValueError("GOAL_REACHED requires a satisfied verifier verdict")
            if not self.outputs:
                raise ValueError("GOAL_REACHED requires the verified outputs")
        elif self.outputs:
            raise ValueError("outputs are authoritative only on GOAL_REACHED")
        return self

    @property
    def steps_dispatched(self) -> int:
        return len(self.trace.steps)
