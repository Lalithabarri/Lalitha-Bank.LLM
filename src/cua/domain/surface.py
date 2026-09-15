"""What a Surface reports when observed (ARCHITECTURE §5, D07).

Refs (``e1``, ``e2``, ...) are transient: they identify an element within ONE observation only
and are never persisted into a trace, artifact, or evidence record (invariant I1).
"""

from cua.domain.base import DomainModel


class SurfaceElement(DomainModel):
    ref: str
    role: str
    accessible_name: str
    value: str | None = None
    enabled: bool = True
    tag_hint: str | None = None
    context_hint: str | None = None


class SurfaceSnapshot(DomainModel):
    url: str
    page_title: str
    step_index: int
    elements: list[SurfaceElement]
    visible_text_outline: str
