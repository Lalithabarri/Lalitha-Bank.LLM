"""policy/ — ActionGate, allowlist, risk tiers.

Owns: ``ActionGate`` — the single authorization chokepoint (ARCHITECTURE §3, §8, D09, D10).
Must never: accept LLM input. The model proposes; it never modifies policy or risk.

Implemented at ARCHITECTURE §15 step 4.
"""
