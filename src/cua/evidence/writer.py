"""EvidenceWriter — the only structured write path for run evidence (ARCHITECTURE §10, D19).

``JsonlEvidenceWriter`` appends one JSON object per line to ``events.jsonl`` for exactly one run.
Every ``write()``:

    typed event -> JSON dict -> Redactor (payload) -> re-validate -> ONE write -> flush -> fsync

Redaction happens before any byte exists on disk; there is no raw-then-redact path. The file is
opened in exclusive-create mode, so a run can never overwrite another. Each event is fsynced
because the durability of ``ACTION_DISPATCHED`` *before* the driver call is the point of that
event. Any failure — cannot open, cannot serialise, redactor refused, cannot append — is an
``EvidenceError`` (original chained); nothing is retried here and nothing is swallowed.

``EvidenceStore`` owns the on-disk layout::

    <root>/<run_kind>/<run_id>/events.jsonl     e.g. evidence/replay/run_3f9a1c2b7d4e/events.jsonl

and reads a file back, failing loudly on a malformed line or an unredacted event.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import ValidationError

from cua.evidence.events import ArtifactRef, EventType, EvidenceError, EvidenceEvent, RunKind
from cua.evidence.redaction import Redactor

EVENTS_FILE = "events.jsonl"
ARTIFACTS_DIR = "artifacts"
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")


class EvidenceSink(Protocol):
    """Where a recorder sends events. Persists only the redacted representation."""

    def write(self, event: EvidenceEvent) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class ArtifactSink(Protocol):
    """Optional sink capability (1.2): store one binary evidence artifact beside the events —
    intervention screenshots. Returns a safe run-relative reference; bytes never enter JSONL."""

    def write_artifact(self, name: str, data: bytes, media_type: str) -> ArtifactRef: ...


def artifact_ref(name: str, data: bytes, media_type: str) -> ArtifactRef:
    """The reference every sink must return for ``name``/``data`` (shared by fakes)."""
    if not _ARTIFACT_NAME.match(name):
        raise EvidenceError(f"not a safe artifact name: {name!r}")
    return ArtifactRef(
        path=f"{ARTIFACTS_DIR}/{name}",
        sha256=hashlib.sha256(data).hexdigest(),
        media_type=media_type,
        size_bytes=len(data),
    )


def redacted_form(event: EvidenceEvent, redactor: Redactor) -> EvidenceEvent:
    """The event as it may be persisted: payload redacted, ``redaction_applied`` set.

    Envelope fields are system-generated identifiers and enums (never observed text), so only the
    payload is rewritten. The result is re-validated so a placeholder can never break the schema.
    Raises ``EvidenceError`` when the event cannot be represented.
    """
    try:
        data = event.model_dump(mode="json")
        data["payload"] = redactor.redact(data["payload"])
        data["redaction_applied"] = True
        return EvidenceEvent.model_validate(data)
    except (TypeError, ValueError) as exc:  # ValidationError is a ValueError
        raise EvidenceError(
            f"cannot serialise {event.event_type.value} event: {exc}", event_type=event.event_type
        ) from exc


class JsonlEvidenceWriter:
    def __init__(self, path: Path, redactor: Redactor) -> None:
        self._path = Path(path)
        self._redactor = redactor
        self._events_written = 0
        self._closed = False
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self._path, "x", encoding="utf-8")  # noqa: SIM115 - long-lived handle
        except OSError as exc:
            raise EvidenceError(f"cannot create evidence file {self._path}: {exc}") from exc

    @property
    def path(self) -> Path:
        return self._path

    @property
    def events_written(self) -> int:
        return self._events_written

    def write(self, event: EvidenceEvent) -> None:
        if self._closed:
            raise EvidenceError(
                f"evidence file {self._path} is closed; refusing {event.event_type.value}",
                event_type=event.event_type,
            )
        line = redacted_form(event, self._redactor).model_dump_json() + "\n"
        try:
            self._fh.write(line)
            self._fh.flush()
            os.fsync(self._fh.fileno())
        except OSError as exc:
            raise EvidenceError(
                f"cannot append {event.event_type.value} event to {self._path}: {exc}",
                event_type=event.event_type,
            ) from exc
        self._events_written += 1

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._fh.flush()
            os.fsync(self._fh.fileno())
            self._fh.close()
        except OSError as exc:
            raise EvidenceError(f"cannot close evidence file {self._path}: {exc}") from exc

    def write_artifact(self, name: str, data: bytes, media_type: str) -> ArtifactRef:
        """Store ``data`` at ``<run dir>/artifacts/<name>`` (never overwriting) and fsync it."""
        if self._closed:
            raise EvidenceError(f"evidence file {self._path} is closed; refusing artifact {name}")
        ref = artifact_ref(name, data, media_type)
        target = self._path.parent / ref.path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise EvidenceError(f"cannot store evidence artifact {ref.path}: {exc}") from exc
        return ref


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    @property
    def root(self) -> Path:
        return self._root

    def run_dir(self, run_kind: RunKind, run_id: str) -> Path:
        if "/" in run_id or "\\" in run_id or run_id in ("", ".", ".."):
            raise ValueError(f"not a run id: {run_id!r}")
        return self._root / run_kind.value.lower() / run_id

    def events_path(self, run_kind: RunKind, run_id: str) -> Path:
        return self.run_dir(run_kind, run_id) / EVENTS_FILE

    def open_run(self, run_kind: RunKind, run_id: str, redactor: Redactor) -> JsonlEvidenceWriter:
        return JsonlEvidenceWriter(self.events_path(run_kind, run_id), redactor)

    @staticmethod
    def read_events(path: Path) -> list[EvidenceEvent]:
        """Load a run's events. Malformed or unredacted lines are errors, never skipped."""
        events: list[EvidenceEvent] = []
        try:
            text = Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise EvidenceError(f"cannot read evidence file {path}: {exc}") from exc
        for number, line in enumerate(text.splitlines(), start=1):
            try:
                event = EvidenceEvent.model_validate_json(line)
            except ValidationError as exc:
                raise EvidenceError(f"{path}:{number}: malformed evidence line: {exc}") from exc
            if not event.redaction_applied:
                raise EvidenceError(f"{path}:{number}: event was persisted without redaction")
            events.append(event)
        return events


def event_types(events: list[EvidenceEvent]) -> list[EventType]:
    """Convenience for tests and demos: the chronology as a list of event types."""
    return [event.event_type for event in events]
