"""raw ARIA snapshot text -> parse -> SurfaceElement[], against captures from the real app."""

from pathlib import Path

import pytest

from cua.domain import SurfaceElement
from cua.surface.aria import STRUCTURAL_ROLES, parse_ai_snapshot, read_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / f"{name}.aria.txt").read_text()


def by(elements: list[SurfaceElement], **match) -> list[SurfaceElement]:
    return [e for e in elements if all(getattr(e, k) == v for k, v in match.items())]


EXPECTED_FIXTURES = {
    "A_search",
    "B_detail_M1001",
    "C_detail_M1002",
    "D_transfer_form",
    "E_transfer_review",
    "F_not_found",
    "G_detail_M1001_ambiguous",
}


def test_every_captured_fixture_parses_and_yields_elements():
    names = {p.stem.removesuffix(".aria") for p in FIXTURES.glob("*.aria.txt")}
    assert names == EXPECTED_FIXTURES
    for name in sorted(names):
        text = load(name)
        roots = parse_ai_snapshot(text)
        assert roots, name
        elements, outline = read_snapshot(text)
        assert elements, name
        assert outline, name


def test_search_page_exposes_member_id_textbox_and_search_button():
    elements, outline = read_snapshot(load("A_search"))
    assert by(elements, role="textbox", accessible_name="Member ID")
    assert by(elements, role="button", accessible_name="Search")
    textbox = by(elements, role="textbox")[0]
    assert textbox.context_hint == "group: Look up member"
    assert "h1: Member Search" in outline


@pytest.mark.parametrize(
    "fixture, member_id, name, checking, savings",
    [
        ("B_detail_M1001", "M1001", "Alice Morgan", "$2,340.50", "$15,275.00"),
        ("C_detail_M1002", "M1002", "Rahul Iyer", "$980.00", "$4,120.75"),
    ],
)
def test_member_detail_is_semantically_identifiable(fixture, member_id, name, checking, savings):
    elements, _ = read_snapshot(load(fixture))
    assert by(elements, role="heading", accessible_name=f"Member {member_id} — {name}")
    checking_cells = by(elements, role="cell", context_hint="table: Accounts > row: Checking")
    savings_cells = by(elements, role="cell", context_hint="table: Accounts > row: Savings")
    assert [c.value for c in checking_cells] == [checking]
    assert [c.value for c in savings_cells] == [savings]
    assert by(elements, role="link", accessible_name="Transfer funds")


def test_ambiguous_savings_yields_two_distinct_candidates_not_one():
    elements, _ = read_snapshot(load("G_detail_M1001_ambiguous"))
    savings = by(elements, role="cell", context_hint="table: Accounts > row: Savings")
    assert len(savings) == 2
    assert {c.value for c in savings} == {"$15,275.00", "$250.00"}
    assert len({c.ref for c in savings}) == 2
    assert len(by(elements, role="cell", context_hint="table: Accounts > row: Checking")) == 1
    assert len(by(elements, role="rowheader", accessible_name="Savings")) == 2


def test_transfer_form_controls():
    elements, _ = read_snapshot(load("D_transfer_form"))
    from_box = by(elements, role="combobox", accessible_name="From account")
    to_box = by(elements, role="combobox", accessible_name="To account")
    assert [c.value for c in from_box] == ["Checking"]  # [selected] option -> value
    assert [c.value for c in to_box] == ["Savings"]
    assert by(elements, role="textbox", accessible_name="Amount")
    assert by(elements, role="button", accessible_name="Review transfer")
    assert all(e.context_hint == "group: Transfer details" for e in from_box + to_box)


def test_transfer_review_summary_and_controls():
    elements, _ = read_snapshot(load("E_transfer_review"))
    summary = "table: Transfer summary > row: "
    assert by(elements, role="cell", context_hint=summary + "Amount")[0].value == "$500.00"
    assert by(elements, role="cell", context_hint=summary + "From account")[0].value == "Checking"
    assert by(elements, role="button", accessible_name="Confirm transfer")
    assert by(elements, role="link", accessible_name="Cancel and go back")


def test_member_not_found_is_an_observable_business_state():
    elements, outline = read_snapshot(load("F_not_found"))
    alerts = by(elements, role="alert")
    assert [a.value for a in alerts] == ["No member found for M404."]
    assert by(elements, role="textbox", accessible_name="Member ID")[0].value == "M404"
    assert "alert: No member found for M404." in outline


def test_structural_roles_are_context_not_elements():
    elements, _ = read_snapshot(load("B_detail_M1001"))
    assert not {e.role for e in elements} & STRUCTURAL_ROLES
    assert all(e.ref for e in elements)


def test_parser_rejects_unknown_syntax_explicitly_rather_than_dropping_it():
    with pytest.raises(ValueError):
        parse_ai_snapshot("this is not a snapshot")
    with pytest.raises(ValueError):
        parse_ai_snapshot('- button "Search" trailing-garbage')
    with pytest.raises(ValueError):
        parse_ai_snapshot("- 123 not-a-role")


def test_property_lines_attach_to_parent_whatever_their_key():
    roots = parse_ai_snapshot(
        '- link "Home" [ref=e2]:\n  - /url: /home\n  - /future-prop: some value\n'
    )
    link = roots[0]
    assert link.attrs["url"] == "/home"
    assert link.attrs["future-prop"] == "some value"
    assert link.children == []


def test_escaped_quotes_and_attributes_parse():
    roots = parse_ai_snapshot('- button "Say \\"hi\\"" [disabled] [ref=e9]: note')
    node = roots[0]
    assert node.role == "button"
    assert node.name == 'Say "hi"'
    assert node.attrs == {"disabled": None, "ref": "e9"}
    assert node.text == "note"
    assert node.ref == "e9"
