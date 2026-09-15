"""The closed transform vocabulary: STRING, DECIMAL, INTEGER, BOOLEAN (ARCHITECTURE §6).

Every transform goes from observed text to a typed value by explicit, closed rules — no eval,
no locale guessing. DECIMAL is parsed from the normalized string straight into ``Decimal``;
nothing in this module goes through a binary floating-point value (a structural test asserts
the word never appears here).
"""

import re
from decimal import Decimal, InvalidOperation

from cua.artifact import TransformType


class TransformError(ValueError):
    """The observed text does not denote a value of the declared type."""


_DECIMAL = re.compile(r"^-?\d+(\.\d+)?$")
_INTEGER = re.compile(r"^-?\d+$")
_TRUE = frozenset({"true", "yes", "1"})
_FALSE = frozenset({"false", "no", "0"})

PYTHON_TYPES: dict[TransformType, type] = {
    TransformType.STRING: str,
    TransformType.DECIMAL: Decimal,
    TransformType.INTEGER: int,
    TransformType.BOOLEAN: bool,
}


def _normalize_number(text: str, kind: str) -> str:
    """Strip currency presentation: one leading ``$``, ``,`` separators, ``(x)`` negatives."""
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text.startswith("-"):
        negative = not negative
        text = text[1:].strip()
    if text.startswith("$"):
        text = text[1:].strip()
    text = text.replace(",", "")
    if not text:
        raise TransformError(f"empty text is not a {kind}")
    return f"-{text}" if negative else text


def apply_transform(transform: TransformType, raw: str) -> Decimal | int | bool | str:
    if not isinstance(raw, str):
        raise TransformError(f"expected observed text, got {type(raw).__name__}")
    text = raw.strip()
    if transform is TransformType.STRING:
        return text
    if transform is TransformType.DECIMAL:
        number = _normalize_number(text, "decimal")
        if not _DECIMAL.match(number):
            raise TransformError(f"{raw!r} is not a decimal")
        try:
            return Decimal(number)
        except InvalidOperation as exc:  # unreachable after the regex; belt and braces
            raise TransformError(f"{raw!r} is not a decimal") from exc
    if transform is TransformType.INTEGER:
        number = _normalize_number(text, "integer")
        if not _INTEGER.match(number):
            raise TransformError(f"{raw!r} is not an integer")
        return int(number)
    if transform is TransformType.BOOLEAN:
        lowered = text.lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
        raise TransformError(f"{raw!r} is not a boolean")
    raise TransformError(f"unknown transform {transform!r}")  # closed vocabulary


def validate_output(transform: TransformType, value: object) -> None:
    """The produced value must be exactly the declared Python type (bool is not an INTEGER)."""
    expected = PYTHON_TYPES[transform]
    if type(value) is not expected:
        raise TransformError(
            f"{transform.value} output must be {expected.__name__}, got {type(value).__name__}"
        )
