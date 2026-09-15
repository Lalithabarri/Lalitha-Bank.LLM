"""PlaywrightSurface — the only module in ``cua`` that imports Playwright (ARCHITECTURE §4, D05).

Owns the browser lifecycle (one Chromium browser, one context, one page per surface), the
wrapper-generated ``session_id``, observation, and the minimum action execution the feasibility
milestone needs. No Playwright type appears in any public signature; nothing here is re-exported
by ``cua.surface``.

Observation mechanism (documented Playwright API, version 1.62):
  * ``page.aria_snapshot(mode="ai")`` — one call — is the semantic source: roles, accessible
    names, values, tree structure, states, and ``[ref=…]`` observation references.
  * ``page.title()`` — one call.
``tag_hint`` is deliberately left ``None`` in V1: no consumer needs it (D13 target descriptors
are role + name + scope), and filling it cost one extra round trip per role on every observe.

Refs are observation-local. ``observe()`` replaces the internal ``ref -> (role, name, ordinal)``
mapping wholesale; ``act()`` resolves a ref through ``page.get_by_role(role, name=…,
exact=True).nth(ordinal)`` and then invalidates the mapping until the next ``observe()``. No
undocumented selector syntax is used, and no ref ever leaves a ``SurfaceSnapshot``.

A surface is single-use: it names exactly one browser session (``session_id``). After
``close()`` it cannot be reopened and no longer answers its old ``session_id`` — a stale id
would defeat the same-session proof the HITL design relies on (ARCHITECTURE §9, D18).

Every driver call runs inside ``_driver_calls()``: a Playwright ``Error`` (including its
``TimeoutError``) is re-raised as the driver-neutral ``SurfaceDriverError`` with the original
chained as ``__cause__``. No Playwright exception type leaves this module.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from playwright.sync_api import Browser, BrowserContext, Locator, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError

from cua.domain import ActionType, SurfaceElement, SurfaceSnapshot
from cua.domain.ids import new_session_id
from cua.surface.aria import read_snapshot
from cua.surface.contract import (
    SURFACE_ACTION_TYPES,
    ActResult,
    StaleObservationError,
    SurfaceAction,
    SurfaceDriverError,
    UnknownRefError,
    UnsupportedActionError,
)

_INPUT_ROLES = frozenset({"textbox", "combobox", "searchbox", "spinbutton"})


@contextmanager
def _driver_calls() -> Iterator[None]:
    """Translate any driver failure into ``SurfaceDriverError`` at the boundary."""
    try:
        yield
    except PlaywrightError as exc:
        raise SurfaceDriverError(f"{type(exc).__name__}: {exc}") from exc


@dataclass(frozen=True)
class _RefRecipe:
    """How to re-locate a ref from the current snapshot using documented role queries."""

    role: str
    name: str
    ordinal: int  # index among elements sharing (role, name) — or all of ``role`` if unnamed


class PlaywrightSurface:
    def __init__(
        self, *, headless: bool = True, slow_mo_ms: int = 0, default_timeout_ms: int = 5000
    ) -> None:
        self._headless = headless
        self._slow_mo_ms = slow_mo_ms
        self._default_timeout_ms = default_timeout_ms
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._session_id: str | None = None
        self._closed = False
        self._step_index = 0
        self._ref_map: dict[str, _RefRecipe] = {}
        self._refs_valid = False
        # Measurements exposed for the feasibility report; not part of the Surface contract.
        self.last_observe_driver_calls = 0
        self.dispatched_actions = 0

    # --- lifecycle ---------------------------------------------------------------------------

    def open(self) -> PlaywrightSurface:
        if self._closed:
            raise RuntimeError("surface is closed and single-use; create a new PlaywrightSurface")
        if self._page is not None:
            return self
        with _driver_calls():
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(
                headless=self._headless, slow_mo=self._slow_mo_ms or None
            )
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
            self._page.set_default_timeout(self._default_timeout_ms)
        self._session_id = new_session_id()
        return self

    def close(self) -> None:
        """End the session. Idempotent; the surface cannot be reopened afterwards."""
        try:
            with _driver_calls():
                if self._context is not None:
                    self._context.close()
                if self._browser is not None:
                    self._browser.close()
                if self._pw is not None:
                    self._pw.stop()
        finally:
            self._pw = self._browser = self._context = self._page = None
            self._session_id = None
            self._closed = True
            self._ref_map = {}
            self._refs_valid = False

    def __enter__(self) -> PlaywrightSurface:
        return self.open()

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def session_id(self) -> str:
        if self._session_id is None:
            raise RuntimeError("surface is closed" if self._closed else "surface is not open")
        return self._session_id

    def _require_page(self) -> Page:
        if self._page is None:
            raise RuntimeError("surface is closed" if self._closed else "surface is not open")
        return self._page

    # --- observe -----------------------------------------------------------------------------

    def observe(self) -> SurfaceSnapshot:
        page = self._require_page()
        calls = 0

        with _driver_calls():
            text = page.aria_snapshot(mode="ai")
        calls += 1
        elements, outline = read_snapshot(text)

        with _driver_calls():
            title = page.title()
        calls += 1

        self._ref_map = _build_ref_map(elements)
        self._refs_valid = True
        self._step_index += 1
        self.last_observe_driver_calls = calls
        return SurfaceSnapshot(
            url=page.url,
            page_title=title,
            step_index=self._step_index,
            elements=elements,
            visible_text_outline=outline,
        )

    # --- act ---------------------------------------------------------------------------------

    def _resolve(self, ref: str) -> Locator:
        """Resolve a ref from the CURRENT observation to a live element via role queries.

        Private: returns a driver type. Used by ``act()`` and by adapter-level alignment tests.
        """
        if not self._refs_valid:
            raise StaleObservationError(
                "refs are stale: call observe() again before acting after an action"
            )
        recipe = self._ref_map.get(ref)
        if recipe is None:
            raise UnknownRefError(f"{ref} is not part of the current observation")
        page = self._require_page()
        with _driver_calls():
            locator = (
                page.get_by_role(recipe.role, name=recipe.name, exact=True)  # type: ignore[arg-type]
                if recipe.name
                else page.get_by_role(recipe.role)  # type: ignore[arg-type]
            ).nth(recipe.ordinal)
            count = locator.count()
        if count == 0:
            raise UnknownRefError(f"{ref} no longer resolves to an element")
        return locator

    def act(self, action: SurfaceAction) -> ActResult:
        if action.action_type not in SURFACE_ACTION_TYPES:
            raise UnsupportedActionError(f"{action.action_type.value} is not a surface action")
        page = self._require_page()

        if action.action_type is ActionType.NAVIGATE:
            if not action.url:
                raise UnsupportedActionError("NAVIGATE requires url")
            self._dispatch(action)
            with _driver_calls():
                page.goto(action.url)
                page.wait_for_load_state()
                return ActResult(action_type=action.action_type, url_after=page.url)

        if not action.ref:
            raise UnsupportedActionError(f"{action.action_type.value} requires ref")
        locator = self._resolve(action.ref)
        role = self._ref_map[action.ref].role

        value: str | None
        if action.action_type is ActionType.READ:
            self._dispatch(action)
            with _driver_calls():
                value = locator.input_value() if role in _INPUT_ROLES else locator.inner_text()
        elif action.action_type is ActionType.FILL:
            if action.value is None:
                raise UnsupportedActionError("FILL requires value")
            self._dispatch(action)
            with _driver_calls():
                locator.fill(action.value)
                value = locator.input_value()
        elif action.action_type is ActionType.SELECT:
            if action.value is None:
                raise UnsupportedActionError("SELECT requires value")
            self._dispatch(action)
            with _driver_calls():
                locator.select_option(label=action.value)
            value = action.value
        else:  # CLICK
            self._dispatch(action)
            with _driver_calls():
                locator.click()
            value = None

        with _driver_calls():
            page.wait_for_load_state()
            return ActResult(
                action_type=action.action_type, ref=action.ref, value=value, url_after=page.url
            )

    def _dispatch(self, action: SurfaceAction) -> None:
        """The single point every driver-bound action passes through.

        This is where ``ACTION_DISPATCHED`` will be emitted once the EvidenceWriter exists
        (ARCHITECTURE §10, step 9). Until then it only counts and invalidates refs: after any
        action the page may have changed, so the current mapping is no longer trusted.
        """
        self.dispatched_actions += 1
        self._refs_valid = False


def _build_ref_map(elements: list[SurfaceElement]) -> dict[str, _RefRecipe]:
    """Ordinals are counted over exactly the set the act-time locator will match.

    Named element: ``get_by_role(role, name=name, exact=True)`` -> ordinal among same (role, name).
    Unnamed element: ``get_by_role(role)`` matches every element of that role -> ordinal among
    all elements of the role, named or not.
    """
    by_role_and_name: dict[tuple[str, str], int] = {}
    by_role: dict[str, int] = {}
    ref_map: dict[str, _RefRecipe] = {}
    for element in elements:
        role_ordinal = by_role.get(element.role, 0)
        by_role[element.role] = role_ordinal + 1
        if element.accessible_name:
            key = (element.role, element.accessible_name)
            ordinal = by_role_and_name.get(key, 0)
            by_role_and_name[key] = ordinal + 1
        else:
            ordinal = role_ordinal
        ref_map[element.ref] = _RefRecipe(element.role, element.accessible_name, ordinal)
    return ref_map
