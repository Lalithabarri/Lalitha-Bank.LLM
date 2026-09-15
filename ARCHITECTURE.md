# ARCHITECTURE FREEZE

Consolidated from approved Bundles A, B, C, D, E, F, G, H.
No new decisions. Every line traces to an approved bundle.

Status: FROZEN. Implementation may begin.
Revisit only if implementation evidence proves an assumption false.

---

## 1. Project thesis

An LLM discovers a legacy-UI workflow **once**, under policy.
A deterministic engine replays it **forever**, with **zero model decisions**,
a **human in the loop** where money moves, and **evidence** for every claim.

Flagship capability: `read_savings_balance(member_id) -> savings_balance`
Escalation capability: `transfer_funds(...)` — irreversible, always human-confirmed in V1.

---

## 2. Architecture diagram

```
                 CLI  (discover | replay | verify)
                  |
          CapabilityRunner
                  |
   +--------------+---------------+
   |                              |
DiscoveryAgent               ReplayEngine
   |                              |
   | LLMClient (Gemini)           | (NO LLM dependency)
   |                              |
   +----------> ActionGate <------+      <-- single authorization chokepoint
                    |
                 Surface  (PlaywrightSurface)
                    |
        Legacy Bank Operations Console (local, synthetic)

   NormalizedTrace --> ArtifactCompiler --> CapabilityArtifact --> ArtifactStore
   All components --> EvidenceWriter --> Redactor --> evidence/
   ControlOwner <--> ActionGate, HITL
```

---

## 3. Modules and ownership

| Module | Owns | Must never |
|---|---|---|
| `surface/` | Driver, observe/act, session_id, `ACTION_DISPATCHED` | Leak Playwright types upward |
| `llm/` | Gemini client, structured output, one corrective retry | Be imported by replay |
| `discovery/` | Observe→decide→act loop, stopping reasons | Call `Surface.act()` directly |
| `artifact/` | Schema, compiler, store, invariants I1–I6 | Hold secrets or approvals |
| `replay/` | Deterministic execution, outcomes, retries | Import `LLMClient` |
| `policy/` | ActionGate, allowlist, risk tiers | Accept LLM input |
| `hitl/` | ControlOwner, intervention, hand-back | Convert DENY into approval |
| `evidence/` | EvidenceWriter, Redactor, screenshots | Write unredacted structured data |
| `console/` | Reviewer UI (adapter only) | Contain automation logic |

---

## 4. Dependency direction

```
cli -> runner -> {discovery, replay} -> action_gate -> surface
artifact <- compiler <- normalized_trace <- discovery
policy, hitl, evidence: depended upon, never depend upward
llm/: reachable ONLY from discovery/
```

Enforced structurally: `verify.sh` asserts no module outside `surface/` imports
`playwright`, and no module reachable from `ReplayEngine` imports the Gemini SDK
or `LLMClient`.

---

## 5. Discovery flow

```
observe (SurfaceSnapshot: url, page_title, step_index,
         elements[ref, role, accessible_name, value, enabled,
                  tag_hint, context_hint], visible_text_outline)
  -> Gemini proposes typed action (CLICK FILL SELECT NAVIGATE READ
                                   WAIT FINISH REPORT_BLOCKED)
  -> Pydantic validation (1 corrective retry, else MODEL_ERROR)
  -> ActionGate.authorize
  -> Surface.act
  -> verify / observe
  -> normalized trace event
  -> repeat
```

Stop reasons: `GOAL_REACHED | MAX_STEPS | TIMEOUT | DEAD_END |
BLOCKED_BY_POLICY | MODEL_ERROR`. Defaults: max_steps 25, timeout 5 min
(operational, not architectural). Only `GOAL_REACHED` yields an artifact.

Transient refs (`e1`, `e2`) exist for one observation only and are never persisted.

---

## 6. Compilation flow

Input: normalized trace + declared discovery inputs. Output: `CapabilityArtifact`.

- `schema_version` (format) and `capability_version` (semver: MAJOR contract break,
  MINOR workflow change, PATCH locator/postcondition repair) kept separate.
- Values compile to explicit `LITERAL` or `INPUT_REF{input_name}`.
  A declared discovery input must not survive as a literal.
  **Compilation fails** if a required runtime input cannot be safely parameterized.
- Persisted actions: `NAVIGATE FILL SELECT CLICK READ` only. `WAIT` never persisted.
- `TargetDescriptor`: ordered semantic strategies (role + accessible_name,
  label semantics, visible text, optional row/section scope, runtime-input anchoring).
  No refs, CSS, XPath, or driver objects.
- Every mutating/navigation step carries a declarative postcondition;
  READ steps declare the extraction target; artifact-level `success_checkpoint` mandatory.
- `known_outcomes[]` (e.g. `MEMBER_NOT_FOUND`, terminal_status `BUSINESS_OUTCOME`,
  declarative detector).
- `risk` per step: `SAFE_READ | REVERSIBLE_WRITE | IRREVERSIBLE` — policy-stamped
  metadata, **never authorization**.
- Transforms limited to `STRING DECIMAL INTEGER BOOLEAN`. No eval, no generated code.
- Provenance: discovery_run_id, model_id, compiled_at, compiler_version, optional
  redacted/templated goal summary. No transcript, chain-of-thought, keys, or PII.

Invariants I1–I6: no transient refs; inputs parameterized; durable semantic targets;
final checkpoint + declared outputs; business outcomes distinguishable from failures;
no secret or reusable authorization.

---

## 7. Replay flow

`ReplayDeps = {surface, action_gate, control_owner, clock, evidence_writer, redactor, config}`
— deliberately **no LLMClient**.

```
validate artifact -> validate runtime inputs -> open session
for each ordered step:
    observe / check declared known outcomes
    verify AUTOMATION ownership
    bind INPUT_REF / LITERAL
    resolve target uniquely
    ActionGate.authorize
        DENY                 -> FAILURE / POLICY_DENIED
        REQUIRE_INTERVENTION -> suspend, create InterventionRequest
        ALLOW                -> Surface.act
    await postcondition while watching declared outcomes + bounded recoverables
    record evidence
check declared known outcomes once more
verify final success checkpoint
extract / convert declared outputs
-> SUCCESS
```

**Target resolution** (one bounded overall window):
loop all ordered strategies per pass; `1` match resolves; `>1` is `AMBIGUOUS_TARGET`
and fails closed immediately; if all return `0`, poll briefly and repeat;
`TARGET_NOT_FOUND` at deadline. **Never `first()`.**

**Conditions**: `route_matches | element_present | text_present | value_equals`.
Declarative only. Any automated recovery action also passes through ActionGate.

**Terminal statuses, exactly three**: `SUCCESS | BUSINESS_OUTCOME | FAILURE`.
Recoverable conditions stay internal recorded events.

**Retries**: poll for not-yet-present; gated dismissal + bounded retry for known modal;
never retry `AMBIGUOUS_TARGET` or `POLICY_DENIED`; at most one retry for `SAFE_READ`;
at most one for `REVERSIBLE_WRITE` when demonstrably idempotent;
`IRREVERSIBLE` never auto-dispatched. Every attempt logged.

**Unknown commit**: if a potentially committing side effect occurred and completion
cannot be established — `UNKNOWN_COMMIT_STATE`, `side_effect_may_have_completed=true`,
`safe_to_retry=false`. No repeat, no auto-refresh, human reconciliation.

---

## 8. ActionGate flow

```
caller -> bind values -> resolve target -> ActionGate.authorize(action, resolved_target)
       -> ALLOW | DENY | REQUIRE_INTERVENTION -> Surface.act ONLY on ALLOW
```

- Deny-by-default allowlist: origins, routes, action types. Enforced on the
  **resolved execution target**, not the model's `intent_summary`.
- Deterministic config. The model proposes; it can never modify policy or risk.
- `IRREVERSIBLE` -> `REQUIRE_INTERVENTION` in V1.
- `ControlOwner != AUTOMATION` -> `DENY(CONTROL_NOT_OWNED)`.
- `DENY` is a hard boundary and is **never** converted into human approval.

Secrets: env/runtime only; never in artifact, trace, or evidence; never sent to the
model unless required. Synthetic business values (balances) may be observed —
redaction must not prevent the system completing its goal.
Redaction happens **before** disk, never raw-to-disk-then-redact-for-display.

---

## 9. HITL state machine

```
AUTOMATION --escalate--> PENDING_HUMAN --accept--> HUMAN
HUMAN --done--> RETURNING --verified--> AUTOMATION
              RETURNING --unverified--> terminal FAILURE
PENDING_HUMAN --abort--> FAILURE / ESCALATION_ABANDONED
```

Triggers: `REQUIRE_INTERVENTION`, irreversible action, `DEAD_END`,
`RECOVERY_EXHAUSTED`, optionally `AMBIGUOUS_TARGET`. **Not `POLICY_DENIED`.**

Same session: wrapper-generated `session_id`; one browser/context creation per run;
no context or page recreation across the handoff.

Hand-back: re-observe (observe allowed, act forbidden) -> classify against declared
outcomes, the step postcondition, and the final checkpoint -> if verified, mark runtime
step `completed_by = HUMAN` (execution metadata, not an artifact mutation) -> advance
past it -> restore AUTOMATION. Never re-dispatch a human-completed action.

Declined irreversible step -> `INTERVENTION_DECLINED` / `ESCALATION_ABANDONED`.
Unverifiable post-human irreversible state -> `UNKNOWN_COMMIT_STATE`.

Evidence: `InterventionRequest` + `HumanActionRecord` (pre/post digests and
screenshots, classification, redacted note). Observed state change, not keystrokes.

Invariants H1–H6: same session; exclusive ownership; irreversible never auto-dispatched;
no duplicate execution; verified resume only; hard policy stays hard.

---

## 10. Evidence and eval strategy

Envelope (JSONL): `ts, run_id, run_kind, session_id, seq, event_type, step_id?,
step_index?, severity, payload, redaction_applied`.

`ACTION_DISPATCHED` is emitted at the Surface boundary immediately before the driver
call — it is the ground truth for "did an automated action actually occur."
Paired with `ACTION_COMPLETED` / `ACTION_FAILED`.

Persistence boundaries: `EvidenceWriter` (structured), `ArtifactStore` (validated
artifacts), screenshot capture (failure + HITL pre/post only). No ad-hoc writes.

```
evidence/ discovery/<run_id>/ | artifacts/ | replay/<run_id>/
          interventions/<escalation_id>/ | evals/ | README.md
```

Eval matrix (frozen):

| ID | Claim | Status |
|---|---|---|
| E01 | Genuine Gemini discovery at M1001 | LIVE API |
| E02 | Discover M1001 -> replay M1002 | AUTOMATED |
| E03 | Zero-LLM replay (exploding stub + structural) | AUTOMATED |
| E04 | M404 -> BUSINESS_OUTCOME / MEMBER_NOT_FOUND | AUTOMATED |
| E05 | Ambiguous target fails closed, zero dispatch | AUTOMATED |
| E06 | Policy denial, zero dispatch, no escalation | AUTOMATED |
| E07 | HUMAN/RETURNING ownership denies automation | AUTOMATED |
| E08 | Same-session HITL continuity | MANUAL + AUTOMATED ASSERTIONS |
| E09 | No repeat after human irreversible action | MANUAL + AUTOMATED ASSERTIONS |
| E10 | Sentinel secret never persisted | AUTOMATED |

`scripts/verify.sh` — offline, fresh-clone, no API key, no human.
`scripts/verify_live.sh` — E01, E08, E09. verify.sh reports these as
`LIVE/MANUAL — see committed evidence`.

Unit tests prove components in isolation. Behavioral evals prove system claims from
observable evidence, written to falsify.

---

## 11. Heterogeneous and multi-tenant evolution

`Surface` is the seam: `PlaywrightSurface` today, `DesktopAccessibilitySurface`
possible later. Descriptors are accessibility concepts (role + name + scope), which
UIA and AX expose too. Artifact, replay, policy, and evals would be unchanged.

Multi-tenant: vendor base capability + restricted tenant overlay varying only base
origin, route aliases, locator aliases, wait tuning, known-harmless variants.
The overlay grammar has **no fields** for risk, policy, steps, or approval, so
abuse is a validation error rather than a permission. Merge validated against
identical step count, action sequence, and risk values.

Drift: detected -> `FAILURE` + evidence -> artifact never self-modifies ->
human-initiated rediscovery -> evals -> new capability version.
No self-healing model decisions in deterministic replay.

Designed, not implemented.

---

## 12. V1 cuts

Multi-tenant overlays; desktop surface; self-healing replay (refused, not deferred);
approval workflow and stored authorization; value-aware policy; screenshot
redaction/OCR; queues, database, registry, auth, WebRTC co-browsing;
per-step screenshots; telemetry backend.

---

## 13. Reviewer console role

`LegacyBank Capability Console` — a thin adapter over `CapabilityRunner`, `RunResult`,
`CapabilityArtifact`, evidence, and `ControlOwner`. Contains **no automation logic**;
none in templates or frontend JS.

Sections: Overview · Live Run Timeline (real events only) · Capability Viewer
(+ raw JSON) · Safety/Policy (ALLOW / DENY / HUMAN REQUIRED) · HITL panel
(the human still operates the same headed browser; the console never fakes it) ·
Eval/Evidence view (real E01–E10).

Visual: professional agentic-financial-operations console — dark/slate base,
restrained accent, clear typography, compact cards, readable timeline, subtle state
transitions. No chatbot framing, gradients, hacker aesthetic, fake metrics.

Tech: server-rendered HTML + CSS + small vanilla JS. No React/Vite/Node.

---

## 14. Onsite seams

| Extension | Seam |
|---|---|
| Second surface | `Surface` interface; accessibility-shaped descriptors |
| Artifact registry | `ArtifactStore` + `name@version` identity |
| Distributed execution | Existing module boundaries in the monolith |
| Persistent control ownership | `ControlOwner` already explicit state |
| Tenant overlays | Load-time merge point + invariant set |
| Bounded approved recovery | `ActionGate` wraps every action |
| Approval / reconciliation | `REQUIRE_INTERVENTION` + `UNKNOWN_COMMIT_STATE` |

---

## 15. Exact implementation order

Strategy: prove the deterministic production core with a handwritten artifact
**before** introducing model uncertainty. Discovery then has exactly one job —
produce an artifact the already-proven replay engine can execute.

1. Repo skeleton + Python environment + minimal Pydantic domain models.
2. Local Legacy Bank Operations Console — M1001, M1002, M404; search, member
   detail, balances, transfer, review, confirm; deterministic fault/ambiguity mode.
3. `Surface` contract + `PlaywrightSurface`. Immediately run the feasibility check
   that semantic/ARIA observation can identify every control the target needs.
4. `ActionGate` + allowlist + risk classification + `ControlOwner` basics.
5. `CapabilityArtifact` schema + `ArtifactStore`.
6. One manually-written valid artifact: `read_savings_balance(member_id)`.
7. `TargetResolver` + declarative condition evaluator + `RunResult` +
   deterministic `ReplayEngine`.
8. **Prove the deterministic core before any LLM discovery**, using the handwritten
   artifact: successful replay for M1001/M1002; M404 business outcome; ambiguity
   failure; policy denial; zero-LLM structural and behavioral proof.
9. `EvidenceWriter` + `Redactor`, sufficient for real run evidence.
10. Gemini `LLMClient` + structured action output.
11. `DiscoveryAgent` + normalized trace.
12. **E01 — genuine Gemini discovery against M1001.**
13. `ArtifactCompiler` + `compile_report.json` + invariants I1–I6.
14. **Flagship proof:** discover M1001 -> compile generated artifact -> replay M1002
    -> zero model decisions during replay.
15. Complete automated E02–E07 and E10 evidence.
16. Full HITL: `InterventionRequest`, same-session CLI handoff, hand-back
    verification, unknown-commit behavior.
17. **E08 / E09.**
18. `scripts/verify.sh` + `scripts/verify_live.sh`.
19. README + REPORT.md functional draft (seven headings, exact order).
20. LegacyBank Capability Console reviewer UI.
21. Visual polish + demo media if time.
22. Fresh-clone verification.
23. Final GitHub cleanup and submission.

Correctness and evals outrank UI polish at every step.

