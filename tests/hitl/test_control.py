import pytest

from cua.hitl import ControlOwner, ControlOwnerState, IllegalTransition


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


def test_state_is_not_assignable():
    owner = ControlOwner()
    with pytest.raises(AttributeError):
        owner.state = ControlOwnerState.HUMAN  # type: ignore[misc]


# --- Milestone 8: the four edges of ARCHITECTURE §9 and nothing else (H1) ----------------------

EDGES = {
    "escalate": (ControlOwnerState.AUTOMATION, ControlOwnerState.PENDING_HUMAN),
    "accept": (ControlOwnerState.PENDING_HUMAN, ControlOwnerState.HUMAN),
    "hand_back": (ControlOwnerState.HUMAN, ControlOwnerState.RETURNING),
    "restore": (ControlOwnerState.RETURNING, ControlOwnerState.AUTOMATION),
}


def test_the_full_handoff_cycle_and_no_human_to_automation_shortcut():
    owner = ControlOwner()
    owner.escalate()
    owner.accept()
    assert owner.state is ControlOwnerState.HUMAN and not owner.is_automation
    with pytest.raises(IllegalTransition):
        owner.restore()  # HUMAN -> AUTOMATION does not exist
    owner.hand_back()
    assert owner.state is ControlOwnerState.RETURNING and not owner.is_automation
    owner.restore()
    assert owner.is_automation


@pytest.mark.parametrize("edge", sorted(EDGES))
@pytest.mark.parametrize("start", list(ControlOwnerState))
def test_every_edge_is_legal_from_exactly_its_source_state(edge, start):
    owner = ControlOwner(start)
    source, target = EDGES[edge]
    if start is source:
        getattr(owner, edge)()
        assert owner.state is target
    else:
        with pytest.raises(IllegalTransition, match=edge):
            getattr(owner, edge)()
        assert owner.state is start  # an illegal edge changes nothing
