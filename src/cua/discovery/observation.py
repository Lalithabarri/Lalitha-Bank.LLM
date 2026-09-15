"""From the RAW ``SurfaceSnapshot`` to what discovery derives from it — nothing else reads elements.

* ``to_observation`` — the **model-safe** ``Observation``: bounded, every known bound input value
  templated to ``<input:<name>>`` (A1), refs kept verbatim. The raw snapshot is never mutated.
* ``digest`` — a stable fingerprint of the raw page for the no-progress rule.
* ``derive_semantic_target`` — the durable descriptor discovery will persist for an element; for
  content roles the accessible name (== the value) is dropped.
* ``identity_matches`` — how many elements of the raw snapshot that descriptor resolves to, with
  the same exact ``find`` replay uses. The ambiguity guard requires exactly one.
* ``input_evidence`` — how the raw page evidences each bound input (whole URL path segment or a
  whole token of a heading line), recorded on READ steps for the goal verifier.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from cua.discovery.goal import InputEvidence
from cua.discovery.templating import InputTemplater, PlaceholderStyle
from cua.discovery.trace import SemanticTarget
from cua.domain import SurfaceElement, SurfaceSnapshot
from cua.llm import Observation, ObservedElement
from cua.surface import CONTENT_ROLES, find

MAX_ELEMENTS = 150


def to_observation(snapshot: SurfaceSnapshot, templater: InputTemplater | None) -> Observation:
    """The model-safe view. ``templater`` is ``None`` only on a labelled DIAGNOSTIC run."""
    elements = snapshot.elements[:MAX_ELEMENTS]
    if templater is None:
        return Observation(
            observation_index=snapshot.step_index,
            url=snapshot.url,
            page_title=snapshot.page_title,
            elements=[ObservedElement(**e.model_dump()) for e in elements],
            visible_text_outline=snapshot.visible_text_outline,
            truncated=len(snapshot.elements) > MAX_ELEMENTS,
        )
    return Observation(
        observation_index=snapshot.step_index,
        url=templater.url(snapshot.url),
        page_title=templater.text(snapshot.page_title) or "",
        elements=[
            ObservedElement(
                ref=e.ref,
                role=e.role,
                accessible_name=templater.text(e.accessible_name) or "",
                value=templater.text(e.value),
                enabled=e.enabled,
                tag_hint=e.tag_hint,
                context_hint=templater.text(e.context_hint),
            )
            for e in elements
        ],
        visible_text_outline=templater.text(snapshot.visible_text_outline) or "",
        truncated=len(snapshot.elements) > MAX_ELEMENTS,
    )


def digest(snapshot: SurfaceSnapshot) -> str:
    """Fingerprint of the observable page state (path, title, semantic elements). Refs excluded."""
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


def derive_semantic_target(element: SurfaceElement) -> SemanticTarget:
    """The descriptor discovery persists for ``element``: name omitted for content roles."""
    return SemanticTarget(
        role=element.role,
        accessible_name=None if element.role in CONTENT_ROLES else element.accessible_name,
        context_hint=element.context_hint,
    )


def identity_matches(snapshot: SurfaceSnapshot, target: SemanticTarget) -> int:
    """How many elements the persisted descriptor resolves to on this exact observation."""
    return len(
        find(
            snapshot,
            role=target.role,
            name=target.accessible_name,
            context_hint=target.context_hint,
        )
    )


_HEADING_LINE = re.compile(r"^h(?:\d+|\?): ")  # the outline renders headings as ``h1: text``


def input_evidence(snapshot: SurfaceSnapshot, bound_inputs: Mapping[str, str]) -> dict[str, str]:
    """For each bound input: ROUTE when its value is a whole path segment, else HEADING when it
    is a whole token of a heading line of the outline; absent otherwise."""
    evidence: dict[str, str] = {}
    segments = urlsplit(snapshot.url).path.split("/")
    headings = [
        line for line in snapshot.visible_text_outline.splitlines() if _HEADING_LINE.match(line)
    ]
    for name, value in bound_inputs.items():
        if value in segments:
            evidence[name] = InputEvidence.ROUTE.value
            continue
        templater = InputTemplater({name: value}, PlaceholderStyle.MODEL)
        if any(templater.contains_bound_value(line) for line in headings):
            evidence[name] = InputEvidence.HEADING.value
    return evidence
