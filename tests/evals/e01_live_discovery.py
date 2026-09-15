"""E01 — genuine live discovery: preflight, one bounded run, and the A6 MUST-PASS audit.

Run as ``python -m tests.evals.e01_live_discovery --preflight`` (one minimal structured-output
request through the production client, no Legacy Bank content) or ``... --live [--evidence-root
DIR]`` (one bounded discovery: real Legacy Bank, real Chromium, real ActionGate, real
EvidenceRecorder, real OpenAIResponsesClient, ``mask_bound_inputs=True``, entry route only).

Privacy invariant of the live audit: the real outbound HTTP request bodies captured through an
``httpx2`` request hook are kept IN MEMORY ONLY — never persisted, printed or returned; only
booleans derived from them are reported. Authorization headers are never captured. What is
persisted is the redacted discovery evidence written by the ``EvidenceRecorder`` — nothing else.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import httpx2

ROOT = Path(__file__).resolve().parents[2]
ENTRY_ROUTES = ("/members/search",)
DEFAULT_PORT = 8000
MEMBER_INPUT = "member_id"
DEFAULT_MEMBER = "M1001"
_REF_TOKEN = re.compile(r"(?<![A-Za-z0-9_])(?:f\d+)?e\d+(?![A-Za-z0-9_])")


def _opted_in() -> bool:
    return os.environ.get("CUA_LIVE_API") == "1" and bool(os.environ.get("OPENAI_API_KEY"))


# --- in-memory capture (never persisted) ----------------------------------------------------------


class BodyCapture:
    """httpx2 request hook: keeps the outbound JSON bodies in memory. No headers, no URL."""

    def __init__(self) -> None:
        self.bodies: list[str] = []

    def __call__(self, request: httpx2.Request) -> None:
        request.read()
        self.bodies.append(request.content.decode("utf-8", errors="replace"))


class RecordingLLM:
    """Wraps the real client; keeps every DiscoveryRequest in memory for the boundary audit."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.requests = []
        self.replies = []

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    def decide(self, request):
        self.requests.append(request)
        reply = self._inner.decide(request)
        self.replies.append(reply)
        return reply


# --- preflight ------------------------------------------------------------------------------------


def preflight(model_id: str | None = None) -> dict:
    """ONE minimal structured-output request through the production adapter. Sends no Legacy Bank
    content, no member id, no artifact data: a neutral goal on a blank observation."""
    import openai

    from cua.discovery import to_observation
    from cua.domain import SurfaceSnapshot
    from cua.llm import (
        DiscoveryRequest,
        GoalStatement,
        ModelOutputError,
        ProviderError,
    )
    from cua.llm.openai_client import TRANSPORT_RETRIES, OpenAIResponsesClient

    started = time.monotonic()
    try:
        client = OpenAIResponsesClient(model_id=model_id, call_timeout_s=60.0)
    except ProviderError as exc:
        return _provider_failure("preflight", exc, openai.__version__)
    blank = SurfaceSnapshot(
        url="about:blank", page_title="", step_index=1, elements=[], visible_text_outline=""
    )
    request = DiscoveryRequest(
        goal=GoalStatement(
            name="preflight_check",
            description=(
                "Connectivity check only. There is nothing to do on this blank page: answer "
                "with kind FINISH and intent_summary 'preflight ok'."
            ),
            inputs=[],
            outputs=[],
        ),
        observation=to_observation(blank, None),
        step_index=1,
        steps_remaining=0,
        seconds_remaining=60.0,
        navigation_routes=[],
    )
    report = {
        "phase": "preflight",
        "sdk_version": openai.__version__,
        "model_requested": client.model_id,
        "transport_retries": client.transport_retries,
        "store_false": True,  # a constant of the adapter; proven on the wire by the mock tests
    }
    try:
        reply = client.decide(request)
    except ProviderError as exc:
        report.update(_provider_failure("preflight", exc, openai.__version__))
        return report
    except ModelOutputError as exc:
        # The provider answered a structured decision the domain rejected: availability is proven.
        report.update(
            {
                "status": "PASS",
                "note": f"structured output returned but rejected by the domain: {exc.feedback}",
                "latency_ms": int((time.monotonic() - started) * 1000),
            }
        )
        return report
    call = reply.call
    report.update(
        {
            "status": "PASS",
            "model_reported": call.model_id_reported,
            "response_id_present": bool(call.provider_response_id),
            "latency_ms": call.latency_ms,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "decision_kind": reply.decision.kind.value,
            "transport_retries": TRANSPORT_RETRIES,
        }
    )
    return report


def _provider_failure(phase: str, exc, sdk_version: str) -> dict:
    return {
        "phase": phase,
        "status": "FAIL",
        "sdk_version": sdk_version,
        "provider_error_kind": exc.kind.value,
        "http_status": exc.status_code,
        "provider_request_id": exc.provider_request_id,
        "diagnostic": str(exc),  # fixed template: kind, status, safe detail — never a body
    }


# --- the live run ---------------------------------------------------------------------------------


@dataclass
class LiveRun:
    result: object
    events: list
    events_path: Path
    requests: list
    bodies: list[str] = field(default_factory=list)  # in memory only
    config: object = None
    policy_source: str = ""
    base_url: str = ""
    seed_balance: Decimal | None = None


def run_live(
    evidence_root: Path,
    *,
    member_id: str = DEFAULT_MEMBER,
    model_id: str | None = None,
    port: int = DEFAULT_PORT,
    max_steps: int = 25,
    timeout_s: float = 300.0,
) -> LiveRun:
    from werkzeug.serving import make_server

    from cua.discovery import (
        READ_SAVINGS_BALANCE_GOAL,
        DiscoveryAgent,
        DiscoveryConfig,
        DiscoveryDeps,
        assert_navigation_routes_within_policy,
    )
    from cua.evidence import EvidenceRecorder, EvidenceStore, RunKind
    from cua.hitl import ControlOwner
    from cua.llm.openai_client import OpenAIResponsesClient
    from cua.policy import ActionGate, PolicyConfig
    from cua.replay import MonotonicClock
    from cua.surface.playwright_surface import PlaywrightSurface
    from legacy_bank import create_app
    from legacy_bank.data import SAVINGS, Bank
    from tests.replay.support import policy_for

    app = create_app(fault_mode=None)
    try:
        server = make_server("127.0.0.1", port, app, threaded=True)
        policy = PolicyConfig.from_json_file(ROOT / "policy" / "legacy_bank.json")
        policy_source = "policy/legacy_bank.json (verbatim)"
    except OSError:
        server = make_server("127.0.0.1", 0, app, threaded=True)
        policy = None
        policy_source = "policy/legacy_bank.json rules re-homed on an ephemeral origin"
    base_url = f"http://127.0.0.1:{server.server_port}"
    if policy is None:
        policy = policy_for(base_url)
    assert_navigation_routes_within_policy(ENTRY_ROUTES, policy)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    capture = BodyCapture()
    http_client = httpx2.Client(event_hooks={"request": [capture]})
    client = RecordingLLM(OpenAIResponsesClient(model_id=model_id, http_client=http_client))
    store = EvidenceStore(evidence_root)
    recorder = EvidenceRecorder(store.open_run)
    config = DiscoveryConfig(
        base_url=base_url,
        navigation_routes=ENTRY_ROUTES,
        max_steps=max_steps,
        timeout_s=timeout_s,
        mask_bound_inputs=True,
    )
    surface = PlaywrightSurface(headless=True, listener=recorder).open()
    try:
        gate = ActionGate(policy, ControlOwner(), observer=recorder)
        agent = DiscoveryAgent(
            DiscoveryDeps(
                surface=surface,
                action_gate=gate,
                clock=MonotonicClock(),
                evidence=recorder,
                llm=client,
            ),
            config,
        )
        result = agent.run(READ_SAVINGS_BALANCE_GOAL, {MEMBER_INPUT: member_id})
    finally:
        surface.close()
        server.shutdown()
        thread.join(timeout=5)
    events_path = store.events_path(RunKind.DISCOVERY, result.run_id)
    events = store.read_events(events_path) if events_path.exists() else []
    seed = Bank().find_member(member_id)
    return LiveRun(
        result=result,
        events=events,
        events_path=events_path,
        requests=client.requests,
        bodies=capture.bodies,
        config=config,
        policy_source=policy_source,
        base_url=base_url,
        seed_balance=seed.balances[SAVINGS] if seed else None,
    )


# --- the A6 audit ---------------------------------------------------------------------------------


def audit(run: LiveRun, *, member_id: str = DEFAULT_MEMBER) -> dict:
    from cua.discovery import StopReason
    from cua.discovery.summaries import trace_summary
    from cua.domain import ActionType
    from cua.evidence import EventType

    result = run.result
    events = run.events
    raw = run.events_path.read_text(encoding="utf-8") if run.events_path.exists() else ""
    lines = [json.loads(line) for line in raw.splitlines()] if raw else []
    key = os.environ.get("OPENAI_API_KEY", "")
    model_requested = run.result.model_id
    calls = [e for e in events if e.event_type is EventType.MODEL_CALL]
    decision_calls = [c for c in calls if c.payload.outcome == "DECISION"]
    dispatched = [e for e in events if e.event_type is EventType.ACTION_DISPATCHED]
    gates = [e for e in events if e.event_type is EventType.GATE_DECISION]
    reads = [
        s
        for s in result.trace.steps
        if s.action_type is ActionType.READ and s.output_name == "savings_balance"
    ]
    read = reads[0] if len(reads) == 1 else None
    ended = events[-1] if events and events[-1].event_type is EventType.DISCOVERY_ENDED else None
    started = events[0] if events and events[0].event_type is EventType.DISCOVERY_STARTED else None

    def preceded_by_allow(i: int) -> bool:
        prev = events[i - 1]
        return (
            prev.event_type is EventType.GATE_DECISION
            and prev.payload.decision == "ALLOW"
            and prev.payload.action_type == events[i].payload.action_type
        )

    must_pass = {
        "stop_reason_goal_reached": result.stop_reason is StopReason.GOAL_REACHED,
        "verifier_satisfied": bool(result.verdict and result.verdict.satisfied),
        "output_matches_seed_oracle": (
            run.seed_balance is not None
            and result.outputs.get("savings_balance") == run.seed_balance
            and type(result.outputs.get("savings_balance")) is Decimal
        ),
        "provider_is_openai": result.provider == "openai"
        and all(c.payload.provider == "openai" for c in calls),
        "model_provenance_consistent": all(
            c.payload.model_id_requested == model_requested
            and (c.payload.model_id_reported or "").startswith(model_requested)
            for c in decision_calls
        )
        and bool(decision_calls),
        "response_ids_on_decisions": all(
            bool(c.payload.provider_response_id) for c in decision_calls
        ),
        "multiple_iterative_decisions": len(decision_calls) >= 2,
        "verified_read_is_unique_savings_cell": bool(
            read
            and read.target
            and read.target.role == "cell"
            and any(
                seg.startswith("row: ") and seg[5:].casefold() == "savings"
                for seg in (read.target.context_hint or "").split(" > ")
            )
            and read.identity_matches == 1
            and read.output_name == "savings_balance"
        ),
        "every_dispatch_preceded_by_allow": bool(dispatched)
        and all(
            preceded_by_allow(i)
            for i, e in enumerate(events)
            if e.event_type is EventType.ACTION_DISPATCHED
        ),
        "no_deny_or_intervention_dispatched": not any(g.payload.decision != "ALLOW" for g in gates),
        "fill_persisted_as_placeholder": any(
            d.payload.action_type == "FILL" and d.payload.value == f"<input:{MEMBER_INPUT}>"
            for d in dispatched
        )
        and not any(d.payload.value == member_id for d in dispatched),
        "member_id_absent_from_evidence": bool(raw) and member_id not in raw,
        "no_ref_key_or_token_in_evidence": bool(raw)
        and '"ref"' not in raw
        and not _REF_TOKEN.search(raw),
        "every_line_redacted": bool(lines) and all(ln["redaction_applied"] is True for ln in lines),
        "schema_version_1_1": bool(lines) and all(ln["schema_version"] == "1.1" for ln in lines),
        "single_session_id": len({e.session_id for e in events}) == 1
        and events[0].session_id == result.session_id,
        "discovery_ended_goal_satisfied": bool(ended and ended.payload.goal_satisfied),
        "persisted_trace_equals_in_memory": bool(
            ended
            and ended.payload.trace.model_dump(mode="json")
            == trace_summary(result.trace).model_dump(mode="json")
        ),
        "member_id_absent_from_model_requests": bool(run.requests)
        and all(member_id not in r.model_dump_json() for r in run.requests),
        "member_id_absent_from_provider_bodies": bool(run.bodies)
        and all(member_id not in b for b in run.bodies),
        "api_key_absent_from_provider_bodies": bool(key)
        and bool(run.bodies)
        and all(key not in b for b in run.bodies),
        "mask_bound_inputs_true": run.config.mask_bound_inputs is True
        and bool(started and started.payload.mask_bound_inputs is True),
        "store_false_on_every_body": bool(run.bodies)
        and all(json.loads(b).get("store") is False for b in run.bodies),
    }

    sequence = [
        (
            s.action_type.value,
            (s.route_template or "")
            if s.action_type is ActionType.NAVIGATE
            else f"{s.target.role}:{s.target.accessible_name or ''}"
            if s.target
            else "",
        )
        for s in result.trace.steps
    ]
    types = [s.action_type.value for s in result.trace.steps]
    fill_then_click = any(
        types[i] == "FILL" and types[i + 1] == "CLICK" for i in range(len(types) - 1)
    )
    invalid = [
        (c.step_index, c.payload.purpose, c.payload.validation.code)
        for c in calls
        if c.payload.validation and c.payload.validation.status == "INVALID"
    ]
    recorded = {
        "run_id": result.run_id,
        "stop_reason": result.stop_reason.value,
        "stop_code": result.detail.code.value,
        "stop_message": result.detail.message,
        "action_sequence": sequence,
        "dispatched_actions": len(dispatched),
        "navigate_count": types.count("NAVIGATE"),
        "read_count": types.count("READ"),
        "fill_immediately_before_click": fill_then_click,
        "steps_to_goal": len(result.trace.steps),
        "observations": sum(1 for e in events if e.event_type is EventType.OBSERVATION),
        "model_calls": result.model_calls,
        "corrective_retries": result.corrective_retries,
        "validator_rejections": invalid,
        "ambiguity_events": sum(1 for _, _, code in invalid if code == "AMBIGUOUS_TARGET"),
        "gate_decisions": [
            (g.payload.action_type, g.payload.decision, g.payload.risk) for g in gates
        ],
        "requests_with_placeholder": sum(
            1 for r in run.requests if f"<input:{MEMBER_INPUT}>" in r.model_dump_json()
        ),
        "masking_rejections": [i for i in invalid if i[2] == "VALUE"],
        "latency_ms_total": sum(c.payload.latency_ms or 0 for c in calls),
        "input_tokens_total": sum(c.payload.input_tokens or 0 for c in calls),
        "output_tokens_total": sum(c.payload.output_tokens or 0 for c in calls),
        "model_reported": next((c.payload.model_id_reported for c in decision_calls), None),
        "typed_output": {k: str(v) for k, v in result.outputs.items()},
        "output_type": {k: type(v).__name__ for k, v in result.outputs.items()},
        "evidence_path": str(run.events_path),
        "events": len(events),
        "policy_source": run.policy_source,
        "base_url": run.base_url,
    }
    return {
        "must_pass": must_pass,
        "all_must_pass": all(must_pass.values()),
        "recorded": recorded,
        "classification": None if all(must_pass.values()) else classify(result, invalid),
    }


def classify(result, invalid) -> str:
    from cua.discovery import StopCode, StopReason

    code = result.detail.code
    if result.stop_reason is StopReason.GOAL_REACHED:
        return "EVIDENCE"  # goal reached but an evidence/privacy invariant failed
    if code is StopCode.PROVIDER_ERROR:
        return "PROVIDER"
    if code is StopCode.EVIDENCE_ERROR:
        return "EVIDENCE"
    if code is StopCode.SURFACE_ERROR:
        return "SURFACE"
    if code in (StopCode.POLICY_DENIED, StopCode.INTERVENTION_REQUIRED):
        return "POLICY"
    if code is StopCode.AMBIGUOUS_TARGET:
        return "VALIDATOR"
    if code is StopCode.INVALID_DECISION:
        codes = set(result.detail.validation_codes)
        if codes & {"SHAPE"}:
            return "PROMPT_SCHEMA"
        if codes & {"VALUE"}:
            return "MASKING"
        if codes & {"PREMATURE_FINISH"}:
            return "GOAL_VERIFIER"
        return "VALIDATOR"
    if code in (
        StopCode.MAX_STEPS,
        StopCode.NO_PROGRESS,
        StopCode.REPORTED_BLOCKED,
        StopCode.TIMEOUT,
    ):
        return "MODEL_BEHAVIOUR"
    return "OTHER"


# --- CLI ------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--evidence-root", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)
    if not _opted_in():
        print("refusing: set CUA_LIVE_API=1 and OPENAI_API_KEY to make real provider requests")
        return 2
    if args.preflight:
        report = preflight(args.model)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("status") == "PASS" else 1
    if args.live:
        root = Path(args.evidence_root) if args.evidence_root else Path(tempfile.mkdtemp())
        run = run_live(root, model_id=args.model)
        report = audit(run)
        print(json.dumps(report, indent=2, default=str))  # metrics and booleans only; no bodies
        return 0 if report["all_must_pass"] else 1
    parser.error("choose --preflight or --live")
    return 2


if __name__ == "__main__":
    sys.exit(main())
