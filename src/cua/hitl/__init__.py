"""hitl/ — ControlOwner, intervention, hand-back.

Owns: ``ControlOwner`` state machine, ``InterventionRequest``, the intervention handler contract
and the terminal operator handler (ARCHITECTURE §3, §9, D17, D18).
Must never: convert DENY into approval; act on the surface.

Milestone 8: the four ownership transitions, ``InterventionRequest`` / ``HandBack``, the
``InterventionHandler`` protocol (receives only the request) and ``StdinInterventionHandler``.
The verification of what the human did is the replay engine's, using the artifact's own
postcondition; the human-action record is the ``INTERVENTION_VERIFIED`` evidence event.
"""

from cua.hitl.cli import StdinInterventionHandler
from cua.hitl.control import ControlOwner, ControlOwnerState, IllegalTransition
from cua.hitl.intervention import (
    HandBack,
    HandBackKind,
    InterventionHandler,
    InterventionRequest,
    SemanticTargetSummary,
    VerificationOutcome,
    new_request_id,
    snapshot_digest,
)

__all__ = [
    "ControlOwner",
    "ControlOwnerState",
    "HandBack",
    "HandBackKind",
    "IllegalTransition",
    "InterventionHandler",
    "InterventionRequest",
    "SemanticTargetSummary",
    "StdinInterventionHandler",
    "VerificationOutcome",
    "new_request_id",
    "snapshot_digest",
]
