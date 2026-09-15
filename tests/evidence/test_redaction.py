"""The four redaction rules: deterministic, idempotent, literal placeholders, no hashing."""

import hashlib

import pytest
from pydantic import BaseModel

from cua.evidence import (
    DEFAULT_SENSITIVE_KEYS,
    MIN_SUBSTRING_LEN,
    REDACTED,
    SENSITIVE_KEY_SUFFIXES,
    Redactor,
)

SECRET = "sk-live-ZzYyXx1234567890"


# --- 1. key rule ---------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(DEFAULT_SENSITIVE_KEYS))
def test_every_listed_key_is_redacted_whatever_its_value(key):
    out = Redactor().redact({key: SECRET, "safe": "kept"})
    assert out == {key: REDACTED, "safe": "kept"}


@pytest.mark.parametrize("key", ["Password", "API-KEY", "Access Token", "x-api-key", "SET-COOKIE"])
def test_key_matching_is_case_and_separator_insensitive(key):
    assert Redactor().redact({key: SECRET}) == {key: REDACTED}


@pytest.mark.parametrize("suffix", SENSITIVE_KEY_SUFFIXES)
def test_suffix_rules_catch_prefixed_names(suffix):
    assert Redactor().redact({f"gemini{suffix}": SECRET}) == {f"gemini{suffix}": REDACTED}


@pytest.mark.parametrize("key", ["spinbutton", "pinned", "foreign_key", "tokens_used", "pincode"])
def test_lookalike_keys_are_not_secrets(key):
    assert Redactor().redact({key: "value"}) == {key: "value"}


def test_key_rule_replaces_the_whole_subtree_and_recurses_through_lists_and_models():
    class Inner(BaseModel):
        password: dict
        note: str

    data = {"items": [{"token": {"nested": SECRET}}, Inner(password={"a": 1}, note="n")]}
    out = Redactor().redact(data)
    assert out == {"items": [{"token": REDACTED}, {"password": REDACTED, "note": "n"}]}


# --- 2. url rule ---------------------------------------------------------------------------------


def test_url_loses_userinfo_query_values_and_fragment_but_keeps_keys_and_path():
    r = Redactor()
    assert (
        r.redact_text("http://user:pw@host:8000/members/M1?token=abc&ref=TXN-1#frag")
        == f"http://host:8000/members/M1?token={REDACTED}&ref={REDACTED}#{REDACTED}"
    )
    assert r.redact_text("https://h/p?bare") == f"https://h/p?{REDACTED}"


def test_urls_inside_free_text_are_rewritten_and_non_urls_are_untouched():
    r = Redactor()
    text = f"goto failed at http://h/x?sig={SECRET} and https://u:p@h2/y (net::ERR)"
    assert (
        r.redact_text(text)
        == f"goto failed at http://h/x?sig={REDACTED} and https://h2/y (net::ERR)"
    )
    for untouched in ("about:blank", "/members/search", "Member Search", "a=b&c=d"):
        assert r.redact_text(untouched) == untouched


# --- 3. ref rule ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("e12", "<ref>"),
        ("f1e30", "<ref>"),
        (
            "e7 is not part of the current observation",
            "<ref> is not part of the current observation",
        ),
        ("(f2e1)", "(<ref>)"),
    ],
)
def test_transient_ref_tokens_become_ref_placeholders(text, expected):
    assert Redactor().redact_text(text) == expected


@pytest.mark.parametrize("text", ["sess_e12ab", "run_3f9ae12b", "e2e", "1e5", "Route66", "evt_e1"])
def test_identifiers_and_words_that_merely_contain_a_ref_shape_are_untouched(text):
    assert Redactor().redact_text(text) == text


# --- 4. value rule -------------------------------------------------------------------------------


def test_known_values_become_name_tagged_placeholders_everywhere():
    r = Redactor(sensitive_values={"input:member_id": "M1001"})
    assert r.redact_text("/members/M1001") == "/members/<input:member_id>"
    assert r.redact_text("Member M1001 - Console") == "Member <input:member_id> - Console"
    assert r.redact_text("No member found for M1001.") == "No member found for <input:member_id>."
    assert r.redact({"value": "M1001", "n": 5}) == {"value": "<input:member_id>", "n": 5}


def test_longest_value_wins_when_one_contains_another():
    r = Redactor(sensitive_values={"input:a": "M1001", "input:b": "M10"})
    assert r.redact_text("M1001 and M10") == "<input:a> and <input:b>"


def test_short_values_only_match_whole_strings():
    r = Redactor(sensitive_values={"input:n": "1"})
    assert len("1") < MIN_SUBSTRING_LEN
    assert r.redact_text("$15,275.00") == "$15,275.00"
    assert r.redact_text("1") == "<input:n>"


def test_with_values_derives_a_new_redactor_and_validates_labels():
    base = Redactor()
    derived = base.with_values({"input:member_id": "M1001"})
    assert base.redact_text("M1001") == "M1001"
    assert derived.redact_text("M1001") == "<input:member_id>"
    assert derived.sensitive_labels == ("input:member_id",)
    with pytest.raises(ValueError):
        Redactor(sensitive_values={"Bad Label": "x"})
    with pytest.raises(ValueError):
        Redactor(sensitive_values={"input:x": ""})


# --- properties ----------------------------------------------------------------------------------


def test_redaction_is_deterministic_and_idempotent():
    r = Redactor(sensitive_values={"input:member_id": "M1001", "sentinel": SECRET})
    data = {
        "url": f"http://u:p@h/members/M1001?k={SECRET}#f",
        "msg": f"e12 failed for M1001 with {SECRET}",
        "password": SECRET,
        "list": ["M1001", {"api_key": "k"}],
        "n": 1,
        "b": False,
        "none": None,
    }
    once = r.redact(data)
    assert r.redact(data) == once
    assert r.redact(once) == once


def test_placeholders_are_literals_never_a_digest_of_the_value():
    r = Redactor(sensitive_values={"sentinel": SECRET})
    out = str(r.redact({"password": SECRET, "text": f"see {SECRET}"}))
    assert SECRET not in out
    for digest in (
        hashlib.sha256(SECRET.encode()).hexdigest(),
        hashlib.md5(SECRET.encode()).hexdigest(),
    ):
        assert digest not in out and digest[:8] not in out


def test_unsupported_types_are_refused_not_stringified():
    with pytest.raises(TypeError):
        Redactor().redact({"x": object()})
    with pytest.raises(TypeError):
        Redactor().redact({"x": {1, 2}})


def test_scalars_pass_through_and_tuples_become_lists():
    assert Redactor().redact({"i": 1, "f": 1.5, "b": True, "n": None, "t": ("a", "b")}) == {
        "i": 1,
        "f": 1.5,
        "b": True,
        "n": None,
        "t": ["a", "b"],
    }
