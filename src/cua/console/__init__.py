"""console/ — Reviewer UI (adapter only).

Owns: the LegacyBank Capability Console, a thin adapter over ``CapabilityRunner``,
``RunResult``, ``CapabilityArtifact``, evidence, and ``ControlOwner`` (ARCHITECTURE §3, §13, D22).
Must never: contain automation logic — none in templates or frontend JS either.

Implemented at ARCHITECTURE §15 step 20.
"""
