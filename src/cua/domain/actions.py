"""The closed action vocabulary (ARCHITECTURE §5, D08).

No generated code, no arbitrary selectors: an unconstrained action space would defeat policy
enforcement. Which of these may be persisted into an artifact is decided by artifact/ at its
own milestone (ARCHITECTURE §6).
"""

from enum import StrEnum


class ActionType(StrEnum):
    CLICK = "CLICK"
    FILL = "FILL"
    SELECT = "SELECT"
    NAVIGATE = "NAVIGATE"
    READ = "READ"
    WAIT = "WAIT"
    FINISH = "FINISH"
    REPORT_BLOCKED = "REPORT_BLOCKED"
