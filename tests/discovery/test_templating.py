"""Discovery-time templating: whole segment for paths, whole token for text, two styles."""

from __future__ import annotations

import pytest

from cua.discovery import InputTemplater, PlaceholderStyle, placeholder

MODEL = InputTemplater({"member_id": "M1001"}, PlaceholderStyle.MODEL)
TRACE = InputTemplater({"member_id": "M1001"}, PlaceholderStyle.TRACE)


def test_placeholder_styles():
    assert placeholder("member_id", PlaceholderStyle.MODEL) == "<input:member_id>"
    assert placeholder("member_id", PlaceholderStyle.TRACE) == "{member_id}"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("M1001", "<input:member_id>"),
        ("Member M1001 — Alice Morgan", "Member <input:member_id> — Alice Morgan"),
        ("No member found for M1001.", "No member found for <input:member_id>."),
        ("Member M1001 - LegacyBank", "Member <input:member_id> - LegacyBank"),
        ("M10012 is another member", "M10012 is another member"),  # not a whole token
        ("XM1001", "XM1001"),
        ("m1001", "m1001"),  # case-sensitive: the value is the value
        ("", ""),
    ],
)
def test_text_is_whole_token(text, expected):
    assert MODEL.text(text) == expected
    assert TRACE.text(text) == expected.replace("<input:member_id>", "{member_id}")


def test_none_passes_through():
    assert MODEL.text(None) is None


@pytest.mark.parametrize(
    "url, expected",
    [
        ("http://h:8000/members/M1001", "http://h:8000/members/{member_id}"),
        ("http://h/members/M1001/transfer", "http://h/members/{member_id}/transfer"),
        ("http://h/members/M10012", "http://h/members/M10012"),
        (
            "http://h/members/search?member_id=M1001#M1001",
            "http://h/members/search?member_id={member_id}#{member_id}",
        ),
        ("about:blank", "about:blank"),
        ("/members/M1001", "/members/{member_id}"),
    ],
)
def test_url_path_is_whole_segment(url, expected):
    assert TRACE.url(url) == expected


def test_short_values_only_replace_a_whole_string():
    short = InputTemplater({"n": "42"}, PlaceholderStyle.MODEL)
    assert short.text("42") == "<input:n>"
    assert short.text("balance 42 and 420") == "balance 42 and 420"
    assert short.path("/x/42") == "/x/<input:n>"  # segments are exact regardless of length


def test_longest_value_first_replaces_a_containing_value_whole():
    t = InputTemplater({"a": "M1001", "b": "M1001-EXT"}, PlaceholderStyle.MODEL)
    assert t.text("ref M1001-EXT and M1001") == "ref <input:b> and <input:a>"


def test_contains_bound_value_mirrors_the_replacement_rule():
    assert MODEL.contains_bound_value("route /members/M1001")
    assert not MODEL.contains_bound_value("route /members/M10012")
    assert not MODEL.contains_bound_value(MODEL.text("route /members/M1001"))


def test_empty_values_are_refused():
    with pytest.raises(ValueError):
        InputTemplater({"member_id": ""}, PlaceholderStyle.MODEL)


def test_idempotent():
    once = MODEL.text("Member M1001")
    assert MODEL.text(once) == once
