"""Markup rules for the synthetic target: semantic HTML, labelled controls, no test-id shortcuts."""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

import legacy_bank

TEMPLATES_DIR = Path(legacy_bank.__file__).parent / "templates"

_TRANSFER = {"from_account": "checking", "to_account": "savings"}

# Every screen, rendered with real data, in both default and fault mode.
PAGES = [
    ("GET", "/members/search", None),
    ("POST", "/members/search", {"member_id": "M404"}),
    ("GET", "/members/M1001", None),
    ("GET", "/members/M404", None),
    ("GET", "/members/M1001/transfer", None),
    ("POST", "/members/M1001/transfer", {**_TRANSFER, "amount": "1"}),
    ("POST", "/members/M1001/transfer", {**_TRANSFER, "amount": "x"}),
]


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _render(client, method, path, data):
    response = client.open(path, method=method, data=data)
    assert response.status_code in (200, 404), (method, path, response.status_code)
    return response.get_data(as_text=True)


def _complete_page(client) -> str:
    confirm = client.post(
        "/members/M1001/transfer/confirm",
        data={"from_account": "checking", "to_account": "savings", "amount": "1"},
    )
    return client.get(confirm.headers["Location"]).get_data(as_text=True)


def _all_pages(client) -> list[str]:
    pages = [_render(client, *page) for page in PAGES]
    pages.append(_complete_page(client))
    return pages


def test_no_template_contains_test_id_attributes():
    for template in TEMPLATES_DIR.glob("*.html"):
        text = template.read_text()
        assert "data-testid" not in text, template.name
        assert "data-test" not in text, template.name
        assert re.search(r"\bdata-cy\b", text) is None, template.name


@pytest.mark.parametrize("fixture_name", ["client", "client_ambiguous"])
def test_every_form_control_has_a_label(request, fixture_name):
    client = request.getfixturevalue(fixture_name)
    for page in _all_pages(client):
        parser = _Collector()
        parser.feed(page)
        label_targets = {a["for"] for t, a in parser.tags if t == "label" and a.get("for")}
        for tag, attrs in parser.tags:
            if tag in ("input", "select", "textarea") and attrs.get("type") != "hidden":
                assert attrs.get("id") in label_targets, (tag, attrs)


@pytest.mark.parametrize("fixture_name", ["client", "client_ambiguous"])
def test_every_page_has_one_h1_and_a_main(request, fixture_name):
    client = request.getfixturevalue(fixture_name)
    for page in _all_pages(client):
        assert page.count("<h1>") == 1
        assert page.count("<main>") == 1
        assert "<nav" in page


def test_buttons_are_real_submit_buttons_with_visible_text(client):
    for page in _all_pages(client):
        for match in re.finditer(r"<button([^>]*)>([^<]*)</button>", page):
            assert 'type="submit"' in match.group(1)
            assert match.group(2).strip()
