"""Intervention types (ARCHITECTURE §9, D17, D18; Milestone 8).

``InterventionRequest`` is what the engine hands to an ``InterventionHandler`` when the gate
answers ``REQUIRE_INTERVENTION``: safe structured facts only — identity of the run, the step, the
semantic target, what is being asked and how completion will be verified. No transient ref, no
driver object, no selector, no credential, no model text (structural: ``extra="forbid"`` and no
such field exists).

The handler contract is ``intervene(request) -> HandBack``. The handler receives the request and
nothing else — not the surface, the gate, the page or the engine — so it physically cannot execute
the UI operation or reopen a browser. Its answer is a *hand-back signal*, never a claim about the
transaction: both ``DONE`` and ``ABORT`` lead to a fresh observation and deterministic
verification of the real browser state; only the verifier decides whether the irreversible
operation committed (amendment A2).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Protocol
from urllib.parse import urlsplit

from cua.domain import DomainModel, SurfaceSnapshot


class HandBackKind(StrEnum):
    DONE = "DONE"  # "please re-observe and verify now"
    ABORT = "ABORT"  # "verify, but do not continue automation afterwards"


class VerificationOutcome(StrEnum):
    VERIFIED_COMPLETED = "VERIFIED_COMPLETED"  # the completion signal was observed
    UNKNOWN_COMMIT_STATE = "UNKNOWN_COMMIT_STATE"  # it was not; commit state is unknown


class SemanticTargetSummary(DomainModel):
    role: str
    accessible_name: str | None = None
    context_hint: str | None = None


class InterventionRequest(DomainModel):
    request_id: str
    run_id: str
    session_id: str
    artifact_id: str
    step_id: str
    step_index: int
    reason: str  # e.g. "IRREVERSIBLE"
    risk: str  # RiskTier value
    requested_action_summary: str  # e.g. "CLICK button 'Confirm transfer'"
    semantic_target_summary: SemanticTargetSummary | None = None
    verification_requirement: str  # the rendered postcondition the verifier will evaluate
    created_at: datetime
    owner_before: str
    owner_after: str


class HandBack(DomainModel):
    kind: HandBackKind
    note: str = ""


class InterventionHandler(Protocol):
    def intervene(self, request: InterventionRequest) -> HandBack:
        """Surface the request to a human and block until they hand control back."""
        ...


def new_request_id() -> str:
    return f"ivr_{uuid.uuid4().hex[:12]}"


def snapshot_digest(snapshot: SurfaceSnapshot) -> str:
    """Fingerprint of the observable page state (path, title, semantic elements). Refs excluded;
    the same canonical form discovery uses for its no-progress rule."""
    canonical = [
        urlsplit(snapshot.url).path,
        snapshot.page_title,
        [
            (e.role, e.accessible_name, e.context_hint, e.value, e.enabled)
            for e in snapshot.elements
        ],
    ]
    return hashlib.sha256(json.dumps(canonical, ensure_ascii=False).encode("utf-8")).hexdigest()[
        :16
    ]
