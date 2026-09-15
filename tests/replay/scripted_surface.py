"""ScriptedSurface — an in-memory Surface replaying the Milestone 2 ARIA captures.

Fast, browser-free stand-in for the engine tests. It keeps the real contract's ref discipline
(``observe()`` mints the refs, any ``act()`` invalidates them, unknown refs raise) so an engine
that reused a ref across observations would fail here exactly as it would against Chromium.

Pages are the captured fixtures; the "Search" button behaves like the Legacy Bank: a known
member id navigates to that member's detail, anything else stays on the search screen with the
not-found alert carrying the typed id.
"""

from __future__ import annotations

from pathlib import Path

from cua.domain import ActionType, SurfaceElement, SurfaceSnapshot
from cua.surface import (
    ActResult,
    StaleObservationError,
    SurfaceAction,
    UnknownRefError,
    UnsupportedActionError,
)
from cua.surface.aria import read_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "surface" / "fixtures"

TITLES = {
    "/members/search": "Member Search - LegacyBank Operations Console",
    "/members/M1001": "Member M1001 - LegacyBank Operations Console",
    "/members/M1002": "Member M1002 - LegacyBank Operations Console",
}

MEMBERS = {"M1001": "B_detail_M1001", "M1002": "C_detail_M1002"}


def fixture_text(name: str) -> str:
    return (FIXTURES / f"{name}.aria.txt").read_text()


class ScriptedSurface:
    def __init__(
        self,
        *,
        base_url: str = "http://fake.test",
        ambiguous: bool = False,
        pages: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url
        self.ambiguous = ambiguous
        # path -> raw aria text; tests may override or add pages (e.g. one missing a control).
        self.pages: dict[str, str] = {
            "/members/search": fixture_text("A_search"),
            "/members/M1001": fixture_text(
                "G_detail_M1001_ambiguous" if ambiguous else "B_detail_M1001"
            ),
            "/members/M1002": fixture_text("C_detail_M1002"),
        }
        if pages:
            self.pages.update(pages)
        self.path = "about:blank"
        self.typed: dict[str, str] = {}  # accessible name -> typed value on the current page
        self.not_found_for: str | None = None
        self._elements: list[SurfaceElement] = []
        self._refs_valid = False
        self._step_index = 0
        self.dispatched_actions = 0
        self.raise_on_observe: Exception | None = None
        self.raise_on_act: Exception | None = None
        self.click_navigates = True  # False: the Search click leaves the page unchanged

    # --- contract ---------------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return "sess_scripted"

    def observe(self) -> SurfaceSnapshot:
        if self.raise_on_observe is not None:
            raise self.raise_on_observe
        text = self._page_text()
        elements, outline = read_snapshot(text)
        elements = [self._with_typed(e) for e in elements]
        self._elements = elements
        self._refs_valid = True
        self._step_index += 1
        return SurfaceSnapshot(
            url=f"{self.base_url}{self.path}",
            page_title=TITLES.get(self.path, self.path),
            step_index=self._step_index,
            elements=elements,
            visible_text_outline=outline,
        )

    def act(self, action: SurfaceAction) -> ActResult:
        if self.raise_on_act is not None:
            raise self.raise_on_act
        if action.action_type is ActionType.NAVIGATE:
            if not action.url:
                raise UnsupportedActionError("NAVIGATE requires url")
            self._dispatch()
            assert action.url.startswith(self.base_url), action.url
            self.path = action.url[len(self.base_url) :] or "/"
            self.typed = {}
            self.not_found_for = None
            return ActResult(action_type=action.action_type, url_after=self._url())

        if not self._refs_valid:
            raise StaleObservationError("refs are stale: observe() again")
        element = next((e for e in self._elements if e.ref == action.ref), None)
        if element is None:
            raise UnknownRefError(f"{action.ref} is not part of the current observation")
        self._dispatch()
        value: str | None = None
        if action.action_type is ActionType.READ:
            value = element.value
        elif action.action_type is ActionType.FILL:
            assert action.value is not None
            self.typed[element.accessible_name] = action.value
            value = action.value
        elif action.action_type is ActionType.CLICK:
            self._click(element)
        else:
            raise UnsupportedActionError(f"{action.action_type.value} not scripted")
        return ActResult(
            action_type=action.action_type, ref=action.ref, value=value, url_after=self._url()
        )

    def close(self) -> None:
        pass

    # --- scripting --------------------------------------------------------------------------

    def _dispatch(self) -> None:
        self.dispatched_actions += 1
        self._refs_valid = False

    def _url(self) -> str:
        return f"{self.base_url}{self.path}"

    def _click(self, element: SurfaceElement) -> None:
        if not self.click_navigates:
            return
        if element.role == "button" and element.accessible_name == "Search":
            member_id = self.typed.get("Member ID", "")
            if f"/members/{member_id}" in self.pages and member_id in MEMBERS:
                self.path = f"/members/{member_id}"
                self.typed = {}
                self.not_found_for = None
            else:
                self.not_found_for = member_id

    def _page_text(self) -> str:
        if self.path == "/members/search" and self.not_found_for is not None:
            return fixture_text("F_not_found").replace("M404", self.not_found_for)
        try:
            return self.pages[self.path]
        except KeyError:
            return '- generic [ref=e1]:\n  - heading "Not Found" [level=1] [ref=e2]\n'

    def _with_typed(self, element: SurfaceElement) -> SurfaceElement:
        if element.role == "textbox" and element.accessible_name in self.typed:
            return element.model_copy(update={"value": self.typed[element.accessible_name]})
        return element
