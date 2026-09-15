"""A clock that only moves when someone sleeps. Deadline tests run in zero wall time."""


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self._now = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds
