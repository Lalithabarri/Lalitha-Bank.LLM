"""The replay engine's only source of time.

Deadlines and polling go through an injected ``Clock`` so that every bounded wait is
deterministic under test (a fake clock advances on ``sleep``) and the engine never reaches for
``time`` directly.
"""

import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Monotonic seconds. Only differences are meaningful."""
        ...

    def sleep(self, seconds: float) -> None: ...


class MonotonicClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)
