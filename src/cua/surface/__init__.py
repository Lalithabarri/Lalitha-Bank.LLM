"""surface/ — Driver, observe/act, session_id, ACTION_DISPATCHED.

Owns: the ``Surface`` contract and ``PlaywrightSurface`` (ARCHITECTURE §3, D04, D05).
Must never: leak Playwright types upward.

Implemented at ARCHITECTURE §15 step 3.
"""
