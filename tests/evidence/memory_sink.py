"""In-memory evidence for tests: the same redaction as the JSONL writer, no disk.

``MemorySink`` keeps every event in the form the writer would have persisted (redacted,
``redaction_applied=True``) plus the exact JSON line. ``MemoryEvidence`` is the ``SinkOpener``
the recorder calls once per run; it remembers every sink it opened and can be told to fail on a
given event type (before the write — nothing is kept for that event) or to refuse to open.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from cua.evidence import (
    EventType,
    EvidenceError,
    EvidenceEvent,
    EvidenceSink,
    Redactor,
    RunKind,
    redacted_form,
)

FailWhen = Callable[[EvidenceEvent], bool]


class MemorySink:
    def __init__(
        self,
        redactor: Redactor,
        *,
        fail_on: Iterable[EventType] = (),
        fail_when: FailWhen | None = None,
        fail_close: bool = False,
    ) -> None:
        self._redactor = redactor
        self.fail_on = set(fail_on)
        self.fail_when = fail_when
        self.fail_close = fail_close
        self.events: list[EvidenceEvent] = []
        self.lines: list[str] = []
        self.raw_seen: list[EvidenceEvent] = []  # what the recorder handed over (unredacted)
        self.closed = False

    def write(self, event: EvidenceEvent) -> None:
        self.raw_seen.append(event)
        if self.closed:
            raise EvidenceError("memory sink is closed", event_type=event.event_type)
        if event.event_type in self.fail_on or (self.fail_when and self.fail_when(event)):
            raise EvidenceError(f"simulated disk failure on {event.event_type.value}")
        persisted = redacted_form(event, self._redactor)
        self.lines.append(persisted.model_dump_json())
        self.events.append(persisted)

    def close(self) -> None:
        if self.fail_close and not self.closed:
            self.closed = True
            raise EvidenceError("simulated close failure")
        self.closed = True

    # --- convenience -----------------------------------------------------------------------

    @property
    def types(self) -> list[EventType]:
        return [e.event_type for e in self.events]

    @property
    def text(self) -> str:
        """Exactly the bytes a JSONL file would hold."""
        return "".join(line + "\n" for line in self.lines)

    def of(self, event_type: EventType) -> list[EvidenceEvent]:
        return [e for e in self.events if e.event_type is event_type]


class MemoryEvidence:
    """A ``SinkOpener`` that keeps every sink it opened."""

    def __init__(
        self,
        *,
        fail_on: Iterable[EventType] = (),
        fail_when: FailWhen | None = None,
        fail_open: bool = False,
        fail_close: bool = False,
    ) -> None:
        self.fail_on = set(fail_on)
        self.fail_when = fail_when
        self.fail_open = fail_open
        self.fail_close = fail_close
        self.opened: list[tuple[RunKind, str, Redactor]] = []
        self.sinks: list[MemorySink] = []

    def __call__(self, run_kind: RunKind, run_id: str, redactor: Redactor) -> EvidenceSink:
        self.opened.append((run_kind, run_id, redactor))
        if self.fail_open:
            raise EvidenceError(f"simulated: cannot open evidence for {run_id}")
        sink = MemorySink(
            redactor, fail_on=self.fail_on, fail_when=self.fail_when, fail_close=self.fail_close
        )
        self.sinks.append(sink)
        return sink

    @property
    def last(self) -> MemorySink:
        assert self.sinks, "no evidence run was opened"
        return self.sinks[-1]

    @property
    def events(self) -> list[EvidenceEvent]:
        return self.last.events

    @property
    def types(self) -> list[EventType]:
        return self.last.types

    @property
    def text(self) -> str:
        return self.last.text
