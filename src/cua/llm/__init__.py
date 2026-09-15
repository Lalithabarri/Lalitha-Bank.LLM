"""llm/ — Gemini client, structured output, one corrective retry.

Owns: ``LLMClient`` (ARCHITECTURE §3, D06).
Must never: be imported by replay. Reachable ONLY from discovery/ (§4).

Implemented at ARCHITECTURE §15 step 10.
"""
