"""A minimal Surface stand-in for policy tests. Counts act() calls; never touches a driver."""

from cua.domain import SurfaceSnapshot
from cua.surface import ActResult, SurfaceAction


class FakeSurface:
    def __init__(self, snapshot: SurfaceSnapshot | None = None) -> None:
        self._snapshot = snapshot or SurfaceSnapshot(
            url="http://127.0.0.1:8000/members/search",
            page_title="Member Search",
            step_index=0,
            elements=[],
            visible_text_outline="",
        )
        self.act_calls: list[SurfaceAction] = []

    @property
    def session_id(self) -> str:
        return "sess_fake"

    def observe(self) -> SurfaceSnapshot:
        return self._snapshot

    def act(self, action: SurfaceAction) -> ActResult:
        self.act_calls.append(action)
        return ActResult(
            action_type=action.action_type,
            ref=action.ref,
            value=action.value,
            url_after=self._snapshot.url,
        )

    def close(self) -> None:
        pass
