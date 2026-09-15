"""hitl/ — ControlOwner, intervention, hand-back.

Owns: ``ControlOwner`` state machine, ``InterventionRequest``, ``HumanActionRecord``
(ARCHITECTURE §3, §9, D18).
Must never: convert DENY into approval.

Implemented so far: the ownership states and holder (step 4). Transitions, intervention
requests, and hand-back verification arrive at ARCHITECTURE §15 step 16.
"""

from cua.hitl.control import ControlOwner, ControlOwnerState

__all__ = ["ControlOwner", "ControlOwnerState"]
