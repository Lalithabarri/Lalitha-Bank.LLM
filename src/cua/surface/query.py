"""Semantic queries over a SurfaceSnapshot. Pure functions; no driver involved.

``find`` returns every match. It never picks a first match: ambiguity is information the caller
must see (ARCHITECTURE §7 — ``AMBIGUOUS_TARGET`` fails closed).
"""

from cua.domain import SurfaceElement, SurfaceSnapshot


def find(
    snapshot: SurfaceSnapshot,
    *,
    role: str | None = None,
    name: str | None = None,
    context_hint: str | None = None,
) -> list[SurfaceElement]:
    return [
        element
        for element in snapshot.elements
        if (role is None or element.role == role)
        and (name is None or element.accessible_name == name)
        and (context_hint is None or element.context_hint == context_hint)
    ]
