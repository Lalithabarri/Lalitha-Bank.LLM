"""The minimal domain vocabulary matches ARCHITECTURE §5 / D07 / D08 exactly."""

import pytest
from pydantic import ValidationError

from cua.domain import ActionType, SurfaceElement, SurfaceSnapshot


def test_action_vocabulary_is_the_closed_set_from_d08():
    assert {a.value for a in ActionType} == {
        "CLICK",
        "FILL",
        "SELECT",
        "NAVIGATE",
        "READ",
        "WAIT",
        "FINISH",
        "REPORT_BLOCKED",
    }
    assert len(ActionType) == 8


def test_surface_element_fields_match_d07():
    assert set(SurfaceElement.model_fields) == {
        "ref",
        "role",
        "accessible_name",
        "value",
        "enabled",
        "tag_hint",
        "context_hint",
    }


def test_surface_snapshot_fields_match_d07():
    assert set(SurfaceSnapshot.model_fields) == {
        "url",
        "page_title",
        "step_index",
        "elements",
        "visible_text_outline",
    }


def _snapshot() -> SurfaceSnapshot:
    return SurfaceSnapshot(
        url="http://127.0.0.1:8000/members/search",
        page_title="Member Search - LegacyBank Operations Console",
        step_index=0,
        elements=[
            SurfaceElement(ref="e1", role="textbox", accessible_name="Member ID", value=""),
            SurfaceElement(ref="e2", role="button", accessible_name="Search", tag_hint="button"),
        ],
        visible_text_outline="Member Search\nLook up member\nMember ID",
    )


def test_snapshot_round_trips_through_json():
    snapshot = _snapshot()
    assert SurfaceSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_models_are_frozen():
    snapshot = _snapshot()
    with pytest.raises(ValidationError):
        snapshot.step_index = 1  # type: ignore[misc]


def test_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        SurfaceElement(ref="e1", role="button", accessible_name="Search", css="#go")
