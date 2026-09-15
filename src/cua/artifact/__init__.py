"""artifact/ — Schema, compiler, store, invariants I1–I6.

Owns: ``CapabilityArtifact``, ``ArtifactStore``, ``ArtifactCompiler`` (ARCHITECTURE §3, §6).
Must never: hold secrets or approvals.

Implemented at ARCHITECTURE §15 steps 5 and 13.
"""
