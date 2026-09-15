"""The public Surface contract is driver-neutral and uses serializable boundary types."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from cua.domain import ActionType, SurfaceElement, SurfaceSnapshot
from cua.surface import SURFACE_ACTION_TYPES, ActResult, SurfaceAction
from cua.surface.aria import read_snapshot

FIXTURES = Path(__file__).parent / "fixtures"


def test_importing_the_contract_does_not_load_playwright():
    code = (
        "import sys, cua.surface, cua.domain, cua.surface.aria, cua.surface.query; "
        "print('playwright' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_snapshot_from_real_capture_round_trips_as_plain_json():
    elements, outline = read_snapshot((FIXTURES / "B_detail_M1001.aria.txt").read_text())
    snap = SurfaceSnapshot(
        url="http://127.0.0.1:8000/members/M1001",
        page_title="Member M1001 - LegacyBank Operations Console",
        step_index=2,
        elements=elements,
        visible_text_outline=outline,
    )
    text = snap.model_dump_json()
    assert SurfaceSnapshot.model_validate_json(text) == snap
    data = json.loads(text)

    def only_plain(value):
        if isinstance(value, dict):
            return all(only_plain(v) for v in value.values())
        if isinstance(value, list):
            return all(only_plain(v) for v in value)
        return value is None or isinstance(value, str | int | bool)

    assert only_plain(data)


def test_surface_action_types_are_the_five_driver_operations():
    assert SURFACE_ACTION_TYPES == {
        ActionType.NAVIGATE,
        ActionType.CLICK,
        ActionType.FILL,
        ActionType.SELECT,
        ActionType.READ,
    }
    assert ActionType.WAIT not in SURFACE_ACTION_TYPES


def test_action_and_result_models_are_frozen_and_strict():
    action = SurfaceAction(action_type=ActionType.FILL, ref="e10", value="M1001")
    with pytest.raises(ValidationError):
        action.value = "x"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SurfaceAction(action_type=ActionType.CLICK, ref="e1", selector="#btn")
    result = ActResult(action_type=ActionType.READ, ref="e30", value="$1", url_after="http://x")
    assert result.model_dump()["value"] == "$1"


def test_element_fields_unchanged_from_d07():
    assert set(SurfaceElement.model_fields) == {
        "ref",
        "role",
        "accessible_name",
        "value",
        "enabled",
        "tag_hint",
        "context_hint",
    }
