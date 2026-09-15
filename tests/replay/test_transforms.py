"""Closed transforms. DECIMAL comes from the string, never through a float."""

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from cua.artifact import TransformType
from cua.replay import PYTHON_TYPES, TransformError, apply_transform, validate_output
from cua.replay import transforms as transforms_module

T = TransformType


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$15,275.00", Decimal("15275.00")),
        ("$4,120.75", Decimal("4120.75")),
        ("  $250.00 ", Decimal("250.00")),
        ("1234", Decimal("1234")),
        ("-5.5", Decimal("-5.5")),
        ("(1,234.56)", Decimal("-1234.56")),
        ("$-3.00", Decimal("-3.00")),
    ],
)
def test_decimal_from_currency_text(raw, expected):
    value = apply_transform(T.DECIMAL, raw)
    assert type(value) is Decimal
    assert value == expected
    assert str(value) == str(expected)  # scale preserved: "15275.00", not "15275.0"


@pytest.mark.parametrize("raw", ["1e5", "NaN", "Infinity", "-Infinity", "", "abc", "$", "1.2.3"])
def test_decimal_rejects_non_decimal_text(raw):
    with pytest.raises(TransformError):
        apply_transform(T.DECIMAL, raw)


def test_decimal_never_goes_through_float():
    source = Path(transforms_module.__file__).read_text()
    names = {n.id for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Name)}
    assert "float" not in names
    assert "float" not in source.replace("floating-point", "")  # only the docstring's prose


@pytest.mark.parametrize("raw, expected", [("1,234", 1234), ("-7", -7), (" 42 ", 42), ("(3)", -3)])
def test_integer(raw, expected):
    value = apply_transform(T.INTEGER, raw)
    assert type(value) is int and value == expected


@pytest.mark.parametrize("raw", ["12.5", "", "x", "1e3", "true"])
def test_integer_rejects(raw):
    with pytest.raises(TransformError):
        apply_transform(T.INTEGER, raw)


@pytest.mark.parametrize(
    "raw, expected",
    [("true", True), ("Yes", True), ("1", True), ("FALSE", False), ("no", False), ("0", False)],
)
def test_boolean(raw, expected):
    value = apply_transform(T.BOOLEAN, raw)
    assert type(value) is bool and value is expected


@pytest.mark.parametrize("raw", ["", "maybe", "2", "on"])
def test_boolean_rejects(raw):
    with pytest.raises(TransformError):
        apply_transform(T.BOOLEAN, raw)


def test_string_strips_only():
    assert apply_transform(T.STRING, "  Alice Morgan ") == "Alice Morgan"
    assert apply_transform(T.STRING, "$15,275.00") == "$15,275.00"


def test_non_text_input_is_rejected():
    with pytest.raises(TransformError):
        apply_transform(T.DECIMAL, 15275.0)  # type: ignore[arg-type]


def test_validate_output_requires_the_exact_type():
    validate_output(T.DECIMAL, Decimal("1"))
    validate_output(T.INTEGER, 1)
    validate_output(T.BOOLEAN, True)
    validate_output(T.STRING, "x")
    for transform, wrong in [
        (T.DECIMAL, 1.0),
        (T.DECIMAL, "1"),
        (T.INTEGER, True),  # bool is not an INTEGER
        (T.BOOLEAN, 1),
        (T.STRING, 1),
    ]:
        with pytest.raises(TransformError):
            validate_output(transform, wrong)
    assert PYTHON_TYPES[T.DECIMAL] is Decimal
