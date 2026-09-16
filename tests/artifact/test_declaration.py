"""CapabilityDeclaration: the declared contract is validated before it can reach the compiler."""

import pytest
from pydantic import ValidationError

from cua.artifact import ElementPresent, InputSpec, KnownOutcome, OutputSpec, TransformType
from cua.artifact.declaration import CapabilityDeclaration


def _outcome(code: str, text: str) -> KnownOutcome:
    return KnownOutcome(
        code=code,
        terminal_status="BUSINESS_OUTCOME",
        detector=ElementPresent(target={"strategies": [{"role": "alert", "text_contains": text}]}),
    )


def _declaration(**overrides) -> CapabilityDeclaration:
    fields = dict(
        name="read_thing",
        description="Read a thing.",
        inputs={"thing_id": InputSpec(type=TransformType.STRING)},
        outputs={"value": OutputSpec(type=TransformType.DECIMAL)},
        known_outcomes=[_outcome("NOT_FOUND", "No thing {thing_id}.")],
    )
    fields.update(overrides)
    return CapabilityDeclaration(**fields)


def test_a_valid_declaration_carries_contract_and_declared_outcomes():
    declaration = _declaration()
    assert [o.code for o in declaration.known_outcomes] == ["NOT_FOUND"]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"known_outcomes": [_outcome("X", "a"), _outcome("X", "b")]}, "unique"),
        ({"known_outcomes": [_outcome("X", "No {account_id}")]}, "undeclared inputs"),
        ({"name": "Read-Thing"}, "pattern"),
        ({"outputs": {}}, "at least 1"),
        ({"inputs": {"Thing": InputSpec(type=TransformType.STRING)}}, "identifier"),
    ],
)
def test_invalid_declarations_are_refused(overrides, message):
    with pytest.raises(ValidationError, match=message):
        _declaration(**overrides)
