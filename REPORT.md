# REPORT

Every statement is backed by committed code, tests or evidence; `scripts/verify.sh` re-derives the
offline claims, `scripts/verify_live.sh` the live ones.

## Architecture

Thesis: an LLM discovers a legacy-UI workflow **once**, under policy; a deterministic engine replays
it with **zero model decisions**; where money moves, a human takes over the **same live session**;
every claim has evidence. Single-process modular monolith (Python 3.12, Pydantic, Playwright, Flask
target), structurally enforced boundaries, no infrastructure (D03, D23).

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
observation (refs, legality, literals, ambiguity, premature FINISH) before the gate; `FINISH` is a
proposal — a deterministic verifier establishes `GOAL_REACHED`; bounded by `max_steps` and a
timeout. **E01**: `gpt-5.6-sol`, 5 real calls, 4 gated dispatches, `GOAL_REACHED`,
`savings_balance = 15275.00`, the member id absent from every request body and from the evidence
(`evidence/discovery/run_e49e4d0cbe09`). The dependency direction (`replay` never reaches
`discovery`, `llm`, a provider SDK or the compiler) is asserted by AST and fresh-interpreter tests.

## Artifact schema

`CapabilityArtifact` (`cua/artifact/schema.py`) is data, never code: `schema_version` (format) and
`capability_version` (semver) kept separate; typed `inputs`; values bound as `LITERAL | INPUT_REF`;
ordered `steps` restricted to `NAVIGATE FILL SELECT CLICK READ`; `TargetDescriptor` = ordered
semantic strategies (role, name, table/row/group scope, text_contains), never refs, selectors or
driver objects; a declarative `postcondition` per mutating step and a typed `output` per READ; a
mandatory `success_checkpoint`; `known_outcomes` with declarative detectors (business outcome ≠
failure); per-step `risk` that can raise but never lower the gate's classification; `provenance`
(source, run id, model id, compiler version) with no transcript, key or PII. Two handwritten
artifacts bootstrap the engine; the product is the compiled one.

`ArtifactCompiler` (`cua/artifact/compiler.py`) is a pure function of the **persisted**
`DISCOVERY_ENDED` record (verified stop reason + literal-free trace) and a declared capability
contract. Only `GOAL_REACHED` records compile, and a typed `CompileReport` names the rule behind
every field (invariants I1–I6): each target is exactly the descriptor discovery proved unique (one
strategy, no fallback, never `first()`); bindings are preserved as recorded; postconditions are
`route_matches` on the observed landing path or `value_equals` on the step's own binding; the
checkpoint is `route_matches` on the final path, parameterized because the trace proves the member
by `ROUTE`. It never calls a model, sees a bound value or does I/O, is structurally barred from the
handwritten artifact, and fails an insufficient trace with a closed error vocabulary — never a
partial artifact. The committed generated artifact recompiles byte-for-byte from the committed E01
record. Two honest properties: the generated checkpoint is *weaker* than the handwritten one (route
only: the trace records no heading text and the compiler invents nothing; replay still fails closed
on an ambiguous or non-convertible read), and business outcomes are *declared* beside the goal,
labelled `DECLARED`, because a successful trace can never evidence one — verified by replaying the
generated artifact on M404.

## Determinism & error handling

`ReplayDeps = {surface, action_gate, clock, evidence, control_owner, intervention}` has no field
that can hold a model; a fresh interpreter that refuses to import `openai`, `google`, `cua.llm`,
`cua.discovery` and the compiler replays the generated artifact end to end. **E02**: committed E01
record → compiler → store → M1002 → `SUCCESS`, `Decimal("4120.75")`, 4 dispatches each preceded by
`GATE_DECISION ALLOW`, artifact bytes unchanged (`evidence/replay/run_50600b9540ca`). Targets
resolve by ordered semantic strategies on fresh observations inside one bounded window; `>1` match
is `AMBIGUOUS_TARGET` and fails closed. Recoverable conditions are bounded polls, never a fourth
status, and V1 adds no automatic re-dispatch (D16). Every row below is live-proven in real Chromium.

| Condition | Result |
|---|---|
| no such member (`alert`) | `BUSINESS_OUTCOME / MEMBER_NOT_FOUND`, READ never dispatched |
| two equivalent Savings cells | `FAILURE / AMBIGUOUS_TARGET`, both candidates reported, neither read |
| target absent within the window | `FAILURE / TARGET_NOT_FOUND` with the observed page |
| postcondition or checkpoint not established | `FAILURE / POSTCONDITION_FAILED` (expected vs observed) |
| action/route/origin off the allowlist, or owner ≠ AUTOMATION | `FAILURE / POLICY_DENIED` (+ reason), zero dispatch |
| irreversible action, no handler | `FAILURE / INTERVENTION_REQUIRED`, zero dispatch |
| bad input, unconvertible read, dead browser, evidence write failure | `INVALID_INPUT`, `TRANSFORM_ERROR`, `SURFACE_ERROR`, `EVIDENCE_ERROR` |
| human hand-back without a provable commit | `FAILURE / UNKNOWN_COMMIT_STATE`, `safe_to_retry=false`, never retried |

## Heterogeneity & multi-tenant

*Designed, not implemented.* The seam is the `Surface` protocol (`observe() → SurfaceSnapshot`,
`act(SurfaceAction)`): observations are accessibility concepts (role, name, value, enabled,
table/row/group context) and artifacts target exactly those, so nothing above the surface knows what
a browser is. A `DesktopAccessibilitySurface` over UI Automation or AX would produce the same
snapshot shape from a desktop app, leaving schema, compiler, engine, policy and evals unchanged; a
web app with poor semantics would first extend the reader, not the artifact. Multi-tenant reuse: one
vendor base capability plus a restricted per-tenant overlay whose grammar has fields only for base
origin, route aliases, locator aliases, wait tuning and known-harmless variants — none for risk,
policy, steps or approval, so abuse is a validation error, not a permission; the merge must preserve
step count, action sequence and risk values. Drift surfaces as `TARGET_NOT_FOUND` /
`AMBIGUOUS_TARGET` → human-initiated rediscovery → compile → evals → new `capability_version`;
replay never self-heals (D21).

## Escalation & handoff

Implemented and verified live (**E08/E09**, `evidence/replay/run_af80d82668bc`, headed Chromium, no
model). One `ControlOwner` object is shared by engine and gate, with exactly four edges
`AUTOMATION → PENDING_HUMAN → HUMAN → RETURNING → AUTOMATION`. When the gate answers
`REQUIRE_INTERVENTION` for the IRREVERSIBLE "Confirm transfer" click (zero driver dispatch) the
engine suspends inside the same run: PRE evidence (observation digest + screenshot) while automation
still owns control, then a typed `InterventionRequest` (step, risk, requested action, verification
requirement; never a ref, selector, credential or model text) goes to an `InterventionHandler` that
receives nothing else and so cannot perform the action. The human clicks Confirm in the **same
visible browser, page and `session_id`** and types `done` or `abort` — both mean "re-observe and
verify": HUMAN → RETURNING, a fresh observation evaluates the step's own postcondition (`status`
"The transfer has been posted."), and `INTERVENTION_VERIFIED` records outcome, `completed_by`,
digests and the POST screenshot reference (path + sha256). Only `VERIFIED_COMPLETED` + `done`
restores AUTOMATION and advances **past** the step: the official chronology shows
`ACTION_DISPATCHED` for s1, s2, s3 and s5 only, then `SUCCESS` with
`transfer_reference = TXN-000001` (22 contiguous redacted events, one run id, one session id).
Verified + `abort` ends `FAILURE / INTERVENTION_ABANDONED`; an unprovable completion ends
`FAILURE / UNKNOWN_COMMIT_STATE`, `safe_to_retry=false`, owner left in RETURNING — a step the human
may have completed is never repeated. `DENY` is never converted into a request for approval.

## Safety

A configurable allowlist (`policy/legacy_bank.json`: origins, routes, action types) is enforced by
one chokepoint — the only production caller of `Surface.act` — on the **resolved** execution target
(NAVIGATE on its destination URL, everything else on the observed page), never on a caller claim or
the model's intent; an empty policy denies everything. Risk tiers
`SAFE_READ | REVERSIBLE_WRITE | IRREVERSIBLE` come from deterministic rules where the highest match
wins; the model has no risk field; IRREVERSIBLE is never auto-dispatched and, once a human completes
it, never repeated. Secrets live in the environment only and are never logged, prompted, persisted
or returned; provider requests use `store=False` with zero transport retries, and no prompt,
completion or provider object is persisted. Redaction happens before any byte reaches disk: runtime
inputs persist as `<input:name>`, secret-shaped keys as `[REDACTED]`, URL credentials and query
values are stripped, refs have no field to live in (**E10**, plus `scripts/public_audit.py` over
everything committed). Documented V1 limits: screenshot pixels and derived PII in observed prose are
not redacted — acceptable only because the target data is synthetic; read-path exfiltration is not
policed.

## Cuts

Deliberate, at clean seams: no CLI/`CapabilityRunner` (runs go through `tests/evals`); no reviewer
console; no `WAIT` action, no automatic re-dispatch, no self-healing replay (D21); no second
provider adapter (the `LLMClient` seam is one file per adapter); no tenant overlays or desktop
surface (designed above); screenshots only as intervention evidence (no OCR or vision); no positive
"not committed" verdict after a hand-back; one intervention per step; the terminal is the operator
interface. Live testing caught one defect: Playwright quotes number-looking textbox values
(`"500.00"`), the ARIA reader kept the quotes and verification refused; fixed at the parsing
boundary, pinned by parser and real-browser tests.
