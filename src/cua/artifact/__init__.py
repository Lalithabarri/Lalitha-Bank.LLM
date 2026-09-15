"""artifact/ — Schema, compiler, store, invariants I1–I6.

Owns: ``CapabilityArtifact``, ``ArtifactStore``, ``ArtifactCompiler`` (ARCHITECTURE §3, §6).
Must never: hold secrets or approvals.

Implemented so far: the schema with its structural invariants and the store (steps 5–6).
The compiler and trace-based invariant checks arrive at ARCHITECTURE §15 step 13.
"""

from cua.artifact.schema import (
    PERSISTED_ACTION_TYPES,
    PLACEHOLDER,
    CapabilityArtifact,
    Condition,
    ConditionType,
    ElementPresent,
    InputRef,
    InputSpec,
    KnownOutcome,
    LiteralValue,
    OutputSpec,
    Provenance,
    ProvenanceSource,
    RouteMatches,
    Step,
    TargetDescriptor,
    TargetStrategy,
    TextPresent,
    TransformType,
    ValueBinding,
    ValueEquals,
    ValueKind,
)
from cua.artifact.store import ArtifactConflict, ArtifactNotFound, ArtifactStore

__all__ = [
    "PERSISTED_ACTION_TYPES",
    "PLACEHOLDER",
    "ArtifactConflict",
    "ArtifactNotFound",
    "ArtifactStore",
    "CapabilityArtifact",
    "Condition",
    "ConditionType",
    "ElementPresent",
    "InputRef",
    "InputSpec",
    "KnownOutcome",
    "LiteralValue",
    "OutputSpec",
    "Provenance",
    "ProvenanceSource",
    "RouteMatches",
    "Step",
    "TargetDescriptor",
    "TargetStrategy",
    "TextPresent",
    "TransformType",
    "ValueBinding",
    "ValueEquals",
    "ValueKind",
]
