# REPORT

> **Status: skeleton (Milestone 8 frozen).** Every statement below is backed by committed code,
> tests or evidence on `main`. Sections marked **[NOT YET IMPLEMENTED]** or **[TODO]** describe
> work that has not happened; nothing in them is claimed. Target length when complete: 1–3 pages.

## Architecture

An LLM discovers a legacy-UI workflow **once**, under policy; a deterministic engine replays it
with **zero model decisions**; a human takes over the same live session where the system cannot
safely proceed; every claim has evidence. Single-process modular monolith (Python 3.12, Pydantic,
Playwright, Flask target), no queues/DB/infra (D03, D23).

```
CLI [TODO] -> CapabilityRunner [TODO]
      DiscoveryAgent (cua.discovery)          ReplayEngine (cua.replay)
        | LLMClient -> OpenAI adapter           | no LLM dependency (structural)
        +----------> ActionGate (cua.policy) <--+   single Surface.act caller
                         Surface (PlaywrightSurface, accessibility-first observe/act)
                         Legacy Bank Operations Console (local, synthetic, no test ids)
persisted DISCOVERY_ENDED record -> ArtifactCompiler (cua.artifact.compiler) -> CapabilityArtifact + CompileReport -> ArtifactStore
All components -> EvidenceRecorder -> Redactor -> evidence/<run_kind>/<run_id>/events.jsonl
```

Implemented and verified: target app (M1); `Surface` contract + `PlaywrightSurface` with
observation-local refs (M2); `ActionGate` deny-by-default allowlist, risk tiers, `ControlOwner`
states, `CapabilityArtifact` schema + store, handwritten `read_savings_balance@1.0.0` (M3); binding,
semantic `TargetResolver`, conditions, `RunResult`, `ReplayEngine` (M4); evidence envelope,
`Redactor`, JSONL writer, recorder (M5); provider-neutral `LLMClient`, OpenAI Responses adapter,
model-safe observations, validator, ambiguity guard, goal verifier, normalized trace, discovery loop,
official live E01 (M6); deterministic `ArtifactCompiler` + compile report, generated
`capabilities/generated/read_savings_balance@1.0.0`, E02 replay of the generated artifact on M1002
(M7). Perception is accessibility-shaped (role + accessible name + table/row/group
context), never CSS/XPath. Dependency direction is enforced by structural tests
(`tests/cua/test_boundaries.py`).

## Artifact schema

`CapabilityArtifact` (`cua/artifact/schema.py`): typed, versioned (`schema_version` for the format,
`capability_version` semver for the capability), serializable JSON, parameterized (`inputs` with
types, values bound as explicit `LITERAL | INPUT_REF`), ordered `steps` restricted to
`NAVIGATE FILL SELECT CLICK READ`, robust `TargetDescriptor` = ordered semantic strategies (role,
name, scope = table/row/group context, text_contains) with no refs/selectors/code (I1, I3), typed
`outputs` produced by READ steps (I4), declarative `postcondition`s and a mandatory
`success_checkpoint` (ALL conditions in order), declared `known_outcomes` with detectors (business
outcome ≠ failure), per-step `risk` as policy-stamped metadata (can raise, never lower), and
`provenance` (source handwritten|discovery, model id, run id) with no transcript, key or PII (I6).
Validators reject transient refs, selectors, code, undeclared/unused inputs, unread outputs and
`WAIT`. Example: `capabilities/read_savings_balance@1.0.0.json` (handwritten, replayed in M4).

`ArtifactCompiler` (`cua/artifact/compiler.py`, M7) is a pure function of the persisted
`DISCOVERY_ENDED` record of a discovery run (the literal-free trace plus the verified stop reason —
the same bytes a reviewer reads in `evidence/discovery/<run_id>/events.jsonl`) and a declared
`CapabilityDeclaration` (name, description, typed inputs/outputs, declared known outcomes). It
compiles only a `GOAL_REACHED` record, iterates the trace in order, and derives every field by a
named rule written into a typed `CompileReport` (I1–I6 with evidence): each target is exactly the
semantic descriptor the discovery ambiguity guard resolved to one element (role + name + row/group
scope; one strategy, no fallback, never `first()`); bindings are preserved as recorded
(`INPUT_REF(member_id)`); NAVIGATE/CLICK postconditions are `route_matches` on the observed landing
path, FILL is `value_equals` on its own binding; the success checkpoint is `route_matches` on the
final observed path, parameterized because the trace's `input_evidence` proves the member by
`ROUTE`; risk is the gate's classification at discovery; provenance is `source: discovery`,
`discovery_run_id: run_e49e4d0cbe09`, `model_id: gpt-5.6-sol`, `compiler_version: 1.0.0`. It never
calls a model, never receives a bound value, never string-replaces, does no I/O, and is
structurally and behaviourally barred from reading the handwritten artifact (the compile succeeds
with `open` monkeypatched to raise and in a scratch directory holding only the evidence file).
Insufficient traces fail with a closed `CompileErrorCode` (unsuccessful record, unparameterized
input, ambiguous or unproven target, missing/duplicate output producer, transform inconsistency,
underivable success semantics, irreversible step, …); no partial artifact is ever produced. Output:
`capabilities/generated/read_savings_balance@1.0.0.json` + `compile_reports/…`, reproducible
byte-for-byte from the committed E01 evidence.

**Intentionally weaker checkpoint.** The generated artifact's success checkpoint is only
`route_matches("/members/{member_id}")`; the handwritten bootstrap additionally checks the member
heading. This is a property of evidence-driven compilation, not a defect: the trace records enough
structured evidence to justify the route (the value was read on a page whose path segment *is* the
requested member), but no heading text, so the compiler refuses to invent the stronger condition.
Replay still fails closed if the Savings cell does not resolve uniquely or does not convert.

Known business outcomes are **declared**, not discovered: a successful trace can never evidence
one (an outcome ends discovery as `REPORT_BLOCKED`), so `MEMBER_NOT_FOUND` is declared beside the
goal (`cua/discovery/capabilities.py`, text traceable to the M2 not-found capture), labelled
`DECLARED` in the report, and verified by replaying the generated artifact on M404.

## Determinism & error handling

`ReplayDeps = {surface, action_gate, clock, evidence}` — no field can hold a model; a fresh
interpreter that refuses to import `openai`, `google`, `cua.llm` or `cua.discovery` replays the
capability end to end (`tests/replay/test_zero_model.py`, live variant). Targets resolve by ordered
semantic strategies; `>1` match is `AMBIGUOUS_TARGET` and fails closed (never `first()`).
Exactly three terminal results: `SUCCESS` (typed outputs), `BUSINESS_OUTCOME` (e.g.
`MEMBER_NOT_FOUND`, detected declaratively before any generic failure), `FAILURE` with step,
expected, observed, candidates (`AMBIGUOUS_TARGET`, `TARGET_NOT_FOUND`, `POLICY_DENIED`,
`INTERVENTION_REQUIRED`, `POSTCONDITION_FAILED`, `INVALID_INPUT`, `TRANSFORM_ERROR`,
`SURFACE_ERROR`, `EVIDENCE_ERROR`). Recoverable conditions are bounded observation polls inside one
window, never a fourth status; no automatic re-dispatch exists in V1 (added only on evidence, D16).
Outputs are authoritative only on `SUCCESS`; a run whose evidence cannot be written is not a success.
Live proofs (real Chromium): M1001 → `15275.00`, M1002 → `4120.75` from the same artifact object,
M404 → `BUSINESS_OUTCOME`, ambiguity → fail closed with zero READ, empty policy → zero dispatch, dead
port → `SURFACE_ERROR`.

Discovery is bounded the same way: `max_steps=25`, `timeout_s=300` on an injected clock, stop
reasons `GOAL_REACHED | MAX_STEPS | TIMEOUT | DEAD_END | BLOCKED_BY_POLICY | MODEL_ERROR`, one
corrective retry, a deterministic no-progress dead-end rule, and a deterministic goal verifier —
the model's FINISH is only a proposal.

**E02** (`tests/evals/test_e02_compile_replay.py`, evidence `evidence/replay/run_50600b9540ca`): the
official E01 record → compiler → generated artifact, persisted and reloaded through `ArtifactStore`
→ replayed on **M1002** in a fresh interpreter whose import guard forbids `openai`, `google`,
`cua.llm`, `cua.discovery` and the compiler modules themselves → `SUCCESS`,
`savings_balance == Decimal("4120.75")`, 14 events, 4 dispatches each preceded by `GATE_DECISION
ALLOW`, zero `MODEL_CALL`s, artifact bytes unchanged, `M1002` absent from the evidence; 20/20
audit items. The same generated artifact reports M404 as `BUSINESS_OUTCOME/MEMBER_NOT_FOUND` with
the READ never dispatched.

**[TODO]** failure-mode table.

## Heterogeneity & multi-tenant

Designed, not implemented (ARCHITECTURE §11). The `Surface` Protocol is the seam between "how we
perceive/act" and "the recorded flow": observations and targets are accessibility concepts (role,
name, table/row/group context) that UIA/AX expose too, so a `DesktopAccessibilitySurface` would leave
artifact, replay, policy and evals unchanged; `PlaywrightSurface` is the only file that imports the
driver (structural test). Multi-tenant reuse: a vendor base capability plus a restricted tenant
overlay whose grammar has fields only for base origin, route aliases, locator aliases, wait tuning
and known-harmless variants — no fields for risk, policy, steps or approval, so abuse is a validation
error; drift → `FAILURE` + evidence → human-initiated rediscovery → new capability version, never
self-healing replay (D20, D21).

**[TODO]** Expand into the credible written design the assignment asks for (one page).

## Escalation & handoff

Implemented and verified live (M8, official run `evidence/replay/run_af80d82668bc`, headed
Chromium, real Legacy Bank, `transfer_funds@1.0.0`, no model). One `ControlOwner` object is shared
by the engine and `ActionGate` (identity asserted at run start) with exactly four edges:
`AUTOMATION → PENDING_HUMAN → HUMAN → RETURNING → AUTOMATION`; there is no HUMAN → AUTOMATION
shortcut. When the gate answers `REQUIRE_INTERVENTION` for the IRREVERSIBLE "Confirm transfer"
click (zero driver dispatch), the engine suspends *inside the same run*: it takes PRE evidence
(observation digest + PNG screenshot) while automation still owns control, records a typed
`InterventionRequest` (identity, step, risk, the requested action, the verification requirement —
no ref, selector, credential or model text), relinquishes ownership, and hands the request to an
`InterventionHandler` that receives nothing else — no surface, gate, page or engine — so it
physically cannot perform the action. The V1 handler is the terminal: the human clicks Confirm in
the **same visible browser, context, page and `session_id`** (never closed or recreated) and types
`done` or `abort`. Both mean "re-observe and verify": HUMAN → RETURNING, a fresh bounded
observation evaluates the step's own postcondition (`status` "The transfer has been posted."), POST
evidence is captured from the observation the verdict was made on, and `INTERVENTION_VERIFIED`
records outcome, `completed_by`, pre/post digests and screenshot references (relative path,
sha256, media type — bytes never enter the JSONL). Only `VERIFIED_COMPLETED` + `done` restores
AUTOMATION, marks the step `completed_by=HUMAN` and advances **past** it: the official chronology
shows `ACTION_DISPATCHED` for s1, s2, s3 and s5 only, then `RUN_COMPLETED / SUCCESS` with
`transfer_reference = TXN-000001` (E08 same session, E09 no repeat, one run id, one session id,
22 contiguous redacted 1.2 events). `VERIFIED_COMPLETED` + `abort` ends the run
`FAILURE / INTERVENTION_ABANDONED` (committed, not resumed). When completion cannot be proven —
remaining on the review page proves nothing — the result is `FAILURE / UNKNOWN_COMMIT_STATE`,
`safe_to_retry=false`, the owner stays RETURNING, nothing is ever retried; evidence that fails
after the human may have acted is never a reason to repeat anything (PRE evidence failure fails
closed before any ownership is granted). `DENY` is never converted into a request for approval.
Deferred: a positive "not committed" verdict (the review page carries no signal), multiple
interventions per run, a non-terminal operator interface.

## Safety

Explicit, configurable allowlist (`policy/legacy_bank.json`: origins, routes, action types) enforced
by one chokepoint on the **resolved** execution target — NAVIGATE on its real destination URL, every
other action on the observed page — never on a caller claim or the model's intent; an empty policy
denies everything. Risk tiers `SAFE_READ | REVERSIBLE_WRITE | IRREVERSIBLE` are assigned by
deterministic rules where the highest matching risk wins (a reorder can never downgrade a commit
button); the model has no risk field at all. In discovery the model only ever sees a **model-safe**
observation (known runtime inputs replaced by `<input:name>`), may only NAVIGATE to a narrow
entry-route list that is a subset of policy, and every decision is validated on the exact raw
observation it answered (stale/unknown/invented refs, illegal action/element pairs, literal inputs,
undeclared outputs, ambiguous targets, premature FINISH) before the gate sees it. Secrets: the
credential lives in the environment only, is never logged, prompted, persisted or returned in an
error; provider requests use `store=False` and zero transport retries; prompts, completions and
provider objects are never persisted. Redaction happens before any byte reaches disk: runtime inputs
persist as `<input:member_id>`, secret-shaped keys as `[REDACTED]`, URL credentials/query values
stripped, refs have no field to live in.

Official E01 (`evidence/discovery/run_e49e4d0cbe09`): `gpt-5.6-sol`, first masked attempt, 5 real
calls, 4 gated dispatches, `GOAL_REACHED`, `M1001` absent from the persisted evidence, from every
model request and from every real outbound request body (verified in memory at run time).
Documented V1 limits: derived PII in observed prose (a synthetic name in a heading) is not detected;
read-path exfiltration is not policed; pixel content is not redacted (no screenshots yet).

**[TODO]** E10 as a named eval script; `verify.sh` / `verify_live.sh`.

## Cuts

Deliberate, at clean seams: no CLI/`CapabilityRunner` (runs are driven from tests and
`tests/evals`); screenshots only as intervention evidence (no OCR, no model vision, no general
capture); no reviewer console; no `WAIT` action; no automatic re-dispatch retries; no
self-healing replay (refused, D21); no Gemini/Anthropic adapters (D24 keeps the seam); no tenant
overlays or desktop surface (designed only); no queues, DB, auth, telemetry. Compiler limits, by
design: the checkpoint carries only what the trace justifies (route, not heading — see Artifact
schema); one verified strategy per target (no descriptor minimization without an observation);
known outcomes are declared, not trace-learned (an outcome-discovery run merging `OutcomeCandidate`
is the seam); a CLICK that changes no route gets a weak-but-true route postcondition. Known limits are listed
per milestone in PROJECT_STATUS.md.

**[TODO]** Final cut list at submission.
