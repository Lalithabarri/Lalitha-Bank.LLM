"""E02 — the official E01 discovery record, compiled, replayed on a different member with zero model
decisions (ARCHITECTURE §10 eval matrix; §15 step 14).

    official E01 persisted record -> ArtifactCompiler -> generated CapabilityArtifact
    -> persisted + reloaded through ArtifactStore
    -> fresh-interpreter replay on M1002 under the zero-model guard (which also forbids the
       compiler) -> SUCCESS, savings_balance == Decimal("4120.75")

Run as ``python -m tests.evals.e02_compile_replay [--capabilities-root DIR] [--evidence-root DIR]
[--compile-only]``. Neither the compiler nor the replay ever receives the handwritten Milestone 4
artifact; it is loaded here only to assert the generated one differs from it. No provider SDK is
involved anywhere: this eval runs offline, in the normal suite, with the loopback socket guard.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from cua.artifact import ArtifactStore, CapabilityArtifact, InputRef, ProvenanceSource
from cua.artifact.compile_report import CompileReport, CompileSuccess
from cua.artifact.compiler import COMPILER_VERSION, ArtifactCompiler
from cua.discovery.capabilities import READ_SAVINGS_BALANCE_DECLARATION
from cua.evidence import DiscoveryEndedPayload, EventType, EvidenceStore, RunKind

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_E01_RUN_ID = "run_e49e4d0cbe09"
OFFICIAL_MODEL = "gpt-5.6-sol"
CAPABILITY_VERSION = "1.0.0"
ARTIFACT_ID = f"read_savings_balance@{CAPABILITY_VERSION}"
HANDWRITTEN_ROOT = ROOT / "capabilities"
REPLAY_MEMBER = "M1002"
EXPECTED_BALANCE = Decimal("4120.75")
DISCOVERY_MEMBER = "M1001"
# Values the generated files must never carry: the discovery member, the replay member, the
# not-found member, and the balances observed at discovery / expected at replay.
FORBIDDEN_LITERALS = ("M1001", "M1002", "M404", "15275", "15,275", "4120", "4,120", "$")
_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")
SELECTOR_MARKERS = ("css=", "xpath=", "//", "text=", "<input:", "eval(", "<script")
REPLAY_CHRONOLOGY = (
    [EventType.RUN_STARTED]
    + [
        EventType.GATE_DECISION,
        EventType.ACTION_DISPATCHED,
        EventType.ACTION_COMPLETED,
    ]
    * 4
    + [EventType.RUN_COMPLETED]
)


def official_record() -> DiscoveryEndedPayload:
    store = EvidenceStore(ROOT / "evidence")
    events = store.read_events(store.events_path(RunKind.DISCOVERY, OFFICIAL_E01_RUN_ID))
    last = events[-1]
    assert last.event_type is EventType.DISCOVERY_ENDED
    assert isinstance(last.payload, DiscoveryEndedPayload)
    return last.payload


def compile_record(
    record: DiscoveryEndedPayload | None = None, *, compiled_at: datetime | None = None
) -> CompileSuccess:
    result = ArtifactCompiler().compile(
        record or official_record(),
        READ_SAVINGS_BALANCE_DECLARATION,
        capability_version=CAPABILITY_VERSION,
        compiled_at=compiled_at or datetime.now(UTC).replace(microsecond=0),
    )
    if not isinstance(result, CompileSuccess):
        raise AssertionError(f"compilation failed: {result.errors}")
    return result


@dataclass(frozen=True)
class Persisted:
    artifact_path: Path
    report_path: Path
    artifact: CapabilityArtifact  # as reloaded through the store
    report: CompileReport


def persist(compiled: CompileSuccess, capabilities_root: Path) -> Persisted:
    store = ArtifactStore(capabilities_root)
    artifact_path = store.save(compiled.artifact)
    report_path = store.save_compile_report(compiled.artifact.artifact_id, compiled.report)
    return Persisted(
        artifact_path=artifact_path,
        report_path=report_path,
        artifact=store.load_id(compiled.artifact.artifact_id),
        report=store.load_compile_report(compiled.artifact.artifact_id),
    )


def replay_generated(artifact_path: Path, evidence_root: Path, *, member: str = REPLAY_MEMBER):
    """The zero-model replay in a fresh interpreter (guard forbids the model layer, discovery
    and the compiler); returns the script's JSON report."""
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.replay.zero_model_replay",
            "--member",
            member,
            "--live",
            "--artifact",
            str(artifact_path),
            "--evidence-root",
            str(evidence_root),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


@dataclass(frozen=True)
class E02Run:
    record: DiscoveryEndedPayload
    compiled: CompileSuccess
    persisted: Persisted
    replay: dict
    replay_events_path: Path


def run_e02(capabilities_root: Path, evidence_root: Path) -> E02Run:
    record = official_record()
    compiled = compile_record(record)
    persisted = persist(compiled, capabilities_root)
    replay = replay_generated(persisted.artifact_path, evidence_root)
    return E02Run(record, compiled, persisted, replay, Path(replay["evidence_path"]))


# --- audit ----------------------------------------------------------------------------------------


def audit(run: E02Run) -> dict:
    record, compiled, persisted, replay = run.record, run.compiled, run.persisted, run.replay
    artifact = persisted.artifact
    artifact_text = persisted.artifact_path.read_text(encoding="utf-8")
    report_text = persisted.report_path.read_text(encoding="utf-8")
    generated_text = artifact_text + report_text
    handwritten = ArtifactStore(HANDWRITTEN_ROOT).load_id(ARTIFACT_ID)
    again = compile_record(record, compiled_at=compiled.artifact.provenance.compiled_at)
    doctored_record = DiscoveryEndedPayload.model_validate(
        {**record.model_dump(mode="json"), "outputs": {"savings_balance": "0.01"}}
    )
    doctored = compile_record(doctored_record, compiled_at=compiled.artifact.provenance.compiled_at)
    store = EvidenceStore(run.replay_events_path.parents[2])
    events = store.read_events(run.replay_events_path)
    replay_text = run.replay_events_path.read_text(encoding="utf-8")
    replay_lines = [json.loads(line) for line in replay_text.splitlines()]
    dispatch_preceded_by_allow = all(
        events[i - 1].event_type is EventType.GATE_DECISION
        and events[i - 1].payload.decision == "ALLOW"
        for i, e in enumerate(events)
        if e.event_type is EventType.ACTION_DISPATCHED
    )
    fill = artifact.steps[1]

    must_pass = {
        "source_run_is_official": (
            record.trace.discovery_run_id == OFFICIAL_E01_RUN_ID
            and record.stop_reason == "GOAL_REACHED"
            and record.goal_satisfied
        ),
        "compile_deterministic": again.artifact == compiled.artifact
        and again.report == compiled.report,
        "compile_ignores_discovered_outputs": doctored.artifact == compiled.artifact,
        "generated_validates_through_store": artifact == compiled.artifact
        and persisted.report == compiled.report,
        "provenance_is_discovery_e01": (
            artifact.provenance.source is ProvenanceSource.DISCOVERY
            and artifact.provenance.discovery_run_id == OFFICIAL_E01_RUN_ID
            and artifact.provenance.model_id == OFFICIAL_MODEL
            and artifact.provenance.compiler_version == COMPILER_VERSION
        ),
        "generated_differs_from_handwritten": (
            artifact != handwritten
            and handwritten.provenance.source is ProvenanceSource.HANDWRITTEN
            and [s.step_id for s in artifact.steps] != [s.step_id for s in handwritten.steps]
        ),
        "no_member_id_or_balance_in_generated": not any(
            literal in generated_text for literal in FORBIDDEN_LITERALS
        ),
        "no_ref_or_selector_in_generated": _REF_TOKEN.search(artifact_text) is None
        and not any(marker in generated_text for marker in SELECTOR_MARKERS),
        "no_model_text_in_generated": not any(
            step.intent_summary in generated_text for step in record.trace.steps
        ),
        "fill_is_input_ref_member_id": fill.value == InputRef(input_name="member_id"),
        "checkpoint_parameterized": any(
            "{member_id}" in getattr(c, "route", "") for c in artifact.success_checkpoint
        )
        and compiled.report.success_checkpoint.parameterized_by == ["member_id"],
        "known_outcome_declared_not_discovered": [
            (o.code, o.source) for o in compiled.report.known_outcomes
        ]
        == [("MEMBER_NOT_FOUND", "DECLARED")],
        "replay_status_success": replay["status"] == "SUCCESS",
        "replay_output_decimal_4120_75": replay["outputs"] == {"savings_balance": "4120.75"}
        and replay["output_types"] == {"savings_balance": "Decimal"}
        and Decimal(replay["outputs"]["savings_balance"]) == EXPECTED_BALANCE,
        "replay_zero_model": replay["forbidden_loaded"] == []
        and replay["forbidden_attempted"] == [],
        "replay_zero_provider_calls": not any(
            e.event_type in (EventType.MODEL_CALL, EventType.OBSERVATION) for e in events
        ),
        "replay_evidence_identifies_compiled_artifact": (
            events[0].event_type is EventType.RUN_STARTED
            and events[0].payload.provenance_source == "discovery"
            and events[0].payload.artifact_id == ARTIFACT_ID
            and replay["artifact_id"] == ARTIFACT_ID
            and replay["provenance_source"] == "discovery"
        ),
        "every_dispatch_preceded_by_allow": [e.event_type for e in events] == REPLAY_CHRONOLOGY
        and dispatch_preceded_by_allow,
        "artifact_unchanged_by_replay": replay["artifact_unchanged"] is True
        and persisted.artifact_path.read_text(encoding="utf-8") == artifact_text,
        "member_id_absent_from_replay_evidence": (
            REPLAY_MEMBER not in replay_text
            and "<input:member_id>" in replay_text
            and all(line["redaction_applied"] is True for line in replay_lines)
            and replay["member_in_evidence"] is False
        ),
    }
    return {
        "eval": "E02",
        "must_pass": must_pass,
        "all_must_pass": all(must_pass.values()),
        "recorded": {
            "discovery_run_id": record.trace.discovery_run_id,
            "artifact_id": artifact.artifact_id,
            "artifact_path": str(persisted.artifact_path.relative_to(ROOT))
            if persisted.artifact_path.is_relative_to(ROOT)
            else str(persisted.artifact_path),
            "report_path": str(persisted.report_path.relative_to(ROOT))
            if persisted.report_path.is_relative_to(ROOT)
            else str(persisted.report_path),
            "replay_evidence_path": str(run.replay_events_path.relative_to(ROOT))
            if run.replay_events_path.is_relative_to(ROOT)
            else str(run.replay_events_path),
            "replay_events": len(events),
            "steps": [s.step_id for s in artifact.steps],
        },
    }


# --- CLI ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capabilities-root", default=None)
    parser.add_argument("--evidence-root", default=None)
    parser.add_argument("--compile-only", action="store_true")
    args = parser.parse_args(argv)
    capabilities_root = (
        Path(args.capabilities_root) if args.capabilities_root else Path(tempfile.mkdtemp())
    )
    if args.compile_only:
        persisted = persist(compile_record(), capabilities_root)
        print(
            json.dumps(
                {"artifact": str(persisted.artifact_path), "report": str(persisted.report_path)}
            )
        )
        return 0
    evidence_root = Path(args.evidence_root) if args.evidence_root else Path(tempfile.mkdtemp())
    report = audit(run_e02(capabilities_root, evidence_root))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["all_must_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
