"""evidence/ — EvidenceWriter, Redactor, screenshots.

Owns: the only structured-write path to disk (ARCHITECTURE §3, §10, D19).
Must never: write unredacted structured data. Redaction happens before disk, never after.

Implemented at ARCHITECTURE §15 step 9: the typed, versioned ``EvidenceEvent`` envelope and its
1.0 vocabulary; the deterministic ``Redactor``; the append-only ``JsonlEvidenceWriter`` behind
``EvidenceStore``'s layout; and the ``EvidenceRecorder`` that the Surface (``DispatchListener``)
and the ActionGate (``GateObserver``) notify. Screenshots are deferred to the HITL milestone
(pixel redaction cannot yet be guaranteed); the failure event carries a redacted observation
summary instead.

The evidence layer is observational. It never authorizes, dispatches, retries, or influences a
replay decision; if it cannot record, it raises ``EvidenceError`` — its own failure domain — and
the caller stops without repeating anything.
"""

from cua.evidence.events import (
    EVIDENCE_SCHEMA_VERSION,
    PAYLOAD_MODELS,
    ActionCompletedPayload,
    ActionDispatchedPayload,
    ActionFailedPayload,
    BusinessOutcomePayload,
    EventType,
    EvidenceError,
    EvidenceEvent,
    FailureSummary,
    GateDecisionPayload,
    ObservationSummary,
    OutcomeSummary,
    Payload,
    RunKind,
    RunStartedPayload,
    RunTerminalPayload,
    Severity,
    StepSummary,
    TargetSummary,
)
from cua.evidence.recorder import EvidenceRecorder, SinkOpener
from cua.evidence.redaction import (
    DEFAULT_SENSITIVE_KEYS,
    MIN_SUBSTRING_LEN,
    REDACTED,
    SENSITIVE_KEY_SUFFIXES,
    Redactor,
)
from cua.evidence.writer import (
    EVENTS_FILE,
    EvidenceSink,
    EvidenceStore,
    JsonlEvidenceWriter,
    event_types,
    redacted_form,
)

__all__ = [
    "DEFAULT_SENSITIVE_KEYS",
    "EVENTS_FILE",
    "EVIDENCE_SCHEMA_VERSION",
    "MIN_SUBSTRING_LEN",
    "PAYLOAD_MODELS",
    "REDACTED",
    "SENSITIVE_KEY_SUFFIXES",
    "ActionCompletedPayload",
    "ActionDispatchedPayload",
    "ActionFailedPayload",
    "BusinessOutcomePayload",
    "EventType",
    "EvidenceError",
    "EvidenceEvent",
    "EvidenceRecorder",
    "EvidenceSink",
    "EvidenceStore",
    "FailureSummary",
    "GateDecisionPayload",
    "JsonlEvidenceWriter",
    "ObservationSummary",
    "OutcomeSummary",
    "Payload",
    "Redactor",
    "RunKind",
    "RunStartedPayload",
    "RunTerminalPayload",
    "Severity",
    "SinkOpener",
    "StepSummary",
    "TargetSummary",
    "event_types",
    "redacted_form",
]
