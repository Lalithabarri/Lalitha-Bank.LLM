"""RAW snapshot vs model-safe Observation; digest; semantic-target derivation; input evidence."""

from __future__ import annotations

from cua.discovery import (
    MAX_ELEMENTS,
    InputTemplater,
    PlaceholderStyle,
    derive_semantic_target,
    digest,
    identity_matches,
    input_evidence,
    to_observation,
)
from cua.domain import SurfaceElement
from cua.surface import find
from tests.discovery.support import detail_snapshot, not_found_snapshot, search_snapshot

MODEL = InputTemplater({"member_id": "M1001"}, PlaceholderStyle.MODEL)


def test_model_safe_view_masks_every_echo_and_keeps_refs_while_the_raw_snapshot_is_untouched():
    raw = detail_snapshot()
    before = raw.model_dump()
    view = to_observation(raw, MODEL)
    serialised = view.model_dump_json()
    assert "M1001" not in serialised
    assert view.url == "http://fake.test/members/<input:member_id>"
    assert (
        "Member <input:member_id>" in view.page_title or "<input:member_id>" not in view.page_title
    )
    heading = next(e for e in view.elements if e.role == "heading")
    assert heading.accessible_name == "Member <input:member_id> — Alice Morgan"
    id_cell = next(
        e
        for e in view.elements
        if e.role == "cell" and e.context_hint == "table: Member profile > row: Member ID"
    )
    assert id_cell.accessible_name == "<input:member_id>" and id_cell.value == "<input:member_id>"
    assert "h1: Member <input:member_id>" in view.visible_text_outline
    assert [e.ref for e in view.elements] == [e.ref for e in raw.elements]
    assert view.observation_index == raw.step_index and view.truncated is False
    # the raw snapshot still carries the value for local, deterministic use
    assert raw.model_dump() == before and "M1001" in raw.url


def test_unmasked_view_is_only_for_diagnostic_runs():
    raw = detail_snapshot()
    view = to_observation(raw, None)
    assert "M1001" in view.model_dump_json()


def test_element_cap_marks_truncation():
    raw = search_snapshot()
    many = raw.model_copy(
        update={
            "elements": [
                SurfaceElement(ref=f"e{i}", role="cell", accessible_name=str(i))
                for i in range(MAX_ELEMENTS + 5)
            ]
        }
    )
    view = to_observation(many, MODEL)
    assert len(view.elements) == MAX_ELEMENTS and view.truncated is True


def test_digest_ignores_refs_and_changes_with_the_page():
    a = detail_snapshot()
    b = a.model_copy(
        update={"elements": [e.model_copy(update={"ref": "x" + e.ref}) for e in a.elements]}
    )
    assert digest(a) == digest(b)
    assert digest(a) != digest(search_snapshot())
    assert digest(a) != digest(detail_snapshot(ambiguous=True))


def test_semantic_target_and_identity_matches():
    snap = detail_snapshot()
    button = find(search_snapshot(), role="button", name="Search")[0]
    assert derive_semantic_target(button).model_dump() == {
        "role": "button",
        "accessible_name": "Search",
        "context_hint": "group: Look up member",
    }
    rowheader = find(snap, role="rowheader", name="Savings")[0]
    target = derive_semantic_target(rowheader)
    assert (
        target.accessible_name is None and target.context_hint == "table: Accounts > row: Savings"
    )
    assert identity_matches(snap, target) == 1
    assert identity_matches(detail_snapshot(ambiguous=True), target) == 2


def test_input_evidence_route_then_heading_then_nothing():
    assert input_evidence(detail_snapshot(), {"member_id": "M1001"}) == {"member_id": "ROUTE"}
    no_route = detail_snapshot().model_copy(update={"url": "http://fake.test/members/search"})
    assert input_evidence(no_route, {"member_id": "M1001"}) == {"member_id": "HEADING"}
    # the not-found page: the id is in an alert and a textbox, never a heading or the route
    assert input_evidence(not_found_snapshot(), {"member_id": "M404"}) == {}
    assert input_evidence(search_snapshot(), {"member_id": "M1001"}) == {}
