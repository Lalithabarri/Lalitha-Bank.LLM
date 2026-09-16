"""Wraps any Surface and records every ``act()`` it receives. Driver-neutral.

Used by the engine tests to prove "zero dispatches" claims independently of the surface's own
counters: the recorded list is the ground truth for what the engine (via the gate) asked for.
"""

from cua.domain import SurfaceSnapshot
from cua.surface import ActResult, Surface, SurfaceAction


class RecordingSurface:
    def __init__(self, inner: Surface) -> None:
        self._inner = inner
        self.acts: list[SurfaceAction] = []
        self.observes = 0
        self.screenshots = 0

    @property
    def session_id(self) -> str:
        return self._inner.session_id

    def observe(self) -> SurfaceSnapshot:
        self.observes += 1
        return self._inner.observe()

    def act(self, action: SurfaceAction) -> ActResult:
        self.acts.append(action)
        return self._inner.act(action)

    def close(self) -> None:
        self._inner.close()

    def capture_screenshot(self) -> bytes:
        """Forwarded only if the inner surface has the capability (never impersonated)."""
        self.screenshots += 1
        return self._inner.capture_screenshot()  # type: ignore[attr-defined]

    @property
    def act_types(self) -> list[str]:
        return [a.action_type.value for a in self.acts]
