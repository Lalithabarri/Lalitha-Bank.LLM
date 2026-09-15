# ENGINEERING DECISIONS

"The simplest implementation that proves the required invariants today,
while preserving explicit seams for tomorrow."

| ID | Decision | Chosen approach | Why | Main trade-off | Revisit when |
|---|---|---|---|---|---|
| D01 | Target application | Local fake "Legacy Bank Operations Console" — search, detail, balances, transfer, review, confirm; server-rendered, no test IDs; synthetic data only | Full control of error states needed to demonstrate the taxonomy; zero ToS/credential risk; fast to build | Less "impressive" than automating a real public site | Time remains after core evals pass; add iframe hostility to the same app |
| D02 | Language/runtime | Python 3.12 | Mature Playwright bindings; Pydantic maps directly onto "typed, versioned, reviewable" artifact requirement | If less fluent than in TS, costs time relearning idioms mid-project | Only before Bundle B started — not revisited |
| D03 | Process architecture | Single-process modular monolith, strong internal module boundaries, no queues/DB/K8s/microservices | Matches "simpler is fine if justified"; nothing in V1 needs concurrency or distributed infra | Can't survive its own crash mid-run without an external supervisor | HITL design (Bundle F) proves a single process can't keep the browser usable during pause — never happened |
| D04 | Surface abstraction | Accessibility-first `observe()`/`act()` contract: role + accessible name + optional row/section scope; screenshots evidence-only | Directly answers the 3.7 seam question; accessibility data is named as desktop-portable in the assignment glossary | More upfront work than dumping DOM/HTML | Driver's accessibility API proves too sparse against the real target |
| D05 | Browser driver | Playwright Python (sync API), role/accessible-name locators + accessibility snapshot | Implements D04 directly; built-in auto-waiting; best-documented option under time pressure | Browser-only — no desktop coverage | Never in V1 (desktop explicitly declined) |
| D06 | LLM provider | Google Gemini API, model `gemini-3.6-flash`, verified by real smoke test | Working API access confirmed | Vendor lock at client layer, contained by `LLMClient` interface | Never — frozen after B |
| D07 | Observation strategy | `{url, page_title, step_index, elements[], visible_text_outline}`; each element `{ref, role, accessible_name, value, enabled, tag_hint, context_hint}`; refs transient only | `context_hint` makes row-scoped targets expressible; caps model context; keeps refs out of persistence | Poorly-semantic legacy cells may be unreachable | Fallback locator strategy needed (D13) |
| D08 | LLM action vocabulary | Closed set: CLICK, FILL, SELECT, NAVIGATE, READ, WAIT, FINISH, REPORT_BLOCKED — no generated code, no arbitrary selectors | Unconstrained action space defeats policy enforcement; matches "propose semantic actions, not code" | No scroll/hover/keyboard primitives | A demo scenario genuinely needs one — add to vocabulary deliberately |
| D09 | ActionGate / policy authority | Single synchronous chokepoint; sole caller of `Surface.act()`; static deterministic config; model proposes, never modifies policy | Structural authority independent of LLM reasoning; testable (S1–S3) | Doesn't cover read-path exfiltration | Never in V1 — named limitation |
| D10 | Action risk model | SAFE_READ / REVERSIBLE_WRITE / IRREVERSIBLE, assigned by deterministic policy/config over (action_type, route); separate from data sensitivity | Risk (world-state effect) and sensitivity (data type) are orthogonal axes; collapsing them made the artifact's risk field ambiguous | Commit-verb detection for IRREVERSIBLE is heuristic | Target app's confirm control changes in a way that breaks the heuristic |
| D11 | Artifact schema philosophy | Typed, versioned, parameterized capability, decoupled from the raw transcript; `schema_version` and `capability_version` kept separate | Assignment's own named focal point; separates format evolution from capability evolution | More upfront schema design than a step list | Never in V1 — frozen after C |
| D12 | Parameter binding | Explicit discriminated `LITERAL \| INPUT_REF{input_name}`; compiler receives declared discovery inputs + trace; compilation fails if a required input can't be parameterized | Declared fact enforced by the compiler, not inferred by heuristic matching | None material | Never — this was itself a correction to an earlier heuristic proposal |
| D13 | Target locator strategy | `TargetDescriptor`: ordered semantic strategies (role+name, label, visible text, optional row/section scope with `anchor_param`); no refs/CSS/XPath | Named load-bearing piece; durable across UI structure changes as long as semantics hold | Legacy elements with poor accessibility semantics may need more fallback strategies than V1 builds | Feasibility check (Bundle A step 3) finds real gaps |
| D14 | Business outcome model | `known_outcomes[]` on the artifact, declarative detectors, checked continuously (before next step, while waiting, before final failure) — never inferred from a timeout | Prevents "no such member" from being misclassified as `TARGET_NOT_FOUND`/timeout | Detector coverage limited to what's declared at compile time | A new legitimate business outcome is needed for a new capability |
| D15 | Replay determinism | `ReplayDeps` has no `LLMClient`; three terminal statuses only (SUCCESS/BUSINESS_OUTCOME/FAILURE); ambiguous targets fail closed, never `first()` | "No LLM in the decision loop" enforced structurally, not by convention | Ambiguity failure requires the artifact author to write more specific descriptors | Never — this is the core invariant (R1–R3) |
| D16 | Retry semantics | Small per-step bounded retry budget by deterministic rule (poll/dismiss/re-resolve); never retry AMBIGUOUS_TARGET or POLICY_DENIED; every retry logged | Bounded so it can't mask a real failure or loop forever | No global retry scheduler — if a new recoverable pattern appears, it needs its own rule | A demo run reveals a recoverable condition not yet covered |
| D17 | Irreversible / unknown-commit behavior | IRREVERSIBLE always routes through ActionGate to `REQUIRE_INTERVENTION`, never dispatched automatically; uncertain post-action commit state → `UNKNOWN_COMMIT_STATE`, `safe_to_retry=false`, never repeated | Duplicate financial mutation is worse than a stalled run; matches "real money, real stakes" framing | Fully unattended replay of the transfer flow can't run hands-off | A future approval system binds authorization to exact transaction details — explicitly out of V1 |
| D18 | HITL ownership | Explicit `ControlOwner` state (AUTOMATION/PENDING_HUMAN/HUMAN/RETURNING), not a flag; same wrapper-generated `session_id` across the handoff; resume only after deterministic re-verification | Exclusivity and same-session continuity are both falsifiable (H1, H2); no restart-disguised-as-resume | CLI blocking on stdin is the whole "operator interface" in V1 | Persistent/distributed ownership needed — `ControlOwner` already shaped for it |
| D19 | Evidence/redaction | `EvidenceWriter` + `Redactor` as the only structured-write path; redaction before disk, never after; screenshots only on failure/HITL, never per-step | Structural rather than conventional; matches "never persist secrets or raw sensitive data" | Screenshot pixel content isn't redacted (acceptable only because target data is synthetic) | Real/sensitive target data is ever used — named production limitation |
| D20 | Multi-tenant strategy | Base capability + restricted tenant overlay grammar with no fields for risk/policy/steps/approval; designed only, not built | Abuse becomes a validation error, not a permission, by construction | Not implemented — no working demonstration | Only if implementation finishes with meaningful time to spare |
| D21 | Drift strategy | Drift → FAILURE + evidence; artifact never self-modifies; human-initiated rediscovery → evals → new version | Self-healing replay would silently reintroduce LLM judgment into a financial action | No automatic detection beyond fallback-strategy usage as a signal | Never in V1 — explicitly refused, not deferred |
| D22 | Reviewer console | Thin adapter over `CapabilityRunner`/`RunResult`/`CapabilityArtifact`/evidence/`ControlOwner`; server-rendered HTML+CSS+vanilla JS; zero automation logic in it | Reviewer comprehension without a second implementation surface to keep in sync | Time spent here is time not spent on core evals | Core evals fail — console work stops immediately |
| D23 | V1 infrastructure/scaling cuts | No queues, Redis, Kafka, Kubernetes, microservices, multi-tenant DB, distributed lease, auth platform | No current requirement needs them; named directly as an anti-signal in the assignment | None — this is a pure simplification | A specific future requirement demands one, not "it would be more scalable" |
| D24 | LLM provider — implemented V1 adapter (supersedes D06 for the implementation) | OpenAI Responses API through the official `openai` Python SDK; strict Structured Outputs (Pydantic `text_format`, `strict: true`, `additionalProperties: false`); target model `gpt-5.6-sol`, runtime-configurable (`CUA_OPENAI_MODEL`); `store=False` on every request; no background mode; no chaining; SDK transport retries fixed at 0 (one `MODEL_CALL` evidence event == one HTTP attempt; the only retry is the single evidenced corrective retry); credential from `OPENAI_API_KEY` at runtime only; the SDK is importable from exactly one adapter, `cua.llm.openai_client`; the model sees a model-safe observation (known runtime inputs templated to `<input:name>`) and may only NAVIGATE to a narrow entry-route list that is a subset of the policy allowlist | At implementation time an OpenAI credential is available to the build environment and Gemini credentials are not; submission time is constrained; the provider sits behind the provider-neutral `LLMClient`, so the change is one adapter, not an architecture change. **This is not a claim that OpenAI was measured to be better than Gemini — no comparison was run.** | D06's Gemini smoke-test evidence does not transfer; vendor lock at the adapter layer remains (contained); zero transport retries mean a transient 429/5xx aborts an attempt as `MODEL_ERROR` (rerun, attempts recorded) | Gemini (or another) credentials are available and time permits a second adapter against the same contract and the same discovery tests; transport retries only together with explicit transport-attempt evidence |

D06 is retained verbatim as the historical record; D24 supersedes it for the implemented V1 provider
(Milestone 6, official E01 run `run_e49e4d0cbe09`).

---

## Decision principles

- LLM discovers; artifact remembers; deterministic engine executes.
- Policy authority is independent of the model — the model proposes, it
  never modifies policy or risk.
- Ambiguity fails closed — never guess between equivalent candidates.
- LLM output is not proof; evals provide proof — a claim is only real once
  it's been falsified and survived.
- Irreversible uncertainty is not blindly retried — an unknown commit
  state ends in escalation, never a repeat.
- Human takeover preserves the same live session — a handoff that
  secretly restarts is a failed handoff.
- Risk (what an action does) and sensitivity (what data it touches) are
  separate axes, never one tier.
- Scalability comes from stable boundaries before distributed
  infrastructure — design the seam, don't build the infrastructure.
- Core correctness outranks UI polish, while UI makes evidence easier to
  understand once correctness already holds.

---

## Deferred evolution

**DESIGNED FOR, NOT IMPLEMENTED IN V1.**

- DesktopAccessibilitySurface
- Centralized ArtifactRegistry
- Distributed execution
- Persistent/distributed control ownership
- Tenant overlays
- Bounded policy-approved recovery
- Approval/reconciliation systems

DECISIONS FROZEN
