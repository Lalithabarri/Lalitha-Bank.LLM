"""ScriptedSurface — an in-memory Surface replaying the Milestone 2 ARIA captures.

Fast, browser-free stand-in for the engine tests. It keeps the real contract's ref discipline
(``observe()`` mints the refs, any ``act()`` invalidates them, unknown refs raise) so an engine
that reused a ref across observations would fail here exactly as it would against Chromium.

Pages are the captured fixtures; the "Search" button behaves like the Legacy Bank: a known
member id navigates to that member's detail, anything else stays on the search screen with the
not-found alert carrying the typed id.

Dispatch evidence mirrors ``PlaywrightSurface`` exactly: ``listener.on_dispatched`` before the
"driver" step (a raising listener leaves ``dispatched_actions`` and the refs untouched), then the
counter and ref invalidation, then the operation, then ``on_completed`` / ``on_failed``.
``raise_on_act`` set to a ``SurfaceDriverError`` fails *as the driver operation* (after
``on_dispatched``, like a dead port); any other exception is raised before anything happens
(like an ``UnknownRefError`` from ref resolution, or a programming error).
"""

from __future__ import annotations

from pathlib import Path

from cua.domain import ActionType, SurfaceElement, SurfaceSnapshot
from cua.surface import (
    ActResult,
    DispatchListener,
    DispatchRecord,
    NullDispatchListener,
    StaleObservationError,
    SurfaceAction,
    SurfaceDriverError,
    UnknownRefError,
    UnsupportedActionError,
)
from cua.surface.aria import read_snapshot

FIXTURES = Path(__file__).resolve().parents[1] / "surface" / "fixtures"

TITLES = {
    "/members/search": "Member Search - LegacyBank Operations Console",
    "/members/M1001": "Member M1001 - LegacyBank Operations Console",
    "/members/M1002": "Member M1002 - LegacyBank Operations Console",
    "/members/M1001/transfer": "Transfer Funds - Member M1001 - LegacyBank Operations Console",
    "/members/M1001/transfer/complete": (
        "Transfer Complete - Member M1001 - LegacyBank Operations Console"
    ),
}
TRANSFER_PATH = "/members/M1001/transfer"
TRANSFER_COMPLETE_PATH = "/members/M1001/transfer/complete"

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
        listener: DispatchListener | None = None,
    ) -> None:
        self.base_url = base_url
        self.ambiguous = ambiguous
        self.listener: DispatchListener = listener or NullDispatchListener()
        # path -> raw aria text; tests may override or add pages (e.g. one missing a control).
        self.pages: dict[str, str] = {
            "/members/search": fixture_text("A_search"),
            "/members/M1001": fixture_text(
                "G_detail_M1001_ambiguous" if ambiguous else "B_detail_M1001"
            ),
            "/members/M1002": fixture_text("C_detail_M1002"),
            TRANSFER_PATH: fixture_text("D_transfer_form"),
        }
        if pages:
            self.pages.update(pages)
        self.path = "about:blank"
        self.typed: dict[str, str] = {}  # accessible name -> typed value on the current page
        self.not_found_for: str | None = None
        self.reviewing = False  # the transfer form was submitted for review (same path)
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
            url=self._url(),
            page_title=TITLES.get(self.path, self.path),
            step_index=self._step_index,
            elements=elements,
            visible_text_outline=outline,
        )

    def act(self, action: SurfaceAction) -> ActResult:
        if self.raise_on_act is not None and not isinstance(self.raise_on_act, SurfaceDriverError):
            raise self.raise_on_act  # before anything: ref resolution / programming error
        if action.action_type is ActionType.NAVIGATE:
            if not action.url:
                raise UnsupportedActionError("NAVIGATE requires url")
            assert action.url.startswith(self.base_url), action.url
            record = self._record(action, destination=action.url)

            def navigate() -> ActResult:
                self.path = action.url[len(self.base_url) :] or "/"
                self.typed = {}
                self.not_found_for = None
                return ActResult(action_type=action.action_type, url_after=self._url())

            return self._attempt(record, navigate)

        if not self._refs_valid:
            raise StaleObservationError("refs are stale: observe() again")
        element = next((e for e in self._elements if e.ref == action.ref), None)
        if element is None:
            raise UnknownRefError(f"{action.ref} is not part of the current observation")
        record = self._record(action, element=element)

        def operate() -> ActResult:
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

        return self._attempt(record, operate)

    def capture_screenshot(self) -> bytes:
        """Deterministic fake PNG-like bytes naming the current page (ScreenshotCapable)."""
        return b"\x89PNG\r\n\x1a\n" + b"scripted " + self.path.encode("utf-8")

    def close(self) -> None:
        pass

    # --- dispatch evidence (same order as PlaywrightSurface) ----------------------------------

    def _record(
        self,
        action: SurfaceAction,
        *,
        destination: str | None = None,
        element: SurfaceElement | None = None,
    ) -> DispatchRecord:
        return DispatchRecord(
            session_id=self.session_id,
            dispatch_seq=self.dispatched_actions + 1,
            observation_index=self._step_index,
            action_type=action.action_type,
            url_before=self._url() if self.path != "about:blank" else "about:blank",
            destination=destination,
            target_role=element.role if element else None,
            target_name=element.accessible_name if element else None,
            value=action.value
            if action.action_type in (ActionType.FILL, ActionType.SELECT)
            else None,
        )

    def _attempt(self, record: DispatchRecord, operation) -> ActResult:
        self.listener.on_dispatched(record)
        self._dispatch()
        try:
            if isinstance(self.raise_on_act, SurfaceDriverError):
                raise self.raise_on_act  # the "driver" fails after the attempt is recorded
            result = operation()
        except SurfaceDriverError as exc:
            self.listener.on_failed(record, exc)
            raise
        self.listener.on_completed(record, result)
        return result

    def _dispatch(self) -> None:
        self.dispatched_actions += 1
        self._refs_valid = False

    def _url(self) -> str:
        # Before the first NAVIGATE the page is blank, as in a fresh browser (discovery observes
        # it; replay never does because its first step navigates).
        return "about:blank" if self.path == "about:blank" else f"{self.base_url}{self.path}"

    # --- Milestone 8: SIMULATION of a human acting in the browser (unit tests only) -----------

    def simulate_human_confirm(self) -> None:
        """What the page looks like after a *human* clicked "Confirm transfer" — a state change
        underneath the contract, never an ``act()``. Simulation for state-machine tests only; it
        does not count as the official headed E08/E09 proof."""
        self.path = TRANSFER_COMPLETE_PATH
        self.pages[TRANSFER_COMPLETE_PATH] = fixture_text("H_transfer_complete")
        self.reviewing = False
        self.typed = {}
        self._refs_valid = False

    def _click(self, element: SurfaceElement) -> None:
        if not self.click_navigates:
            return
        if element.role == "button" and element.accessible_name == "Review transfer":
            self.reviewing = True  # the Legacy Bank renders the review page at the same URL
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
        if self.path == "about:blank":
            return ""  # a blank page has no accessibility tree
        if self.path == "/members/search" and self.not_found_for is not None:
            return fixture_text("F_not_found").replace("M404", self.not_found_for)
        if self.path == TRANSFER_PATH and self.reviewing:
            return fixture_text("E_transfer_review")
        try:
            return self.pages[self.path]
        except KeyError:
            return '- generic [ref=e1]:\n  - heading "Not Found" [level=1] [ref=e2]\n'

    def _with_typed(self, element: SurfaceElement) -> SurfaceElement:
        if element.role == "textbox" and element.accessible_name in self.typed:
            return element.model_copy(update={"value": self.typed[element.accessible_name]})
        return element
