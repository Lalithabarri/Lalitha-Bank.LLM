"""The Milestone 2 feasibility experiment: a real Chromium observing the real Legacy Bank app.

Adapter-level tests. They call ``Surface.act()`` directly to exercise the driver boundary; in
production every automated action reaches ``act()`` only through ``ActionGate``
(ARCHITECTURE §8). Set ``CUA_FEASIBILITY_DUMP=<dir>`` to write every observed snapshot as JSON.
"""

import json
import os
import socket
from pathlib import Path

import pytest

from cua.domain import ActionType, SurfaceSnapshot
from cua.surface import (
    StaleObservationError,
    SurfaceAction,
    SurfaceDriverError,
    SurfaceError,
    UnknownRefError,
    UnsupportedActionError,
    find,
)

pytestmark = pytest.mark.browser

ACCOUNTS = "table: Accounts > row: "


def dump(name: str, snapshot: SurfaceSnapshot, extra: dict | None = None) -> None:
    target = os.environ.get("CUA_FEASIBILITY_DUMP")
    if not target:
        return
    directory = Path(target)
    directory.mkdir(parents=True, exist_ok=True)
    payload = snapshot.model_dump()
    if extra:
        payload["_measured"] = extra
    (directory / f"{name}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def observe(surface, name: str) -> SurfaceSnapshot:
    snapshot = surface.observe()
    dump(name, snapshot, {"driver_calls": surface.last_observe_driver_calls})
    return snapshot


def navigate(surface, url: str) -> None:
    surface.act(SurfaceAction(action_type=ActionType.NAVIGATE, url=url))


def one(snapshot, **query):
    matches = find(snapshot, **query)
    assert len(matches) == 1, (query, [m.model_dump() for m in matches])
    return matches[0]


# --- A. member search ------------------------------------------------------------------------


def test_A_search_exposes_member_id_textbox_and_search_button(surface, live_bank):
    navigate(surface, live_bank.url("/members/search"))
    snap = observe(surface, "A_search")
    assert snap.page_title == "Member Search - LegacyBank Operations Console"
    assert snap.url.endswith("/members/search")
    one(snap, role="textbox", name="Member ID")
    one(snap, role="button", name="Search")
    one(snap, role="heading", name="Member Search")
    assert surface.last_observe_driver_calls == 2


# --- B/C. member detail — one strategy, two members ------------------------------------------


def assert_member_detail(
    snap: SurfaceSnapshot, member_id: str, name: str, checking: str, savings: str
):
    """One semantic strategy must identify any member's detail page — nothing member-specific."""
    one(snap, role="heading", name=f"Member {member_id} — {name}")
    assert one(snap, role="cell", context_hint=ACCOUNTS + "Checking").value == checking
    assert one(snap, role="cell", context_hint=ACCOUNTS + "Savings").value == savings
    one(snap, role="link", name="Transfer funds")


@pytest.mark.parametrize(
    "member_id, name, checking, savings",
    [
        ("M1001", "Alice Morgan", "$2,340.50", "$15,275.00"),
        ("M1002", "Rahul Iyer", "$980.00", "$4,120.75"),
    ],
)
def test_BC_member_detail_semantics_hold_for_both_members(
    surface, live_bank, member_id, name, checking, savings
):
    navigate(surface, live_bank.url(f"/members/{member_id}"))
    snap = observe(surface, f"BC_detail_{member_id}")
    assert_member_detail(snap, member_id, name, checking, savings)


# --- D. transfer form ------------------------------------------------------------------------


def test_D_transfer_form_controls(surface, live_bank):
    navigate(surface, live_bank.url("/members/M1001/transfer"))
    snap = observe(surface, "D_transfer_form")
    assert one(snap, role="combobox", name="From account").value == "Checking"
    assert one(snap, role="combobox", name="To account").value == "Savings"
    one(snap, role="textbox", name="Amount")
    one(snap, role="button", name="Review transfer")


# --- E. transfer review ----------------------------------------------------------------------


def test_E_transfer_review_summary_and_controls(surface, live_bank):
    navigate(surface, live_bank.url("/members/M1001/transfer"))
    form = observe(surface, "E0_transfer_form")
    amount = one(form, role="textbox", name="Amount")
    surface.act(SurfaceAction(action_type=ActionType.FILL, ref=amount.ref, value="500.00"))
    form = observe(surface, "E1_transfer_form_filled")
    review_button = one(form, role="button", name="Review transfer")
    surface.act(SurfaceAction(action_type=ActionType.CLICK, ref=review_button.ref))

    snap = observe(surface, "E_transfer_review")
    one(snap, role="heading", name="Review Transfer — Member M1001")
    summary = "table: Transfer summary > row: "
    assert one(snap, role="cell", context_hint=summary + "Amount").value == "$500.00"
    assert one(snap, role="cell", context_hint=summary + "From account").value == "Checking"
    assert one(snap, role="cell", context_hint=summary + "To account").value == "Savings"
    one(snap, role="button", name="Confirm transfer")
    one(snap, role="link", name="Cancel and go back")
    # Review is not a commit: nothing moved.
    navigate(surface, live_bank.url("/members/M1001"))
    assert_member_detail(
        observe(surface, "E2_detail_after_review"),
        "M1001",
        "Alice Morgan",
        "$2,340.50",
        "$15,275.00",
    )


# --- F. member not found ---------------------------------------------------------------------


def test_F_member_not_found_is_an_observable_business_state(surface, live_bank):
    navigate(surface, live_bank.url("/members/search"))
    snap = observe(surface, "F0_search")
    box = one(snap, role="textbox", name="Member ID")
    surface.act(SurfaceAction(action_type=ActionType.FILL, ref=box.ref, value="M404"))
    snap = observe(surface, "F1_search_filled")
    surface.act(
        SurfaceAction(action_type=ActionType.CLICK, ref=one(snap, role="button", name="Search").ref)
    )

    snap = observe(surface, "F_not_found")
    alert = one(snap, role="alert")
    assert alert.value == "No member found for M404."
    one(snap, role="heading", name="Member Search")
    assert one(snap, role="textbox", name="Member ID").value == "M404"
    assert "alert: No member found for M404." in snap.visible_text_outline


# --- G. ambiguous savings --------------------------------------------------------------------


def test_G_ambiguous_savings_preserves_two_distinct_candidates(surface, live_bank_ambiguous):
    navigate(surface, live_bank_ambiguous.url("/members/M1001"))
    snap = observe(surface, "G_detail_M1001_ambiguous")
    candidates = find(snap, role="cell", context_hint=ACCOUNTS + "Savings")
    assert len(candidates) == 2
    assert {c.value for c in candidates} == {"$15,275.00", "$250.00"}
    assert len({c.ref for c in candidates}) == 2
    assert len(find(snap, role="rowheader", name="Savings")) == 2
    assert len(find(snap, role="cell", context_hint=ACCOUNTS + "Checking")) == 1


def test_G_every_ref_resolves_to_the_element_it_came_from(surface, live_bank_ambiguous):
    """Snapshot-order ordinals vs live get_by_role order: prove they agree, element by element.

    Adapter-level: reaches into the private resolver on purpose.
    """
    navigate(surface, live_bank_ambiguous.url("/members/M1001"))
    snap = observe(surface, "G_alignment")
    for element in snap.elements:
        locator = surface._resolve(element.ref)
        assert locator.count() == 1, element
        own = locator.aria_snapshot().splitlines()[0]
        assert own.startswith(f"- {element.role}"), (element, own)
        if element.accessible_name:
            assert f'"{element.accessible_name}"' in own, (element, own)
        if element.role in {"cell", "rowheader"}:
            row_text = locator.evaluate("e => e.closest('tr').innerText")
            assert element.value in row_text, (element, row_text)
            row_hint = element.context_hint.rsplit("row: ", 1)[-1]
            assert row_hint in row_text, (element, row_text)
    # The two Savings cells resolve to two different DOM elements.
    savings = find(snap, role="cell", context_hint=ACCOUNTS + "Savings")
    texts = [surface._resolve(c.ref).evaluate("e => e.closest('tr').innerText") for c in savings]
    assert texts[0] != texts[1]


# --- transient refs are observation-local ----------------------------------------------------


def test_same_ref_string_means_different_things_on_different_pages(surface, live_bank):
    navigate(surface, live_bank.url("/members/search"))
    first = observe(surface, "refs_search")
    navigate(surface, live_bank.url("/members/M1001"))
    second = observe(surface, "refs_detail")
    first_map = {e.ref: (e.role, e.accessible_name) for e in first.elements}
    second_map = {e.ref: (e.role, e.accessible_name) for e in second.elements}
    # Refs are not browser-stable ids: the same string is not guaranteed to survive, and when a
    # string does recur it need not denote the same thing.
    assert first_map != second_map
    assert second.step_index == first.step_index + 1


def test_act_after_act_without_observe_is_stale(surface, live_bank):
    navigate(surface, live_bank.url("/members/search"))
    snap = observe(surface, "stale_search")
    box = one(snap, role="textbox", name="Member ID")
    surface.act(SurfaceAction(action_type=ActionType.FILL, ref=box.ref, value="M1001"))
    with pytest.raises(StaleObservationError):
        surface.act(SurfaceAction(action_type=ActionType.CLICK, ref=box.ref))
    # A fresh observation re-validates.
    snap = observe(surface, "stale_search_reobserved")
    surface.act(
        SurfaceAction(action_type=ActionType.CLICK, ref=one(snap, role="button", name="Search").ref)
    )
    assert_member_detail(
        observe(surface, "stale_detail"), "M1001", "Alice Morgan", "$2,340.50", "$15,275.00"
    )


def test_unknown_ref_and_ref_from_previous_observation(surface, live_bank):
    navigate(surface, live_bank.url("/members/M1001"))
    detail = observe(surface, "prev_detail")
    transfer_link = one(detail, role="link", name="Transfer funds")
    navigate(surface, live_bank.url("/members/search"))
    observe(surface, "prev_search")
    with pytest.raises(UnknownRefError):
        surface.act(SurfaceAction(action_type=ActionType.CLICK, ref=transfer_link.ref))
    with pytest.raises(UnknownRefError):
        surface.act(SurfaceAction(action_type=ActionType.CLICK, ref="e999"))


def test_navigate_invalidates_refs(surface, live_bank):
    navigate(surface, live_bank.url("/members/M1001"))
    detail = observe(surface, "nav_detail")
    link = one(detail, role="link", name="Transfer funds")
    navigate(surface, live_bank.url("/members/M1002"))
    with pytest.raises(StaleObservationError):
        surface.act(SurfaceAction(action_type=ActionType.CLICK, ref=link.ref))


def test_unsupported_actions_are_rejected_before_any_dispatch(surface, live_bank):
    navigate(surface, live_bank.url("/members/search"))
    observe(surface, "unsupported")
    before = surface.dispatched_actions
    for action_type in (ActionType.WAIT, ActionType.FINISH, ActionType.REPORT_BLOCKED):
        with pytest.raises(UnsupportedActionError):
            surface.act(SurfaceAction(action_type=action_type))
    with pytest.raises(UnsupportedActionError):
        surface.act(SurfaceAction(action_type=ActionType.CLICK))  # no ref
    assert surface.dispatched_actions == before


# --- driver failures cross the boundary as SurfaceDriverError ---------------------------------


def closed_port() -> int:
    """A port nothing is listening on (bound to 0, read back, released)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_driver_failure_is_a_surface_driver_error_not_a_playwright_type(surface):
    before = surface.dispatched_actions
    with pytest.raises(SurfaceDriverError) as excinfo:
        navigate(surface, f"http://127.0.0.1:{closed_port()}/members/search")
    assert isinstance(excinfo.value, SurfaceError)
    assert excinfo.value.__cause__ is not None
    assert type(excinfo.value.__cause__).__module__.startswith("playwright")
    assert "playwright" not in type(excinfo.value).__module__
    assert surface.dispatched_actions == before + 1  # the action was attempted


# --- session lifecycle -----------------------------------------------------------------------


def test_surface_is_single_use_and_forgets_its_session_id(live_bank):
    from cua.surface.playwright_surface import PlaywrightSurface

    surface = PlaywrightSurface(headless=True)
    surface.open()
    session_id = surface.session_id
    assert session_id.startswith("sess_")
    navigate(surface, live_bank.url("/members/search"))
    snap = observe(surface, "lifecycle")
    assert snap.step_index == 1
    surface.close()
    with pytest.raises(RuntimeError):
        _ = surface.session_id
    with pytest.raises(RuntimeError):
        surface.observe()
    with pytest.raises(RuntimeError):
        surface.open()
    surface.close()  # idempotent

    fresh = PlaywrightSurface(headless=True).open()
    try:
        assert fresh.session_id != session_id
        navigate(fresh, live_bank.url("/members/search"))
        assert fresh.observe().step_index == 1
    finally:
        fresh.close()


# --- the flagship flow, end to end through the surface ----------------------------------------


def test_flagship_read_savings_balance_flow(surface, live_bank):
    navigate(surface, live_bank.url("/members/search"))
    snap = observe(surface, "flow_1_search")
    surface.act(
        SurfaceAction(
            action_type=ActionType.FILL,
            ref=one(snap, role="textbox", name="Member ID").ref,
            value="M1001",
        )
    )
    snap = observe(surface, "flow_2_filled")
    surface.act(
        SurfaceAction(action_type=ActionType.CLICK, ref=one(snap, role="button", name="Search").ref)
    )
    snap = observe(surface, "flow_3_detail")
    savings = one(snap, role="cell", context_hint=ACCOUNTS + "Savings")
    result = surface.act(SurfaceAction(action_type=ActionType.READ, ref=savings.ref))
    assert result.value == "$15,275.00"
    assert result.url_after.endswith("/members/M1001")
    assert surface.dispatched_actions == 4  # NAVIGATE, FILL, CLICK, READ


def test_snapshot_from_live_browser_round_trips_as_json(surface, live_bank):
    navigate(surface, live_bank.url("/members/M1001"))
    snap = observe(surface, "json_roundtrip")
    text = snap.model_dump_json()
    assert SurfaceSnapshot.model_validate_json(text) == snap
    assert all(e.tag_hint is None for e in snap.elements)  # V1: deliberately not collected


# --- Milestone 5: dispatch evidence at the driver boundary (ARCHITECTURE §10) ----------------


class RefusingEvidence(Exception):
    """Stands in for the evidence layer's own error; the surface must not know its type."""


class Listener:
    """Records every notification in order; can be told to refuse at one of the three points."""

    def __init__(self, refuse_on: str | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.refuse_on = refuse_on

    def _note(self, name: str, record, payload=None) -> None:
        self.calls.append((name, (record, payload)))
        if name == self.refuse_on:
            raise RefusingEvidence(f"cannot record {name}")

    def on_dispatched(self, record) -> None:
        self._note("on_dispatched", record)

    def on_completed(self, record, result) -> None:
        self._note("on_completed", record, result)

    def on_failed(self, record, error) -> None:
        self._note("on_failed", record, error)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def listening_surface():
    from cua.surface.playwright_surface import PlaywrightSurface

    listener = Listener()
    surface = PlaywrightSurface(headless=True, listener=listener)
    surface.open()
    try:
        yield surface, listener
    finally:
        surface.close()


def test_successful_action_is_dispatched_then_completed_with_no_ref_in_the_record(
    listening_surface, live_bank
):
    surface, listener = listening_surface
    navigate(surface, live_bank.url("/members/search"))
    snap = observe(surface, "m5_search")
    textbox = one(snap, role="textbox", name="Member ID")
    surface.act(SurfaceAction(action_type=ActionType.FILL, ref=textbox.ref, value="M1001"))

    assert listener.names == ["on_dispatched", "on_completed", "on_dispatched", "on_completed"]
    (nav_record, _), (nav_done, nav_result) = listener.calls[0][1], listener.calls[1][1]
    assert nav_record.action_type is ActionType.NAVIGATE
    assert nav_record.dispatch_seq == 1 and nav_record.url_before == "about:blank"
    assert nav_record.destination == live_bank.url("/members/search")
    assert nav_done is nav_record and nav_result.url_after == live_bank.url("/members/search")

    (fill_record, _), (_, fill_result) = listener.calls[2][1], listener.calls[3][1]
    assert fill_record.dispatch_seq == 2 and fill_record.observation_index == snap.step_index
    assert (fill_record.target_role, fill_record.target_name) == ("textbox", "Member ID")
    assert fill_record.value == "M1001" and fill_result.value == "M1001"
    assert "ref" not in fill_record.model_dump()
    assert surface.dispatched_actions == 2


def test_action_dispatched_precedes_the_driver_call_and_a_refusal_leaves_no_trace(live_bank):
    from cua.surface.playwright_surface import PlaywrightSurface

    listener = Listener(refuse_on="on_dispatched")
    surface = PlaywrightSurface(headless=True, listener=listener)
    surface.open()
    try:
        with pytest.raises(RefusingEvidence):
            navigate(surface, live_bank.url("/members/search"))
        assert listener.names == ["on_dispatched"]
        assert surface.dispatched_actions == 0  # not represented as attempted
        assert surface.observe().url == "about:blank"  # the driver never navigated
    finally:
        surface.close()


def test_refusal_before_a_fill_leaves_the_field_and_the_refs_untouched(live_bank):
    from cua.surface.playwright_surface import PlaywrightSurface

    listener = Listener()
    surface = PlaywrightSurface(headless=True, listener=listener)
    surface.open()
    try:
        navigate(surface, live_bank.url("/members/search"))
        snap = observe(surface, "m5_refuse_fill")
        textbox = one(snap, role="textbox", name="Member ID")
        listener.refuse_on = "on_dispatched"
        before = surface.dispatched_actions
        with pytest.raises(RefusingEvidence):
            surface.act(SurfaceAction(action_type=ActionType.FILL, ref=textbox.ref, value="M1001"))
        assert surface.dispatched_actions == before
        listener.refuse_on = None
        # Refs are still valid (nothing was dispatched) and the field is still empty.
        result = surface.act(SurfaceAction(action_type=ActionType.READ, ref=textbox.ref))
        assert result.value == ""
    finally:
        surface.close()


def test_driver_failure_after_a_recorded_dispatch_is_dispatched_then_failed(listening_surface):
    surface, listener = listening_surface
    with pytest.raises(SurfaceDriverError) as excinfo:
        navigate(surface, f"http://127.0.0.1:{closed_port()}/members/search")
    assert listener.names == ["on_dispatched", "on_failed"]
    record, error = listener.calls[1][1]
    assert error is excinfo.value and type(error).__name__ == "SurfaceDriverError"
    assert record.dispatch_seq == 1
    assert surface.dispatched_actions == 1  # the driver attempt counts (M4 invariant kept)


def test_refusal_after_the_driver_call_propagates_and_the_action_did_happen(live_bank):
    from cua.surface.playwright_surface import PlaywrightSurface

    listener = Listener(refuse_on="on_completed")
    surface = PlaywrightSurface(headless=True, listener=listener)
    surface.open()
    try:
        with pytest.raises(RefusingEvidence):
            navigate(surface, live_bank.url("/members/search"))
        assert listener.names == ["on_dispatched", "on_completed"]
        assert surface.dispatched_actions == 1
        assert surface.observe().url == live_bank.url("/members/search")  # it navigated
    finally:
        surface.close()


def test_refusal_on_failed_carries_the_driver_error_as_context():
    from cua.surface.playwright_surface import PlaywrightSurface

    listener = Listener(refuse_on="on_failed")
    surface = PlaywrightSurface(headless=True, listener=listener)
    surface.open()
    try:
        with pytest.raises(RefusingEvidence) as excinfo:
            navigate(surface, f"http://127.0.0.1:{closed_port()}/members/search")
        assert isinstance(excinfo.value.__context__, SurfaceDriverError)
        assert surface.dispatched_actions == 1
    finally:
        surface.close()


def test_unknown_ref_produces_no_dispatch_notification(listening_surface, live_bank):
    surface, listener = listening_surface
    navigate(surface, live_bank.url("/members/search"))
    observe(surface, "m5_unknown_ref")
    listener.calls.clear()
    with pytest.raises(UnknownRefError):
        surface.act(SurfaceAction(action_type=ActionType.CLICK, ref="e999"))
    assert listener.names == []
    assert surface.dispatched_actions == 1
