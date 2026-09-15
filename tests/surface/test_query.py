from pathlib import Path

from cua.domain import SurfaceSnapshot
from cua.surface import find
from cua.surface.aria import read_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def snapshot(name: str) -> SurfaceSnapshot:
    elements, outline = read_snapshot((FIXTURES / f"{name}.aria.txt").read_text())
    return SurfaceSnapshot(
        url="http://target/",
        page_title=name,
        step_index=1,
        elements=elements,
        visible_text_outline=outline,
    )


def test_find_returns_every_match_never_first():
    snap = snapshot("G_detail_M1001_ambiguous")
    matches = find(snap, role="cell", context_hint="table: Accounts > row: Savings")
    assert len(matches) == 2
    assert [m.value for m in matches] == ["$15,275.00", "$250.00"]


def test_find_single_and_none():
    snap = snapshot("B_detail_M1001")
    assert len(find(snap, role="cell", context_hint="table: Accounts > row: Savings")) == 1
    assert find(snap, role="button", name="Nope") == []
    assert len(find(snap, role="link")) == 3


def test_find_with_no_filters_returns_all():
    snap = snapshot("A_search")
    assert find(snap) == snap.elements
