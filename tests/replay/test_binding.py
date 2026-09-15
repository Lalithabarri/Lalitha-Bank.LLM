"""Binding: typed inputs, no placeholder left unbound, segment-safe routes, artifact untouched."""

import pytest
from pydantic import ValidationError

from cua.artifact import (
    PLACEHOLDER,
    ElementPresent,
    InputRef,
    LiteralValue,
    RouteMatches,
    TargetDescriptor,
    TargetStrategy,
    TextPresent,
    ValueEquals,
)
from cua.replay import (
    BindingError,
    BoundStrategy,
    ReplayConfig,
    bind_artifact,
    bind_inputs,
    bind_route,
    bind_text,
    bind_value,
    build_destination,
    normalize_base_url,
)
from cua.replay.binding import bind_condition, bind_strategy
from tests.artifact.test_schema import build
from tests.replay.support import flagship

BASE = "http://127.0.0.1:8000"


# --- inputs ------------------------------------------------------------------------------------


def test_bind_inputs_happy_path_keeps_original_text():
    assert bind_inputs(flagship(), {"member_id": "M1001"}) == {"member_id": "M1001"}


def test_missing_required_input_is_an_error():
    with pytest.raises(BindingError, match="missing required input 'member_id'"):
        bind_inputs(flagship(), {})


def test_unknown_input_is_an_error():
    with pytest.raises(BindingError, match="unknown inputs"):
        bind_inputs(flagship(), {"member_id": "M1001", "memberid": "M1001"})


def test_non_text_input_is_an_error():
    with pytest.raises(BindingError, match="must be text"):
        bind_inputs(flagship(), {"member_id": 1001})


def test_mistyped_input_is_an_error():
    artifact = build(
        lambda d: d["inputs"].update({"limit": {"type": "DECIMAL", "required": False}})
    )
    with pytest.raises(BindingError, match="not a valid DECIMAL"):
        bind_inputs(artifact, {"member_id": "M1001", "limit": "ten"})
    assert bind_inputs(artifact, {"member_id": "M1001", "limit": "$1,000.00"})["limit"] == (
        "$1,000.00"
    )


def test_optional_input_may_be_absent():
    artifact = build(lambda d: d["inputs"].update({"note": {"type": "STRING", "required": False}}))
    assert bind_inputs(artifact, {"member_id": "M1001"}) == {"member_id": "M1001"}


# --- text / values -----------------------------------------------------------------------------


def test_bind_text_substitutes_every_placeholder():
    assert bind_text("No member found for {member_id}.", {"member_id": "M404"}) == (
        "No member found for M404."
    )
    assert bind_text("{a}-{b}-{a}", {"a": "x", "b": "y"}) == "x-y-x"


def test_unbound_placeholder_is_an_error_never_a_wildcard():
    with pytest.raises(BindingError, match=r"unbound placeholder \{member_id\}"):
        bind_text("Member {member_id}", {})


def test_bind_value_literal_and_input_ref():
    assert bind_value(LiteralValue(value="x-{a}"), {"a": "1"}) == "x-1"
    assert bind_value(InputRef(input_name="a"), {"a": "1"}) == "1"
    with pytest.raises(BindingError, match="not bound"):
        bind_value(InputRef(input_name="a"), {})


def test_bound_strategy_never_carries_a_placeholder_even_from_input_content():
    strategy = TargetStrategy(role="heading", text_contains="Member {member_id}")
    assert bind_strategy(strategy, {"member_id": "M1001"}) == BoundStrategy(
        role="heading", text_contains="Member M1001"
    )
    with pytest.raises(BindingError, match="unbound placeholder"):
        bind_strategy(strategy, {"member_id": "{x}"})  # input that looks like a placeholder
    with pytest.raises(ValidationError):
        BoundStrategy(name="{leftover}")


# --- routes ------------------------------------------------------------------------------------


def test_bind_route_binds_one_segment():
    assert bind_route("/members/{member_id}", {"member_id": "M1001"}) == "/members/M1001"


@pytest.mark.parametrize("bad", ["", "a/b", "a\\b", "a?b", "a#b", "a b", "M1001\n", "{x}", "/evil"])
def test_bind_route_rejects_values_that_are_not_a_single_segment(bad):
    with pytest.raises(BindingError):
        bind_route("/members/{member_id}", {"member_id": bad})


def test_bind_route_rejects_unbound_and_malformed_results():
    with pytest.raises(BindingError, match="unbound placeholder"):
        bind_route("/members/{member_id}", {})
    with pytest.raises(BindingError):
        bind_route("//{host}/x", {"host": "evil.example"})


# --- destination -------------------------------------------------------------------------------


def test_destination_from_origin_and_path():
    assert build_destination(BASE, "/members/search") == f"{BASE}/members/search"


def test_trailing_slash_base_is_normalized_consistently():
    assert normalize_base_url(BASE + "/") == BASE
    assert build_destination(BASE + "/", "/members/search") == f"{BASE}/members/search"
    assert ReplayConfig(base_url=BASE + "/").base_url == BASE


@pytest.mark.parametrize(
    "base",
    [
        f"{BASE}/app",
        f"{BASE}/?x=1",
        f"{BASE}/#f",
        "http://user:pw@127.0.0.1:8000",
        "ftp://127.0.0.1:8000",
        "127.0.0.1:8000",
        "http://127.0.0.1:8000 ",
        "",
    ],
)
def test_base_url_must_be_an_origin_only(base):
    with pytest.raises(ValueError):
        normalize_base_url(base)
    with pytest.raises(ValidationError):
        ReplayConfig(base_url=base)


@pytest.mark.parametrize(
    "route",
    [
        "//evil.example/x",
        "http://evil.example/",
        "https://evil.example",
        "\\evil",
        "/a?b",
        "/a#b",
        "/a b",
        "/{unbound}",
        "members/search",
        "/a//b",
        "",
    ],
)
def test_external_or_malformed_route_is_rejected_before_any_dispatch(route):
    with pytest.raises(BindingError):
        build_destination(BASE, route)


def test_segments_are_percent_encoded_and_origin_is_preserved():
    destination = build_destination(BASE, "/members/M%1\u00e9")
    assert destination == f"{BASE}/members/M%251%C3%A9"
    assert destination.startswith(BASE + "/")
    assert build_destination(BASE, "/") == BASE + "/"
    assert build_destination(BASE, "/members/search/") == f"{BASE}/members/search"


# --- conditions and the whole artifact ---------------------------------------------------------


def test_bind_condition_each_type():
    bound = {"member_id": "M1001"}
    route = bind_condition(RouteMatches(route="/members/{member_id}"), bound)
    assert route.route == "/members/M1001"
    text = bind_condition(TextPresent(text="Member {member_id}"), bound)
    assert text.text == "Member M1001"
    present = bind_condition(
        ElementPresent(target=TargetDescriptor(strategies=[TargetStrategy(role="alert")])), bound
    )
    assert present.target.strategies[0].role == "alert"
    equals = bind_condition(
        ValueEquals(
            target=TargetDescriptor(strategies=[TargetStrategy(role="textbox", name="Member ID")]),
            value=InputRef(input_name="member_id"),
        ),
        bound,
    )
    assert equals.value == "M1001"


def test_bind_artifact_binds_everything_and_leaves_the_artifact_untouched():
    artifact = flagship()
    before = artifact.model_dump()
    view = bind_artifact(artifact, {"member_id": "M1002"}, BASE)
    assert artifact.model_dump() == before
    assert view.steps[0].destination == f"{BASE}/members/search"
    assert view.steps[1].value == "M1002"
    assert view.steps[1].postcondition.value == "M1002"
    assert view.steps[2].postcondition.route == "/members/M1002"
    assert view.steps[3].output == "savings_balance"
    assert view.success_checkpoint[0].route == "/members/M1002"
    assert view.success_checkpoint[1].target.strategies[0].text_contains == "Member M1002"
    assert view.known_outcomes[0].detector.target.strategies[0].text_contains == (
        "No member found for M1002."
    )
    assert not [s for s in _strings(view.model_dump()) if PLACEHOLDER.search(s)]
    assert "member_id" not in _strings(view.model_dump())  # only bound values survive


def _strings(value) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def test_bind_artifact_with_referenced_optional_input_absent_is_an_error():
    def mutate(d):
        d["inputs"]["note"] = {"type": "STRING", "required": False}
        d["steps"][1]["postcondition"] = {"type": "text_present", "text": "note {note}"}

    artifact = build(mutate)
    bound = bind_inputs(artifact, {"member_id": "M1001"})
    with pytest.raises(BindingError, match=r"unbound placeholder \{note\}"):
        bind_artifact(artifact, bound, BASE)
