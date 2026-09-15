"""Discovery-time templating of KNOWN bound input values (D12; M6 amendment A1).

Two consumers, one rule set:

* the **model-safe** view of an observation replaces every bound input value with
  ``<input:<name>>`` — the same placeholder the evidence Redactor persists — before a request is
  built, so a provider never receives a runtime business value merely because the UI echoed it;
* the **normalized trace** records the same positions as ``{<name>}`` — the artifact's own
  placeholder syntax (``cua.artifact.PLACEHOLDER``) — so the future compiler never has to search
  and replace literals.

Matching is deliberately narrow and deterministic: a URL path is templated by whole-segment
equality; any other text by whole-token equality (the value bounded by non-identifier characters);
values shorter than ``MIN_TOKEN_LEN`` only when they equal the whole string. Longer values are
replaced first so a value containing another is replaced whole. This is not PII detection: only
the declared, bound runtime inputs are known.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit

MIN_TOKEN_LEN = 3


class PlaceholderStyle(StrEnum):
    MODEL = "MODEL"  # <input:name>
    TRACE = "TRACE"  # {name}


def placeholder(name: str, style: PlaceholderStyle) -> str:
    return f"<input:{name}>" if style is PlaceholderStyle.MODEL else f"{{{name}}}"


def _token_pattern(value: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])")


class InputTemplater:
    def __init__(self, bound_inputs: Mapping[str, str], style: PlaceholderStyle) -> None:
        for name, value in bound_inputs.items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"bound input {name!r} must be non-empty text")
        self._style = PlaceholderStyle(style)
        # Longest first: a value that contains another is replaced whole.
        self._values: tuple[tuple[str, str], ...] = tuple(
            sorted(bound_inputs.items(), key=lambda item: (-len(item[1]), item[0]))
        )
        self._patterns = {name: _token_pattern(value) for name, value in self._values}

    @property
    def style(self) -> PlaceholderStyle:
        return self._style

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self._values)

    def text(self, text: str | None) -> str | None:
        """Whole-token replacement; short values only when they equal the whole string."""
        if text is None:
            return None
        for name, value in self._values:
            if len(value) >= MIN_TOKEN_LEN:
                text = self._patterns[name].sub(placeholder(name, self._style), text)
            elif text == value:
                text = placeholder(name, self._style)
        return text

    def path(self, path: str) -> str:
        """Whole-segment replacement on a URL path: ``/members/<id>`` -> ``/members/{name}``."""
        segments = path.split("/")
        out: list[str] = []
        for segment in segments:
            replaced = segment
            for name, value in self._values:
                if segment == value:
                    replaced = placeholder(name, self._style)
                    break
            out.append(replaced)
        return "/".join(out)

    def url(self, url: str) -> str:
        """Path by whole segment; query and fragment by whole token; origin untouched."""
        parts = urlsplit(url)
        if not parts.scheme:
            return self.path(url)
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                self.path(parts.path),
                self.text(parts.query) or "",
                self.text(parts.fragment) or "",
            )
        )

    def contains_bound_value(self, text: str) -> bool:
        """True when any bound value still appears (by the same whole-token rule)."""
        for name, value in self._values:
            if len(value) >= MIN_TOKEN_LEN:
                if self._patterns[name].search(text):
                    return True
            elif text == value:
                return True
        return False
