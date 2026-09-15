"""Redactor — deterministic rewriting of evidence before it reaches disk (ARCHITECTURE §8, D19).

A small, explicit V1 policy, not a DLP system. Four rules, applied in this order to every string
reachable from a payload (dict / list / Pydantic model / scalar, recursively):

1. **Key rule** — a mapping key that names a secret (``password``, ``token``, ``api_key``, …, or a
   ``_token`` / ``_secret`` / … suffix) has its *entire* value replaced by the literal
   ``[REDACTED]``. Exact and suffix matching only: ``spinbutton``, ``pinned`` and ``foreign_key``
   are not secrets.
2. **URL rule** — every URL (a whole string or a ``scheme://…`` run inside free text) loses its
   userinfo, every query *value* (keys are kept, so ``?ref=[REDACTED]`` still shows a parameter
   existed) and its fragment.
3. **Ref rule** — a transient observation ref token (``e12``, ``f1e30``) becomes ``<ref>``.
   Defence in depth only: the schema has no field for a ref; a ref can still appear inside a
   driver error message.
4. **Value rule** — every occurrence of a *known sensitive value* becomes its label placeholder,
   e.g. ``<input:member_id>``. The replay recorder registers every bound runtime input this way,
   so identifiers are persisted as deterministic, non-reversible, name-tagged placeholders
   (``/members/<input:member_id>``) rather than removed or partially masked. Values shorter than
   ``MIN_SUBSTRING_LEN`` are replaced only when they equal a whole string, so ``"1"`` cannot blank
   every digit in a balance.

Placeholders are literals — never a hash or digest of the value. Idempotent: redacting redacted
data changes nothing. Pure: no I/O.

Documented V1 limits: derived or unknown PII/secrets in arbitrary prose (a member's *name* in a
heading) are not detected unless they are known values, URL credentials/query values, or sit
under a protected key. Sensitivity is not yet declared on artifact inputs/outputs (D10: a
separate axis, deferred). Unsupported Python types raise ``TypeError`` rather than being
stringified silently.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel

REDACTED = "[REDACTED]"
MIN_SUBSTRING_LEN = 3

DEFAULT_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passcode",
        "pin",
        "token",
        "secret",
        "authorization",
        "cookie",
        "cookies",
        "set_cookie",
        "api_key",
        "apikey",
        "x_api_key",
        "access_token",
        "refresh_token",
        "id_token",
        "session_token",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
    }
)
SENSITIVE_KEY_SUFFIXES: tuple[str, ...] = (
    "_token",
    "_secret",
    "_password",
    "_passcode",
    "_api_key",
    "_credential",
    "_credentials",
)

_URL_IN_TEXT = re.compile(r"[A-Za-z][A-Za-z0-9+.\-]*://[^\s\"'<>]+")
# Playwright's ref shape (``e12``, ``f1e30``) as a whole token. The lookarounds exclude
# identifiers such as ``sess_e12…``, ``e2e`` and ``1e5``.
_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")
_LABEL = re.compile(r"^[a-z][a-z0-9_:.\-]*$")


def normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


class Redactor:
    def __init__(
        self,
        *,
        sensitive_values: Mapping[str, str] | None = None,
        sensitive_keys: frozenset[str] = DEFAULT_SENSITIVE_KEYS,
        key_suffixes: tuple[str, ...] = SENSITIVE_KEY_SUFFIXES,
    ) -> None:
        values = dict(sensitive_values or {})
        for label, value in values.items():
            if not _LABEL.match(label):
                raise ValueError(f"sensitive value label {label!r} is not a plain identifier")
            if not isinstance(value, str) or not value:
                raise ValueError(f"sensitive value for {label!r} must be non-empty text")
        # Longest value first so a value that contains another is replaced whole.
        self._values: tuple[tuple[str, str], ...] = tuple(
            sorted(values.items(), key=lambda item: (-len(item[1]), item[0]))
        )
        self._keys = frozenset(normalize_key(k) for k in sensitive_keys)
        self._suffixes = tuple(normalize_key(s) for s in key_suffixes)

    @property
    def sensitive_labels(self) -> tuple[str, ...]:
        return tuple(label for label, _ in self._values)

    def with_values(self, values: Mapping[str, str]) -> Redactor:
        """A new redactor that also knows ``values`` (labels in ``values`` win on conflict)."""
        merged = dict(self._values)
        merged.update(values)
        return Redactor(
            sensitive_values=merged, sensitive_keys=self._keys, key_suffixes=self._suffixes
        )

    # --- rules -------------------------------------------------------------------------------

    def is_sensitive_key(self, key: str) -> bool:
        normalized = normalize_key(key)
        return normalized in self._keys or normalized.endswith(self._suffixes)

    def redact_url(self, url: str) -> str:
        """Structural URL redaction only (userinfo, query values, fragment); see ``redact_text``."""
        parts = urlsplit(url)
        if not parts.scheme:
            return url
        netloc = parts.netloc.rsplit("@", 1)[-1]
        query = "&".join(
            f"{piece.split('=', 1)[0]}={REDACTED}" if "=" in piece else REDACTED
            for piece in parts.query.split("&")
            if piece
        )
        fragment = REDACTED if parts.fragment else ""
        return urlunsplit((parts.scheme, netloc, parts.path, query, fragment))

    def redact_text(self, text: str) -> str:
        text = _URL_IN_TEXT.sub(lambda match: self.redact_url(match.group(0)), text)
        text = _REF_TOKEN.sub("<ref>", text)
        for label, value in self._values:
            if len(value) >= MIN_SUBSTRING_LEN:
                text = text.replace(value, f"<{label}>")
            elif text == value:
                text = f"<{label}>"
        return text

    def redact(self, data: object) -> object:
        """Recursively redact JSON-shaped data (Pydantic models at any depth are dumped first)."""
        return self._walk(data)

    def _walk(self, data: object) -> object:
        if isinstance(data, BaseModel):
            data = data.model_dump(mode="json")
        if isinstance(data, Mapping):
            return {
                str(key): REDACTED if self.is_sensitive_key(str(key)) else self._walk(value)
                for key, value in data.items()
            }
        if isinstance(data, list | tuple):
            return [self._walk(item) for item in data]
        if isinstance(data, str):
            return self.redact_text(data)
        if data is None or isinstance(data, bool | int | float):
            return data
        raise TypeError(f"cannot redact a {type(data).__name__}; evidence must be JSON-shaped")
