"""replay/ — Deterministic execution, outcomes, retries.

Owns: ``TargetResolver``, condition evaluator, ``RunResult``, ``ReplayEngine``
(ARCHITECTURE §3, §7).
Must never: import ``LLMClient`` — ``ReplayDeps`` deliberately has no LLM (D15).

Implemented at ARCHITECTURE §15 step 7.
"""
