"""replay/ — Deterministic execution, outcomes, retries.

Owns: ``TargetResolver``, condition evaluator, ``RunResult``, ``ReplayEngine``
(ARCHITECTURE §3, §7).
Must never: import ``LLMClient`` — ``ReplayDeps`` deliberately has no LLM (D15).

Implemented at ARCHITECTURE §15 step 7: binding, closed transforms, matching, conditions,
resolver, result vocabulary, engine. Evidence emission (step 9) and HITL suspension (step 16)
are not here yet.
"""

from cua.replay.binding import (
    BindingError,
    BoundArtifact,
    BoundCondition,
    BoundDescriptor,
    BoundKnownOutcome,
    BoundStep,
    BoundStrategy,
    bind_artifact,
    bind_inputs,
    bind_route,
    bind_text,
    bind_value,
    build_destination,
    normalize_base_url,
)
from cua.replay.clock import Clock, MonotonicClock
from cua.replay.conditions import (
    ConditionResult,
    detect_known_outcome,
    evaluate,
    evaluate_all,
    observed_path,
)
from cua.replay.engine import ReplayConfig, ReplayDeps, ReplayEngine
from cua.replay.matching import OnSnapshot, match_strategy, resolve_on_snapshot
from cua.replay.resolver import (
    Ambiguous,
    NotFound,
    OutcomeDetected,
    Resolution,
    Resolved,
    TargetResolver,
)
from cua.replay.result import (
    CompletedBy,
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
from cua.replay.transforms import (
    PYTHON_TYPES,
    TransformError,
    apply_transform,
    validate_output,
)

__all__ = [
    "CompletedBy",
    "PYTHON_TYPES",
    "Ambiguous",
    "BindingError",
    "BoundArtifact",
    "BoundCondition",
    "BoundDescriptor",
    "BoundKnownOutcome",
    "BoundStep",
    "BoundStrategy",
    "Clock",
    "ConditionResult",
    "FailureCode",
    "FailureDetail",
    "MonotonicClock",
    "NotFound",
    "OnSnapshot",
    "OutcomeDetail",
    "OutcomeDetected",
    "OutputValue",
    "ReplayConfig",
    "ReplayDeps",
    "ReplayEngine",
    "Resolution",
    "Resolved",
    "RunResult",
    "SnapshotSummary",
    "StepRecord",
    "StepStatus",
    "TargetResolver",
    "TerminalStatus",
    "TransformError",
    "apply_transform",
    "bind_artifact",
    "bind_inputs",
    "bind_route",
    "bind_text",
    "bind_value",
    "build_destination",
    "detect_known_outcome",
    "evaluate",
    "evaluate_all",
    "match_strategy",
    "normalize_base_url",
    "observed_path",
    "resolve_on_snapshot",
    "validate_output",
]
