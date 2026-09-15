"""hitl/ — ControlOwner, intervention, hand-back.

Owns: ``ControlOwner`` state machine, ``InterventionRequest``, ``HumanActionRecord``
(ARCHITECTURE §3, §9, D18).
Must never: convert DENY into approval.

Implemented at ARCHITECTURE §15 steps 4 (ControlOwner basics) and 16 (full HITL).
"""
