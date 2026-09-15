"""domain/ — shared vocabulary used across cua modules. Pure Pydantic; imports no driver, no LLM.

Only the models whose shape is already fixed by the frozen documents AND needed by the next
milestone live here. Everything else (artifact schema, run results, evidence envelope, gate
decisions, control ownership, failure codes, ...) is defined in its owning module at its own
implementation milestone — never frozen ahead of time.
"""

from cua.domain.actions import ActionType
from cua.domain.base import DomainModel
from cua.domain.surface import SurfaceElement, SurfaceSnapshot

__all__ = ["ActionType", "DomainModel", "SurfaceElement", "SurfaceSnapshot"]
