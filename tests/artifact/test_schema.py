"""CapabilityArtifact: round-trips, and every invariant violation fails validation loudly."""

import copy

import pytest
from pydantic import ValidationError

from cua.artifact import (
    PERSISTED_ACTION_TYPES,
    CapabilityArtifact,
    ConditionType,
    ProvenanceSource,
    TargetStrategy,
    TransformType,
    ValueKind,
)
from cua.domain import ActionType


def minimal() -> dict:
    """The smallest valid artifact: navigate, fill an input, read an output."""
    return {
        "artifact_id": "demo@0.1.0",
        "capability_name": "demo",
        "schema_version": "1.0",
        "capability_version": "0.1.0",
        "description": "demo",
        "inputs": {"member_id": {"type": "STRING", "required": True}},
        "outputs": {"balance": {"type": "DECIMAL"}},
        "steps": [
            {
                "step_id": "s1",
                "description": "open",
                "action": "NAVIGATE",
                "route": "/members/search",
                "risk": "SAFE_READ",
                "postcondition": {
                    "type": "element_present",
                    "target": {"strategies": [{"role": "textbox", "name": "Member ID"}]},
                },
            },
            {
                "step_id": "s2",
                "description": "fill",
                "action": "FILL",
                "target": {"strategies": [{"role": "textbox", "name": "Member ID"}]},
                "value": {"kind": "INPUT_REF", "input_name": "member_id"},
                "risk": "REVERSIBLE_WRITE",
                "postcondition": {"type": "text_present", "text": "Member ID"},
            },
            {
                "step_id": "s3",
                "description": "read",
                "action": "READ",
                "target": {
                    "strategies": [{"role": "cell", "scope": "table: Accounts > row: Savings"}]
                },
                "output": "balance",
                "risk": "SAFE_READ",
            },
        ],
        "success_checkpoint": [{"type": "route_matches", "route": "/members/{member_id}"}],
        "known_outcomes": [],
        "provenance": {"source": "handwritten", "compiled_at": "2026-09-15T00:00:00Z"},
    }


def build(mutate=None) -> CapabilityArtifact:
    data = minimal()
    if mutate:
        mutate(data)
    return CapabilityArtifact.model_validate(data)


def rejects(mutate, match: str | None = None):
    with pytest.raises(ValidationError) as excinfo:
        build(mutate)
    if match:
        assert match in str(excinfo.value), str(excinfo.value)


# --- closed vocabularies -----------------------------------------------------------------------


def test_closed_vocabularies_match_architecture():
    assert {t.value for t in TransformType} == {"STRING", "DECIMAL", "INTEGER", "BOOLEAN"}
    assert {k.value for k in ValueKind} == {"LITERAL", "INPUT_REF"}
    assert {c.value for c in ConditionType} == {
        "route_matches",
        "element_present",
        "text_present",
        "value_equals",
    }
    assert PERSISTED_ACTION_TYPES == {
        ActionType.NAVIGATE,
        ActionType.FILL,
        ActionType.SELECT,
        ActionType.CLICK,
        ActionType.READ,
    }
    assert ActionType.WAIT not in PERSISTED_ACTION_TYPES
    assert {s.value for s in ProvenanceSource} == {"handwritten", "discovery"}


# --- valid ---------------------------------------------------------------------------------------


def test_minimal_artifact_validates_and_round_trips():
    artifact = build()
    text = artifact.model_dump_json()
    again = CapabilityArtifact.model_validate_json(text)
    assert again == artifact
    assert again.steps[1].value.input_name == "member_id"
    assert again.provenance.source is ProvenanceSource.HANDWRITTEN


def test_serialized_form_uses_plain_strings_for_discriminators():
    payload = build().model_dump(mode="json")
    assert payload["steps"][1]["value"]["kind"] == "INPUT_REF"
    assert payload["steps"][0]["postcondition"]["type"] == "element_present"
    assert payload["steps"][0]["risk"] == "SAFE_READ"


# --- I1 / I3: no refs, no selectors -----------------------------------------------------------


@pytest.mark.parametrize("ref", ["e12", "f1e30", "e1"])
@pytest.mark.parametrize("field", ["name", "scope", "text_contains", "role"])
def test_transient_ref_is_rejected_as_target_identity(ref, field):
    with pytest.raises(ValidationError, match="transient"):
        TargetStrategy(**{field: ref})


@pytest.mark.parametrize("value", ["//table/tr[2]/td", "css=td", "xpath=//td", "text=Search"])
def test_selector_engine_syntax_is_rejected(value):
    with pytest.raises(ValidationError, match="selector"):
        TargetStrategy(name=value)


@pytest.mark.parametrize("value", [".NET", "/accounts", "#1 priority", "Save .docx", "1e5", "e"])
def test_ordinary_accessible_names_are_not_mistaken_for_selectors(value):
    """The structural model has no selector field; heuristics must not reject real names."""
    assert TargetStrategy(name=value).name == value


def test_strategy_needs_at_least_one_semantic_field():
    with pytest.raises(ValidationError):
        TargetStrategy()


@pytest.mark.parametrize("extra", ["css", "xpath", "selector", "ref", "locator"])
def test_extra_locator_fields_are_errors_on_strategy_and_step(extra):
    def on_strategy(d):
        d["steps"][2]["target"]["strategies"][0][extra] = "anything"

    def on_step(d):
        d["steps"][2][extra] = "anything"

    rejects(on_strategy)
    rejects(on_step)


@pytest.mark.parametrize("extra", ["code", "secret", "approval", "approved_by", "transcript"])
def test_code_secret_and_approval_fields_are_errors(extra):
    def mutate(d):
        d[extra] = "anything"

    rejects(mutate)


# --- persisted actions only -----------------------------------------------------------------------


@pytest.mark.parametrize("action", ["WAIT", "FINISH", "REPORT_BLOCKED", "EVAL"])
def test_non_persisted_action_types_are_rejected(action):
    def mutate(d):
        d["steps"][0]["action"] = action

    rejects(mutate)


# --- I2: inputs are declared and actually used ----------------------------------------------------


def test_input_ref_to_undeclared_input_is_rejected():
    def mutate(d):
        d["steps"][1]["value"] = {"kind": "INPUT_REF", "input_name": "ghost"}

    rejects(mutate, "undeclared inputs")


def test_placeholder_to_undeclared_input_is_rejected():
    def mutate(d):
        d["success_checkpoint"] = [{"type": "route_matches", "route": "/members/{ghost}"}]

    rejects(mutate, "undeclared inputs")


def test_required_input_never_referenced_is_rejected():
    def mutate(d):
        d["steps"][1]["value"] = {"kind": "LITERAL", "value": "M1001"}
        d["success_checkpoint"] = [{"type": "route_matches", "route": "/members/M1001"}]

    rejects(mutate, "never referenced")


def test_optional_input_may_be_unreferenced():
    def mutate(d):
        d["inputs"]["note"] = {"type": "STRING", "required": False}

    build(mutate)


# --- I4: outputs <-> READ steps -------------------------------------------------------------------


def test_read_with_undeclared_output_is_rejected():
    def mutate(d):
        d["steps"][2]["output"] = "mystery"

    rejects(mutate, "undeclared outputs")


def test_declared_output_never_read_is_rejected():
    def mutate(d):
        d["outputs"]["extra"] = {"type": "STRING"}

    rejects(mutate, "never produced")


def test_output_read_twice_is_rejected():
    def mutate(d):
        d["steps"].append(copy.deepcopy(d["steps"][2]) | {"step_id": "s4"})

    rejects(mutate, "only one READ")


def test_missing_checkpoint_is_rejected():
    def mutate(d):
        d["success_checkpoint"] = []

    rejects(mutate)


# --- I5: known outcomes are business outcomes -----------------------------------------------------


def test_known_outcome_must_be_business_outcome():
    def mutate(d):
        d["known_outcomes"] = [
            {
                "code": "MEMBER_NOT_FOUND",
                "terminal_status": "FAILURE",
                "detector": {"type": "text_present", "text": "No member found"},
            }
        ]

    rejects(mutate)


def test_known_outcome_code_shape():
    def mutate(d):
        d["known_outcomes"] = [
            {
                "code": "member not found",
                "terminal_status": "BUSINESS_OUTCOME",
                "detector": {"type": "text_present", "text": "x"},
            }
        ]

    rejects(mutate)


# --- step shape per action ------------------------------------------------------------------------


def test_navigate_with_target_is_rejected():
    def mutate(d):
        d["steps"][0]["target"] = {"strategies": [{"role": "link", "name": "Member Search"}]}

    rejects(mutate, "must not carry a target")


def test_non_navigate_with_route_is_rejected():
    def mutate(d):
        d["steps"][1]["route"] = "/members/search"

    rejects(mutate, "must not carry a route")


def test_fill_without_value_is_rejected():
    def mutate(d):
        del d["steps"][1]["value"]

    rejects(mutate, "requires a value binding")


def test_click_with_value_is_rejected():
    def mutate(d):
        d["steps"][0] = {
            "step_id": "s1",
            "description": "click",
            "action": "CLICK",
            "target": {"strategies": [{"role": "button", "name": "Search"}]},
            "value": {"kind": "LITERAL", "value": "x"},
            "risk": "SAFE_READ",
            "postcondition": {"type": "text_present", "text": "x"},
        }

    rejects(mutate, "must not carry a value binding")


def test_read_with_postcondition_is_rejected():
    def mutate(d):
        d["steps"][2]["postcondition"] = {"type": "text_present", "text": "x"}

    rejects(mutate, "extraction target")


def test_mutating_step_without_postcondition_is_rejected():
    def mutate(d):
        del d["steps"][1]["postcondition"]

    rejects(mutate, "requires a postcondition")


def test_route_must_be_a_path_not_a_url():
    def mutate(d):
        d["steps"][0]["route"] = "http://127.0.0.1:8000/members/search"

    rejects(mutate, "not a full URL")


# --- identity / versioning ------------------------------------------------------------------------


def test_artifact_id_must_equal_name_at_version():
    def mutate(d):
        d["artifact_id"] = "demo@9.9.9"

    rejects(mutate, "artifact_id must be")


@pytest.mark.parametrize("version", ["1", "1.0", "v1.0.0", "1.0.0-beta"])
def test_capability_version_must_be_semver(version):
    def mutate(d):
        d["capability_version"] = version
        d["artifact_id"] = f"demo@{version}"

    rejects(mutate)


def test_schema_version_is_the_format_version():
    def mutate(d):
        d["schema_version"] = "2.0"

    rejects(mutate)


def test_duplicate_step_ids_are_rejected():
    def mutate(d):
        d["steps"][1]["step_id"] = "s1"

    rejects(mutate, "unique")
