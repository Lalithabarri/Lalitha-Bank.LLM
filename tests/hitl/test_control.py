import pytest

from cua.hitl import ControlOwner, ControlOwnerState


def test_default_owner_is_automation():
    owner = ControlOwner()
    assert owner.state is ControlOwnerState.AUTOMATION
    assert owner.is_automation


def test_four_states_from_architecture_section_9():
    assert {s.value for s in ControlOwnerState} == {
        "AUTOMATION",
        "PENDING_HUMAN",
        "HUMAN",
        "RETURNING",
    }


@pytest.mark.parametrize(
    "state", [s for s in ControlOwnerState if s is not ControlOwnerState.AUTOMATION]
)
def test_test_only_constructor_state_is_honoured(state):
    owner = ControlOwner(state)
    assert owner.state is state
    assert not owner.is_automation


def test_state_is_read_only_until_transitions_exist():
    owner = ControlOwner()
    with pytest.raises(AttributeError):
        owner.state = ControlOwnerState.HUMAN  # type: ignore[misc]
    for transition in ("escalate", "accept", "done", "verified", "abort"):
        assert not hasattr(owner, transition), f"{transition} arrives at step 16, not before"
