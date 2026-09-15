"""cua — LLM discovers once; a deterministic engine replays forever.

Module boundaries are fixed by ARCHITECTURE.md §3/§4. Dependency direction:

    cli -> runner -> {discovery, replay} -> action_gate -> surface
    artifact <- compiler <- normalized_trace <- discovery
    policy, hitl, evidence: depended upon, never depend upward
    llm/: reachable ONLY from discovery/

``cua`` never imports ``legacy_bank`` — the target is only ever seen through its UI.
"""
