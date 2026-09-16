# REPORT

Every statement here is backed by committed code, tests or evidence on `main`; `scripts/verify.sh`
re-derives the offline claims, `scripts/verify_live.sh` the live ones.

## Architecture

Thesis: an LLM discovers a legacy-UI workflow **once**, under policy; a deterministic engine
replays it with **zero model decisions**; where money moves, a human takes over the **same live
session** and hands it back; every claim has evidence. Single-process modular monolith (Python
3.12, Pydantic, Playwright, Flask target) with structurally enforced boundaries — no queues, DB or
infrastructure (D03, D23).

```
DiscoveryAgent (cua.discovery) ── LLMClient → OpenAI adapter       ReplayEngine (cua.replay) — no model slot
        └──────────────► ActionGate (cua.policy) ◄──────────────────────┘   single Surface.act caller
                         Surface (accessibility-first observe/act; PlaywrightSurface is the only driver file)
                         Legacy Bank Operations Console (local, synthetic, no test ids)
persisted DISCOVERY_ENDED record → ArtifactCompiler → CapabilityArtifact + CompileReport → ArtifactStore
ControlOwner ↔ ActionGate, ReplayEngine (one shared object)        EvidenceRecorder → Redactor → events.jsonl
```

Discovery: the model sees a model-safe observation (runtime inputs templated to `<input:name>`),
proposes one typed action from a closed vocabulary, and deterministic code validates it on the raw
observation (stale/invented refs, illegal action–element pairs, literal inputs, ambiguity, premature
FINISH) before the gate; `FINISH` is a proposal — a deterministic verifier establishes
`GOAL_REACHED`; bounded by `max_steps` and a timeout. **E01**: `gpt-5.6-sol`, first masked attempt,
5 real calls, 4 gated dispatches, `GOAL_REACHED`, `savings_balance = 15275.00`, the member id absent
from every request body and from the evidence (`evidence/discovery/run_e49e4d0cbe09`). Perception
is accessibility-shaped (role + accessible name + table/row/group context), never CSS/XPath; the
dependency direction (`llm` reachable only from `discovery`; `replay` never reaches `discovery`,
`llm`, a provider SDK or the compiler) is asserted by AST and fresh-interpreter tests.

## Artifact schema

`CapabilityArtifact` (`cua/artifact/schema.py`) is data, never code: `schema_version` (format) and
`capability_version` (semver) kept separate; typed `inputs` with `required`; values bound as
explicit `LITERAL | INPUT_REF`; ordered `steps` restricted to `NAVIGATE FILL SELECT CLICK READ`;
`TargetDescriptor` = ordered semantic strategies (role, name, scope = table/row/group context,
text_contains) with no refs, selectors or driver objects; declarative `postcondition` per
mutating/navigation step, READ steps declare their typed `output`; a mandatory
`success_checkpoint` (all conditions, in order); `known_outcomes` with declarative detectors
(business outcome ≠ failure); per-step `risk` as policy-stamped metadata that can raise but never
lower the gate's classification; `provenance` (source `handwritten | discovery`, run id, model id,
compiler version) with no transcript, key or PII. Validators reject transient refs, selectors,
`WAIT`, undeclared or unused inputs and unread outputs. Two handwritten artifacts bootstrap the
engine (`read_savings_balance`, `transfer_funds`); the product is the compiled one.

`ArtifactCompiler` (`cua/artifact/compiler.py`) is a pure function of the **persisted**
`DISCOVERY_ENDED` record (verified stop reason + literal-free trace — the bytes a reviewer reads)
and a declared capability contract. It compiles only `GOAL_REACHED` records, iterates the trace in
order and derives every field by a named rule written into a typed `CompileReport` proving I1–I6:
each target is exactly the descriptor the discovery ambiguity guard resolved to one element (one
strategy, no fallback, never `first()`); bindings are preserved as recorded; NAVIGATE/CLICK
postconditions are `route_matches` on the observed landing path and FILL is `value_equals` on its
own binding; the checkpoint is `route_matches` on the final observed path, parameterized because the
trace proves the member by `ROUTE`. It never calls a model, receives a bound value, string-replaces
or does I/O, and is structurally and behaviourally barred from the handwritten artifact.
Insufficient traces fail with a closed error vocabulary; no partial artifact is ever produced.
Output `capabilities/generated/read_savings_balance@1.0.0.json` recompiles byte-for-byte from the
committed E01 record.

Two deliberate properties: the generated checkpoint is *weaker* than the handwritten one (route
only — the trace records no heading text, and the compiler refuses to invent conditions it cannot
justify; replay still fails closed on an ambiguous or non-convertible read); and known business
outcomes are *declared* beside the goal, labelled `DECLARED`, because a successful trace can never
evidence one — verified by replaying the generated artifact on M404.

## Determinism & error handling

`ReplayDeps = {surface, action_gate, clock, evidence, control_owner, intervention}` has no field
that can hold a model; a fresh interpreter that refuses to import `openai`, `google`, `cua.llm`,
`cua.discovery` and the compiler replays the generated artifact end to end. **E02**: official E01
record → compiler → artifact reloaded through the store → M1002 → `SUCCESS`,
`Decimal("4120.75")`, 14 events, 4 dispatches each preceded by `GATE_DECISION ALLOW`, artifact
bytes unchanged (`evidence/replay/run_50600b9540ca`). Targets resolve by ordered semantic
strategies on fresh observations inside one bounded window; `>1` match is `AMBIGUOUS_TARGET` and
fails closed. Known outcomes are checked on every observation before any generic failure.
Recoverable conditions are bounded polls, never a fourth status; V1 adds no automatic re-dispatch
(D16). Outputs are authoritative only on `SUCCESS`; a run whose evidence cannot be written is not a
success.

| Condition | Result |
|---|---|
| no such member (`alert`) | `BUSINESS_OUTCOME / MEMBER_NOT_FOUND`, READ never dispatched |
| two equivalent Savings cells | `FAILURE / AMBIGUOUS_TARGET`, both candidates reported, neither read |
| control not on the page within the window | `FAILURE / TARGET_NOT_FOUND` with the observed page |
| postcondition or checkpoint not established | `FAILURE / POSTCONDITION_FAILED` (expected vs observed) |
| action/route/origin off the allowlist, or owner ≠ AUTOMATION | `FAILURE / POLICY_DENIED` (+ reason), zero dispatch |
| irreversible action, no handler | `FAILURE / INTERVENTION_REQUIRED`, zero dispatch |
| bad input, unconvertible read, dead browser, evidence write failure | `INVALID_INPUT`, `TRANSFORM_ERROR`, `SURFACE_ERROR`, `EVIDENCE_ERROR` |
| human hand-back without a provable commit | `FAILURE / UNKNOWN_COMMIT_STATE`, `safe_to_retry=false`, never retried |

All of the above are live-proven in real Chromium (`tests/replay/test_replay_live.py`,
`tests/evals`). Discovery is bounded the same way (six stop reasons, one corrective retry, a
deterministic no-progress rule).

## Heterogeneity & multi-tenant

*Designed, not implemented.* The seam between "how we perceive and act" and "the recorded flow" is
the `Surface` protocol (`observe() → SurfaceSnapshot`, `act(SurfaceAction)`): observations are
accessibility concepts — role, accessible name, value, enabled, table/row/group context — and
artifacts target exactly those concepts, so nothing above the surface knows what a browser is
(`PlaywrightSurface` is the only file that imports the driver, by structural test). A
`DesktopAccessibilitySurface` over UI Automation or AX would produce the same snapshot shape from a
desktop app's accessibility tree, and the artifact schema, compiler, replay engine, policy and evals
would be unchanged; a legacy web app with poor semantics would first extend the reader
(context from headings/landmarks) rather than the artifact. Multi-tenant reuse: one vendor base
capability plus a restricted per-tenant overlay whose grammar has fields only for base origin, route
aliases, locator aliases, wait tuning and known-harmless variants — no fields for risk, policy,
steps or approval, so abuse is a validation error, not a permission; the merge is validated against
identical step count, action sequence and risk values. Drift shows up as `TARGET_NOT_FOUND` /
`AMBIGUOUS_TARGET` with evidence → human-initiated rediscovery → compile → evals → a new
`capability_version`; replay never self-heals (D21).

## Escalation & handoff

Implemented and verified live (**E08/E09**, `evidence/replay/run_af80d82668bc`, headed Chromium,
no model). One `ControlOwner` object is shared by engine and gate (identity asserted at run start)
with exactly four edges `AUTOMATION → PENDING_HUMAN → HUMAN → RETURNING → AUTOMATION`. When the gate
answers `REQUIRE_INTERVENTION` for the IRREVERSIBLE "Confirm transfer" click (zero driver dispatch)
the engine suspends inside the same run: PRE evidence (observation digest + PNG screenshot) while
automation still owns control; a typed `InterventionRequest` (identity, step, risk, requested
action, verification requirement — no ref, selector, credential or model text) is handed to an
`InterventionHandler` that receives nothing else, so it cannot perform the action; the V1 handler is
the terminal. The human clicks Confirm in the **same visible browser, page and `session_id`** and
types `done` or `abort` — both mean "re-observe and verify": HUMAN → RETURNING, a fresh bounded
observation evaluates the step's own postcondition (`status` "The transfer has been posted."), POST
evidence is captured from the observation the verdict was made on, and `INTERVENTION_VERIFIED`
records outcome, `completed_by`, digests and screenshot references (relative path + sha256; bytes
never in the JSONL). Only `VERIFIED_COMPLETED` + `done` restores AUTOMATION and advances **past**
the step: the official chronology shows `ACTION_DISPATCHED` for s1, s2, s3 and s5 only, then
`SUCCESS` with `transfer_reference = TXN-000001` (22 contiguous redacted events, one run id, one
session id). Verified + `abort` ends `FAILURE / INTERVENTION_ABANDONED` (committed, not resumed);
an unprovable completion ends `FAILURE / UNKNOWN_COMMIT_STATE`, `safe_to_retry=false`, owner left in
RETURNING; evidence failure after the human may have acted is never a reason to repeat, and PRE
evidence failure fails closed before any ownership is granted. `DENY` is never converted into a
request for approval.

## Safety

An explicit, configurable allowlist (`policy/legacy_bank.json`: origins, routes, action types) is
enforced by one chokepoint — the only production caller of `Surface.act` — on the **resolved**
execution target (NAVIGATE on its destination URL, everything else on the observed page), never on
a caller claim or the model's intent; an empty policy denies everything. Risk tiers
`SAFE_READ | REVERSIBLE_WRITE | IRREVERSIBLE` come from deterministic rules where the highest match
wins (a reorder can never downgrade a commit button); the model has no risk field; IRREVERSIBLE is
never auto-dispatched and, after a human completes it, never repeated. Secrets live in the
environment only and are never logged, prompted, persisted or returned; provider requests use
`store=False` with zero transport retries; prompts, completions and provider objects are never
persisted. Redaction happens before any byte reaches disk: runtime inputs persist as
`<input:name>`, secret-shaped keys as `[REDACTED]`, URL credentials and query values are stripped,
refs have no field to live in (**E10**, plus `scripts/public_audit.py` over everything committed).
Documented V1 limits: screenshot pixels and derived PII in observed prose (a synthetic name in a
heading) are not redacted — acceptable only because the target data is synthetic; read-path
exfiltration is not policed.

## Cuts

Deliberate, at clean seams: no CLI/`CapabilityRunner` (runs are driven by the eval scripts under
`tests/evals`); no reviewer console; no `WAIT` action, no automatic re-dispatch, no self-healing
replay (refused, D21); no Gemini/Anthropic adapters (the `LLMClient` seam is one file per adapter);
no tenant overlays or desktop surface (designed above); screenshots only as intervention evidence
(no OCR, no model vision); no positive "not committed" verdict after a hand-back (the review page
carries no such signal); one intervention per step; the terminal is the operator interface. Compiler
limits by design: route-only checkpoint, one verified strategy per target, declared (not
trace-learned) outcomes. What live testing caught: Playwright quotes number-looking textbox values
(`"500.00"`) as YAML scalars; the ARIA reader kept the quotes and deterministic verification refused
— fixed at the parsing boundary and pinned by parser and real-browser regression tests.
