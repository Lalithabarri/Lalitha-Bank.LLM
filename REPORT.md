# REPORT

> **Status: skeleton (Milestone 6 frozen).** Every statement below is backed by committed code,
> tests or evidence at `99b7291`. Sections marked **[NOT YET IMPLEMENTED]** or **[TODO]** describe
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
NormalizedTrace -> ArtifactCompiler [NOT YET IMPLEMENTED] -> CapabilityArtifact -> ArtifactStore
All components -> EvidenceRecorder -> Redactor -> evidence/<run_kind>/<run_id>/events.jsonl
```

Implemented and verified: target app (M1); `Surface` contract + `PlaywrightSurface` with
observation-local refs (M2); `ActionGate` deny-by-default allowlist, risk tiers, `ControlOwner`
states, `CapabilityArtifact` schema + store, handwritten `read_savings_balance@1.0.0` (M3); binding,
semantic `TargetResolver`, conditions, `RunResult`, `ReplayEngine` (M4); evidence envelope,
`Redactor`, JSONL writer, recorder (M5); provider-neutral `LLMClient`, OpenAI Responses adapter,
model-safe observations, validator, ambiguity guard, goal verifier, normalized trace, discovery loop,
official live E01 (M6). Perception is accessibility-shaped (role + accessible name + table/row/group
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

**[NOT YET IMPLEMENTED]** `ArtifactCompiler`: the M6 `NormalizedTrace` (literal-free by
construction — FILL recorded as `INPUT_REF(member_id)`, routes as `/members/{member_id}`, targets as
role + context with the content name dropped) is produced and persisted, but no artifact has been
compiled from it yet. Next milestone.

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

**[TODO]** E02 (discover → compile → replay on a different member) once the compiler exists;
failure-mode table.

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

**Full same-session HITL is not yet implemented.** What exists: `ControlOwner` with the four states
(`AUTOMATION | PENDING_HUMAN | HUMAN | RETURNING`); `ActionGate` denies every automated action while
the owner is not `AUTOMATION` (`CONTROL_NOT_OWNED`, tested for all three states, in replay and
discovery); `IRREVERSIBLE` actions (the "Confirm transfer" click) always answer
`REQUIRE_INTERVENTION` and are never dispatched — in V1 both replay and discovery stop safely
(`INTERVENTION_REQUIRED` / `BLOCKED_BY_POLICY`) rather than suspend. `DENY` is never converted into a
request for approval. The wrapper-generated `session_id` names one browser session and is single-use,
which is the mechanism the same-session proof will rest on.

**[NOT YET IMPLEMENTED]** pause → cede → human acts in the same live browser → explicit hand-back →
re-observe and verify → never repeat a completed irreversible action → resume/terminate;
`InterventionRequest` / `HumanActionRecord` evidence; E08/E09.

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

Deliberate, at clean seams: no ArtifactCompiler yet (next); no same-session HITL transitions yet
(after the compiler); no CLI/`CapabilityRunner` (runs are driven from tests and `tests/evals`); no
screenshots; no reviewer console; no `WAIT` action; no automatic re-dispatch retries; no
self-healing replay (refused, D21); no Gemini/Anthropic adapters (D24 keeps the seam); no tenant
overlays or desktop surface (designed only); no queues, DB, auth, telemetry. Known limits are listed
per milestone in PROJECT_STATUS.md.

**[TODO]** Final cut list at submission.
