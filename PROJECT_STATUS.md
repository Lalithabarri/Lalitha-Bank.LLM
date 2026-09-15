# Project Status

Implementation and presentation ledger. **Not** a source of truth — that order is
[REQUIREMENTS.md](REQUIREMENTS.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md). Updated only after a milestone is verified.

## Project thesis

An LLM discovers a legacy-UI workflow once, under policy. A deterministic engine replays it
forever with zero model decisions, a human in the loop where money moves, and evidence for every
claim. Flagship: `read_savings_balance(member_id)`. Escalation: `transfer_funds(...)`.

## Current milestone

**Milestone 3A — COMPLETE: policy + artifact foundation; deterministic runtime remains in
progress.** ActionGate + allowlist + risk classification + ControlOwner basics; CapabilityArtifact
schema + ArtifactStore; the handwritten `read_savings_balance@1.0.0` artifact (ARCHITECTURE §15
steps 4–6). Passed a pre-freeze red-team review after fixing one critical and two high findings
(recorded in the M3 entry). 259 tests passed; ruff clean; no new dependency. Commit recorded in
the M3 entry below.

Milestone 2 is COMPLETE: committed as `b0dff2e`, pushed to `origin/main`.
Milestone 1 is COMPLETE: committed as `362b0f1`, pushed to `origin/main`.

Active next milestone: **Milestone 4 — Deterministic replay core** (ARCHITECTURE §15 steps 7–8).

## Completed milestones

### Milestone 1 — Project scaffold + Legacy Bank Operations Console + minimal domain scaffolding

Maps to ARCHITECTURE §15 steps 1–2.

- **Goal:** a clean Python project and a live, controllable synthetic target application —
  not a partial build of the whole system.
- **Decisions applied:** D01 (local fake console, server-rendered, no test IDs, synthetic data),
  D02 (Python 3.12), D03 (single process, no infra), D07/D08 (snapshot + action vocabulary
  shapes). Session choices: uv, package `cua`, `src/` layout, Flask + Jinja2 for the target app,
  transfers between a member's own accounts (Checking ↔ Savings).
- **What was implemented:**
  - `pyproject.toml` (packages `src/cua`, `src/legacy_bank`; script `legacy-bank`), `uv.lock`,
    `.gitignore` with `.env` excluded, README stub.
  - `src/legacy_bank/`: in-memory `Bank` (M1001 Alice Morgan $2,340.50/$15,275.00; M1002 Rahul
    Iyer $980.00/$4,120.75; M404 absent); screens search → detail → balances and detail →
    transfer → review → confirm → complete; validation errors re-render with `role="alert"`;
    review carries values in hidden fields and confirm re-validates them; `TXN-00000N`
    references; `POST /__admin/reset` harness control; fault mode `ambiguous_savings`
    (startup-only) renders two `Savings` rows on member detail.
  - `src/cua/`: docstring-only packages for every ARCHITECTURE §3 module carrying their
    Owns / Must never rows; `cua.domain` with `DomainModel` (frozen, extra-forbid),
    `ActionType` (8 values), `SurfaceElement`, `SurfaceSnapshot`. Nothing else.
- **Actual verification performed:** `uv sync`; `uv run pytest -q`; `uv run ruff check`/`format`;
  two live servers (`:8000` default, `:8001 --fault-mode ambiguous_savings`) walked with `curl`:
  M1001, M1002, M404 (search POST and direct GET), transfer 500.00 → review (balances unchanged)
  → confirm → complete `TXN-000001` → detail updated, insufficient-funds alert, admin reset,
  2 vs 1 Savings rows, zero `data-testid` in served HTML. A real GUI browser visual check was
  then completed by the project owner: M1001 search/detail, M1002 search/detail with different
  balances, M404 business-outcome message, transfer → review → confirm → complete, and
  `ambiguous_savings` mode on port 8001 showing two Savings rows. Final verification re-ran `uv sync`,
  `pytest -q`, `pytest --collect-only -q`, and `ruff check`, and confirmed on disk (AST import
  scan, symbol scan, `git check-ignore`, `git ls-files`) the boundary, scope, and git-safety
  facts recorded below.
- **Test/eval results:** 51 passed, 51 collected (test_flows 26, test_markup 6, test_domain 6,
  test_boundaries 13). ruff clean, 29 files formatted. No E01–E10 evals exist yet.
- **Bugs or incorrect assumptions discovered:**
  - ruff: `class X(str, Enum)` flagged (UP042) ×2; three lines over 100 chars.
  - Milestone report misstated the per-file test breakdown as 28+6+6+13=53 against an actual
    51; the total was correct, the per-file figure for `test_flows.py` was estimated, not
    collected.
- **Fixes made:** enums switched to `StrEnum`; long lines wrapped; breakdown corrected via
  `pytest --collect-only`.
- **Remaining limitations:** Flask development server only (fine for a local target); phantom
  second Savings balance is a fixed `$250.00` constant; balances render currency-formatted
  (`$2,340.50`), so the later `DECIMAL` transform must strip `$` and `,`; the ARIA feasibility
  check (ARCHITECTURE §15 step 3) has not been run, so "semantic HTML is sufficient for
  role+name targeting" is still an assumption.
- **Git commit:** `362b0f1` — build legacy bank target and project scaffold (pushed to `main`).
- **Presentation/pitch takeaway:** the deterministic core will be proven against a target we
  fully control — every error state (not-found, validation, ambiguity) is reproducible on demand,
  and the target is only ever seen through its UI (`cua` is structurally barred from importing
  `legacy_bank`).

#### Reviewer / benchmark signal

- **Assignment signal:** a controlled live UI with a non-trivial financial workflow and
  reproducible business/failure states.
- **Reference-project lesson applied:** keep the target independent from the automation system
  and make failure cases deterministic rather than relying on public-site behavior.
- **What our implementation improves/clarifies:** M1001/M1002/M404 plus an explicit ambiguity
  fault mode provide repeatable inputs for later replay, business-outcome, and fail-closed evals.
- **Proof:** `tests/legacy_bank/test_flows.py`, `tests/legacy_bank/test_markup.py`,
  `tests/cua/test_boundaries.py`, and manual verification (`curl` walk of every flow plus the
  GUI browser visual check).

### Milestone 2 — Surface contract + PlaywrightSurface + semantic feasibility

Maps to ARCHITECTURE §15 step 3. A feasibility gate: can a browser driver expose the target
through a stable, driver-neutral semantic representation, with the driver fully hidden?

- **Goal:** answer that question against the real app in a real browser before any Artifact,
  Replay, Policy, or Discovery code exists.
- **Decisions applied:** D04 (accessibility-first observe/act), D05 (Playwright Python sync,
  Chromium only), D07 (snapshot shape), D08 (action vocabulary), §3 surface row, §4 import rule.
  Session choices: `page.aria_snapshot(mode="ai")` as the single semantic source; internal
  observation-local `ref → (role, name, ordinal)` map resolved with documented
  `get_by_role(...).nth()`; surface is single-use; `tag_hint` left `None` in V1; no YAML
  dependency (line reader against real captures); `playwright` is the only dependency added.
- **What was implemented:** `cua/surface/contract.py` (`Surface` Protocol, `SurfaceAction`,
  `ActResult`, errors; contract-only exports from `cua.surface`), `cua/surface/aria.py` (raw
  ARIA text → parse → `SurfaceElement[]` + outline; `context_hint` = table caption / rowheader /
  group ancestry), `cua/surface/query.py` (`find`, all matches, never first),
  `cua/surface/playwright_surface.py` (browser lifecycle, `session_id`, observe, NAVIGATE /
  CLICK / FILL / SELECT / READ through one private `_dispatch()`), `cua/domain/ids.py`
  (`new_session_id`). Seven raw ARIA captures under `tests/surface/fixtures/` as reader
  regression inputs. `legacy_bank` untouched.
- **Actual verification performed:** `uv sync`; `uv run playwright install chromium`; full
  `uv run pytest -q` including 16 real-Chromium tests against an in-process Legacy Bank server
  (normal + `ambiguous_savings`); dump run with `CUA_FEASIBILITY_DUMP` (27 observations) from
  which SURFACE_FEASIBILITY.md was authored; `ruff check` + `ruff format --check`; AST import
  scan; `git diff --stat -- src/legacy_bank` empty.
- **Test/eval results:** 98 passed, 98 collected (legacy_bank 32, cua/test_domain 6,
  cua/test_boundaries 24, surface 36). ruff clean, 39 files formatted. States A–G all PASS.
  Measured driver calls per `observe()`: 2, on all 27 observations. No E01–E10 evals exist yet.
- **Bugs or incorrect assumptions discovered:**
  - Plan assumed Chromium names `<tr>`/`<table>`; it does not — rows and tables are unnamed, the
    `rowheader`/`caption` children carry the names. `context_hint` was built on that evidence.
  - Plan proposed filling `tag_hint` with one `evaluate_all` per role (~10 driver calls per
    observe) with a silent-`None` fallback on order mismatch — dropped.
  - `close()` originally kept the old `session_id` and `step_index` — a stale id would undermine
    the HITL same-session proof.
  - First draft of the ref map computed unnamed-element ordinals over unnamed elements only,
    while the locator (`get_by_role(role)`) matches all elements of the role.
  - A planned reader test asserted a 1:1 raw-line-to-node mapping — coupling to incidental
    Playwright formatting; never merged.
  - Feasibility report first stated the test split as "51 + 47"; actual is 38 unchanged M1 +
    24 boundary (13 retained, `playwright` blanket-forbid replaced by exact allowlist) + 36 new.
- **Fixes made:** `context_hint` from rowheader/caption; `tag_hint` mechanism removed
  (`observe()` = 2 driver calls); single-use surface (`open()`/`session_id` raise after
  `close()`; test added); ordinal counted over the set the locator actually matches, proven by
  an element-by-element alignment test on the ambiguous page; reader tests use an explicit
  fixture set + semantic assertions; report split corrected from `--collect-only`.
- **Remaining limitations:** `tag_hint` always `None`; unnamed roles resolved by role +
  ordinal; AI-mode snapshot format is observed not specified (reader fails loudly on unknown
  syntax); Chromium only, headless only so far; waiting is `wait_for_load_state()` only
  (bounded declarative waits are step 7); `ACTION_DISPATCHED` not yet emitted (step 9) — the
  `_dispatch()` chokepoint exists.
- **Git commit:** `b0dff2e` — feat: add surface abstraction and Playwright adapter (pushed to
  `origin/main`).
- **Presentation/pitch takeaway:** the highest-risk architectural assumption was falsifiable and
  survived: a real browser exposes every control and state of both flows by role + accessible
  name + table context, M404 is an observable `alert`, and the ambiguity fault yields two
  distinct candidates that nothing collapses — with Playwright confined to one file behind a
  driver-neutral `Surface`.

#### Reviewer / benchmark signal

- **Assignment signal:** perception biased toward semantics, not selectors; the surface seam
  named in the heterogeneity design is real code, not a diagram.
- **Reference-project lesson applied:** verify the semantic strategy against the live surface
  before building on it; keep transient observation refs strictly separate from durable
  targeting; preserve ambiguity instead of first-match guessing.
- **What our implementation improves/clarifies:** one documented driver call yields the whole
  semantic tree; table relationships are expressed as `context_hint` without any DOM path; the
  ambiguity fault mode is proven to survive observation intact.
- **Proof:** `tests/surface/test_playwright_surface.py` (states A–G, alignment, ref lifecycle,
  single-use, flagship flow), `tests/cua/test_boundaries.py` (single-file Playwright allowlist,
  annotation scan, contract-only public API, subprocess import check), SURFACE_FEASIBILITY.md
  (verbatim dumps, measured call counts).

### Milestone 3 — ActionGate + policy + ControlOwner basics · CapabilityArtifact schema + ArtifactStore · handwritten `read_savings_balance`

Maps to ARCHITECTURE §15 steps 4–6. Step 7 (resolver, conditions, RunResult, ReplayEngine) and
the step-8 proofs were split into Milestone 4 so the deterministic core gets its own review.

- **Goal:** the two things the deterministic engine will stand on — one policy chokepoint that
  becomes the only production caller of `Surface.act()`, and a typed, versioned, parameterized
  artifact — plus one handwritten artifact expressed entirely in M2-proven semantics. Nothing
  executes an artifact yet.
- **Decisions applied:** D09 (gate authority), D10 (risk over `(action_type, route[, target
  name])`, separate from sensitivity; artifact-declared risk can raise, never lower), D11
  (`schema_version` vs `capability_version`), D12 (`LITERAL | INPUT_REF`), D13 (ordered semantic
  strategies), D14 (`known_outcomes[]` with declarative detectors), D18 (`ControlOwner` as
  explicit state), §6 persisted actions / invariants, §8 gate flow and DENY-is-hard.
  **Approved schema extension (not in the frozen provenance fields):** `Provenance.source ∈
  {handwritten, discovery}` so a handwritten fixture can never be mistaken for genuine
  discovery output in evidence (matters for E01). Approved renderings: `DenyReason`
  {CONTROL_NOT_OWNED, ACTION_TYPE_NOT_ALLOWED, ORIGIN_NOT_ALLOWED, ROUTE_NOT_ALLOWED};
  `TargetStrategy{role, name, scope, text_contains}` with `{input}` placeholders as D13's
  concrete form (label semantics collapse into accessible name per M2 evidence).
- **What was implemented:** `cua/policy/{routes,risk,config,action_gate}.py` (`RiskTier`
  ordered, `RiskRule`, `classify_risk`, `effective_risk`; deny-by-default `PolicyConfig`,
  JSON-loadable; `ActionGate.authorize` in the order owner → action type → origin → route →
  IRREVERSIBLE; `ActionGate.dispatch` calls `surface.act` only on ALLOW);
  `cua/hitl/control.py` (`ControlOwnerState`, `ControlOwner` with a **test-only** constructor
  state so the CONTROL_NOT_OWNED branch is exercised now; transitions at step 16);
  `cua/artifact/schema.py` (closed vocabularies, `TargetStrategy`/`TargetDescriptor`,
  `ValueBinding`, four `Condition`s, `Step` shape-per-action, `KnownOutcome`, `Provenance`,
  `CapabilityArtifact` with validators for I1 no refs, I2 declared + actually-referenced
  inputs, I4 outputs ↔ READ steps, I5 business-outcome-only, `extra="forbid"` for I3/I6);
  `cua/artifact/store.py` (`name@version` identity, validated load/save, identity/conflict
  refusal); `policy/legacy_bank.json` (example config: search CLICK → SAFE_READ, "Confirm
  transfer" CLICK → IRREVERSIBLE); `capabilities/read_savings_balance@1.0.0.json`
  (NAVIGATE → FILL ← INPUT_REF member_id → CLICK → READ `cell` @ `table: Accounts > row:
  Savings` → `savings_balance: DECIMAL`; checkpoint route + heading; MEMBER_NOT_FOUND via
  `alert` detector). No new dependency. `surface/`, `domain/`, `legacy_bank/` untouched.
- **Actual verification performed:** `uv run pytest -q` full suite; `ruff check` +
  `ruff format --check`; independent grep for `.act(` call sites, Playwright importers, and
  `M1001`/ref/selector strings in the artifact; adversarial one-off checks (`ref` field, `WAIT`,
  hard-coded member, empty policy against all five action types with a counting fake surface);
  `git diff --stat` proving M1/M2 source and tests are byte-identical except the extended
  boundary file.
- **Test/eval results:** 259 passed, 259 collected (M1/M2: legacy_bank 32, domain 6, surface
  36, boundaries 41 [24 retained + 17 new]; M3 new: policy 65, hitl 6, artifact 73). ruff
  clean. No E01–E10 evals exist yet.
- **Bugs or incorrect assumptions discovered:** (1) first cut of `risk.py` imported
  `route_matches` from `config.py`, which imports `RiskRule` from `risk.py` — an import cycle;
  (2) the plan's `ControlOwner` had no way to reach a non-AUTOMATION state until step 16,
  leaving the CONTROL_NOT_OWNED deny branch (S4/H2) untested; (3) `Provenance.source` was
  presented as if frozen — it is an extension.
  **Pre-freeze red-team review found and fixed before commit:** (4) CRITICAL —
  `ActionGate.dispatch` authorized a caller-supplied route but dispatched `action.url`;
  reproduced: allowed route + `NAVIGATE https://evil.example/` → ALLOW and `goto` called;
  (5) HIGH — origin/route for every action were caller claims, never observed state;
  (6) HIGH — `classify_risk` was first-match-wins: reordering a broad SAFE_READ rule before
  the "Confirm transfer" IRREVERSIBLE rule downgraded a commit to SAFE_READ; (7) MEDIUM — the
  flagship traceability test silently skipped `text_contains` templates, so the
  MEMBER_NOT_FOUND detector and checkpoint heading were never actually traced to captures;
  (8) MEDIUM — `success_checkpoint: list` had no stated semantics; (9) LOW — selector
  heuristics rejected real accessible names (`.NET`, `/accounts`, `#1 priority`).
- **Fixes made:** route matching moved to `policy/routes.py` (leaf); `ControlOwner(state=…)`
  documented as the test-only mechanism and covered by parametrized gate tests; extension
  recorded here and in the schema docstring. Red-team fixes: `dispatch(surface, action, *,
  snapshot, target, declared_risk)` now derives origin/route itself — from `action.url` for
  NAVIGATE, from `snapshot.url` otherwise — via `ActionGate.request_for`; no caller-supplied
  route or decision parameter exists (asserted by a signature test); 7 off-allowlist NAVIGATE
  cases (host, host-suffix, port, scheme, two routes) → DENY with zero `act()` calls;
  `classify_risk` = **highest matching risk wins** (order-independent, proven both orders);
  traceability test binds `{member_id}` with each fixture member and requires the concrete
  string (`No member found for M404.`, `Member M1001`) in a capture — 2 templates checked;
  `success_checkpoint` documented as **ALL** conditions in declared order (approved extension,
  schema docstring); selector heuristics narrowed to `//`, `css=`, `xpath=`, `text=` (structural
  `extra="forbid"` remains the real guarantee); CONTROL_NOT_OWNED dispatch test parametrized
  over PENDING_HUMAN/HUMAN/RETURNING.
- **Remaining limitations:** the gate has no production caller yet (M4's engine) — the
  `.act(` call-site test is what holds that line; `KnownOutcome.terminal_status` is a
  `Literal["BUSINESS_OUTCOME"]` string until `TerminalStatus` exists in `replay/`;
  `policy/legacy_bank.json` hard-codes the default port origin (ephemeral-port tests build
  configs in code); artifact validators are schema-level — the compiler's trace-based checks
  are step 13; the handwritten artifact is unexecuted until M4; `ArtifactStore.save` writes
  the destination directly (not atomic; a truncated file fails validation loudly on load —
  deferred to before submission); route matcher accepts percent-encoded segments such as
  `%2F`/`%2E%2E` as one `{param}` segment (M4 must feed the browser's decoded path or reject
  such segments); `text_contains` must be matched against both `accessible_name` and `value`
  in M4 (alert text lives in `value`), and unbound `{placeholder}`s must be a bind-time error,
  never a wildcard.
- **Git commit:** see below (recorded after commit).
- **Presentation/pitch takeaway:** policy is structural, not conventional — an empty config
  denies every action, DENY has no field that could carry approval, and only one line of
  production code can touch the driver. The artifact is data the compiler and runtime meet at:
  it cannot express a selector, a ref, a piece of code, or a hard-coded member id without
  failing validation.

#### Reviewer / benchmark signal

- **Assignment signal:** the artifact schema is the named focal point; allowlist is explicit
  and configurable; risky actions are distinguished by a justified, conservative model.
- **Reference-project lesson applied:** central policy authority before driver dispatch;
  typed artifact as the compiler/runtime boundary; irreversible behaviour never auto-dispatched.
- **What our implementation improves/clarifies:** the artifact can't hard-code an input
  (required inputs must be referenced), can't lower policy risk, and can't carry a transient
  ref; the gate evaluates the resolved execution target, never an intent summary.
- **Proof:** `tests/policy/test_action_gate.py` (deny-by-default, each reason, zero `act()` on
  DENY/REQUIRE_INTERVENTION, declared risk never lowers), `tests/artifact/test_schema.py`
  (60 cases incl. every invariant violation), `tests/artifact/test_flagship_artifact.py`
  (parameterized, no member ids, semantics traceable to M2 captures),
  `tests/cua/test_boundaries.py` (single `.act(` call site, LLM-free layers, Playwright-free
  annotations).

## Decision corrections worth explaining

| Initially proposed | Corrected to | Why it matters |
|---|---|---|
| Implement the full enum vocabulary (`FailureCode`, `GateDecision`, `TerminalStatus`, `ControlOwnerState`, …) in Milestone 1 as "shared vocabulary" | Only `ActionType`, `SurfaceElement`, `SurfaceSnapshot` now; everything else is defined in its owning module at its own milestone | Freezing runtime semantics before the code that exercises them exists invites churn and silently pre-decides later milestones. Empty, docstring-only locations preserve the architecture without that risk. |
| `FailureCode` listed `DEAD_END` and `MODEL_ERROR` | Those belong to `StopReason` (discovery); `FailureCode` (replay) must not include them | Discovery stop reasons and replay failure codes are different vocabularies; mixing them would blur the discovery/replay boundary the whole design rests on. Forward note — `FailureCode` is not built yet. |
| Plan's route table once showed "No member found for ." | Implementation always renders the requested id: "No member found for M404." | The not-found text is a business-outcome detector input; it must be stable and carry the id. |
| `.gitignore` as one of several scaffold files | `.gitignore` with `.env` excluded written **before** any other file | A real Gemini credential exists locally; the first commit must be incapable of tracking it. |
| Resolve observation refs with an `aria-ref=…` selector found in Playwright's source | Internal observation-local map `ref → (role, name, ordinal)` resolved through documented `get_by_role().nth()`; refs never leave the snapshot | Undocumented selector syntax is not a contract; durable targeting must rest on documented, accessibility-shaped queries (D13). |
| Fill `tag_hint` via one `evaluate_all` per role on every `observe()`, `None` on order mismatch | `tag_hint` stays `None` in V1; `observe()` is 2 driver calls | No consumer needs it; per-role round trips sit inside the future discovery loop; a silent fallback is the guessing pattern the design forbids. |
| Reusable `PlaywrightSurface` (reopen after close) | Single-use: `close()` forgets `session_id`; `open()` afterwards raises | `session_id` is the proof mechanism for the same-session HITL invariant (H1); a stale or recycled id would make that proof meaningless. |
| Add PyYAML because the ARIA snapshot is YAML-shaped | Small line/indent reader written against real captures; no dependency | Dependencies are added on evidence of need; the observed format is one node per line. |
| One Milestone 3 covering §15 steps 4–8 (gate, artifact, resolver, engine, proofs) | Split: M3 = steps 4–6; M4 = step 7 + step-8 proofs | Step 7 is where the determinism invariants (R2–R4) are built; it deserves its own plan and review rather than riding along with schema work. |
| `ControlOwner` read-only until step 16, leaving CONTROL_NOT_OWNED untested | Test-only constructor state, documented as such | An invariant (S4/H2) should not go unexercised because the transitions that reach it aren't built yet. |
| Present `Provenance.source` as part of the frozen schema | Recorded as an approved extension | Ledger honesty: the frozen doc lists provenance fields without a source marker; the marker is added deliberately so handwritten fixtures can never pass as discovery evidence. |
| `ActionGate.dispatch(surface, action, request)` with caller-supplied origin/route | `dispatch(surface, action, *, snapshot, target, declared_risk)`; the gate derives origin/route from `action.url` (NAVIGATE) or the observed `snapshot.url` | Policy must judge the resolved execution target, not a claim (§8). The claim-based form let an allowlisted route authorize a `goto` to any URL, and would have let M4 codify a trusted-caller interface. |
| First-matching risk rule wins | Highest matching risk wins, order-independent | A configuration reorder must never downgrade a commit button; a rule can only raise. Trade-off accepted: no rule can express "this control is safer than the broad rule" — exactly the exception a financial gate should not permit. |
| Singular `success_checkpoint` (ARCHITECTURE wording) | Non-empty list, **ALL** semantics, declared order, short-circuit — approved extension | The four-condition vocabulary has no conjunction; a meaningful checkpoint needs route *and* heading. Recorded so M4's evaluator implements exactly this. |
| Traceability test "every semantic string observed" while skipping templates | Templates bound with fixture member ids; concrete strings must appear in captures | A test that silently skips the two most important strings (the business-outcome detector, the checkpoint heading) would keep passing while the artifact drifted from reality. |

## Evidence produced

| Claim | Evidence |
|---|---|
| Search → detail shows correct member and balances for M1001/M1002 | `tests/legacy_bank/test_flows.py::test_search_valid_member_shows_detail_and_balances` |
| M404 is a business outcome on the search screen, and a 404 on direct navigation, same text | `test_flows.py::test_search_absent_member_is_business_outcome_on_search_screen`, `::test_direct_navigation_to_absent_member_is_404_with_same_text` |
| Transfer → review → confirm → complete moves money exactly once, with a reference | `test_flows.py::test_transfer_happy_path`, `::test_second_transfer_increments_reference` |
| Validation errors and tampered hidden fields never change state | `test_flows.py::test_transfer_validation_errors_leave_state_unchanged`, `::test_confirm_revalidates_tampered_hidden_fields` |
| Fault mode duplicates only the Savings row; default has one; selectable via env | `test_flows.py::test_ambiguous_savings_mode_renders_two_savings_rows`, `::test_default_mode_has_exactly_one_savings_row`, `::test_fault_mode_from_env` |
| Reset restores seed; app instances are isolated | `test_flows.py::test_admin_reset_restores_seed_and_counter`, `::test_app_instances_do_not_share_state` |
| No `data-testid`; every control labelled; one `<h1>` + `<main>` per page | `tests/legacy_bank/test_markup.py` |
| Domain shapes match D07/D08 exactly; models frozen and extra-forbid | `tests/cua/test_domain.py` |
| Every ARCHITECTURE §3 package exists; `cua` imports no `legacy_bank`/`flask`/`google`; `playwright` imported only by `surface/playwright_surface.py`; no Playwright type in driver-neutral annotations; `cua.surface` public API is the contract only; `legacy_bank` never imports `cua` | `tests/cua/test_boundaries.py` |
| A real browser observes every control of the read-balance and transfer flows by role + name + context (states A–E) | `tests/surface/test_playwright_surface.py::test_A_*`, `::test_BC_*`, `::test_D_*`, `::test_E_*`; SURFACE_FEASIBILITY.md |
| The same semantic strategy identifies M1001 and M1002 | `::test_BC_member_detail_semantics_hold_for_both_members` (one shared helper, parametrized) |
| M404 is observable as a business state (`alert` value `No member found for M404.`) | `::test_F_member_not_found_is_an_observable_business_state` |
| `ambiguous_savings` yields two distinct Savings candidates, none collapsed; every ref resolves to the element it came from | `::test_G_ambiguous_savings_preserves_two_distinct_candidates`, `::test_G_every_ref_resolves_to_the_element_it_came_from` |
| Transient refs are observation-local (stale after act, unknown across observations, invalidated by NAVIGATE) | `::test_act_after_act_without_observe_is_stale`, `::test_unknown_ref_and_ref_from_previous_observation`, `::test_navigate_invalidates_refs`, `::test_same_ref_string_means_different_things_on_different_pages` |
| A surface is single-use and forgets its `session_id`; a fresh surface gets a new one | `::test_surface_is_single_use_and_forgets_its_session_id` |
| The flagship flow runs end-to-end through the surface and READs `$15,275.00` | `::test_flagship_read_savings_balance_flow` |
| `observe()` costs 2 driver calls | `::test_A_*` assertion; 27/27 dumps in SURFACE_FEASIBILITY.md |
| Snapshot from a live browser serializes to plain JSON; `import cua.surface` does not load Playwright | `::test_snapshot_from_live_browser_round_trips_as_json`, `tests/surface/test_contract.py` |
| The reader handles real captures and fails loudly on unknown syntax | `tests/surface/test_aria_reader.py` |
| Policy is deny-by-default: an empty config denies every action type | `tests/policy/test_action_gate.py::test_empty_config_denies_everything` |
| Each denial reason (owner, action type, origin, route) is produced by exactly its condition; owner is checked first | `::test_action_type_not_allowed`, `::test_origin_not_allowed`, `::test_route_not_allowed`, `::test_control_not_owned_*`, `::test_control_owner_is_checked_before_everything_else` |
| IRREVERSIBLE → REQUIRE_INTERVENTION, never ALLOW; declared risk can raise but never lower | `::test_irreversible_requires_intervention_never_allow`, `::test_declared_risk_can_raise_but_never_lower`, `tests/policy/test_risk.py::test_effective_risk_never_lowers_policy_risk` |
| NAVIGATE is authorized on its real destination: off-allowlist host / host-suffix / port / scheme / route → DENY, `goto` never called | `::test_navigate_is_authorized_on_its_destination_and_goto_is_never_called[*]`, `::test_navigate_to_allowlisted_destination_is_dispatched_once` |
| Origin/route come from the observed snapshot, not a caller claim; `dispatch` has no route or decision parameter | `::test_route_is_taken_from_the_snapshot_url_not_a_caller_claim`, `::test_non_navigate_actions_require_the_observed_snapshot`, `::test_dispatch_has_no_parameter_for_a_precomputed_decision_or_route` |
| Risk rule order can never downgrade: highest matching risk wins | `tests/policy/test_risk.py::test_highest_matching_risk_wins_regardless_of_rule_order[*]`, `::test_a_rule_can_only_raise_the_default_never_lower_another_match` |
| Templated `text_contains` strings (MEMBER_NOT_FOUND detector, checkpoint heading) resolve to strings actually captured in M2 | `tests/artifact/test_flagship_artifact.py::test_every_semantic_string_was_observed_in_milestone_2` (2 templates checked) |
| A DENY or REQUIRE_INTERVENTION dispatch makes zero `act()` calls; ALLOW makes exactly one | `::test_dispatch_on_deny_never_touches_the_surface`, `::test_dispatch_on_require_intervention_never_touches_the_surface`, `::test_dispatch_on_control_not_owned_never_touches_the_surface`, `::test_dispatch_on_allow_calls_act_exactly_once` |
| `Surface.act` is called from exactly one production line in `cua` | `tests/cua/test_boundaries.py::test_surface_act_is_called_only_from_the_action_gate` |
| The artifact JSON round-trips; refs, selectors, code, secrets, approvals, WAIT, undeclared/unused inputs, and unread outputs all fail validation | `tests/artifact/test_schema.py` (60 cases) |
| `read_savings_balance@1.0.0` is parameterized (no `M1001`), declares MEMBER_NOT_FOUND as a business outcome, and every semantic string appears in an M2 capture | `tests/artifact/test_flagship_artifact.py` |
| policy/artifact/hitl/surface/domain never reach `cua.llm` or `google`, transitively | `tests/cua/test_boundaries.py::test_deterministic_layers_never_reach_the_llm_layer` |

No `evidence/` directory exists yet (arrives with the EvidenceWriter milestone).

## Production and evolution seams

| Deliberately simple in V1 | How it evolves |
|---|---|
| Target app state is in-memory per `create_app()`; `POST /__admin/reset` for test isolation | Stays synthetic by design (D01); a real target replaces the whole app, not the seam |
| Fault mode fixed at process start (CLI/env) | More modes (interstitial, slow load, iframe) added the same way once core evals pass |
| Transfer review state carried in hidden fields, no server session | Intentional legacy-realism; also keeps the app cookie-free for the automation |
| `cua.domain` holds only shapes needed by the next milestone | Each module defines its own models when implemented; boundary test grows into `scripts/verify.sh` |
| Boundary check is a pytest AST scan of `src/cua` | Becomes the fresh-clone `verify.sh` structural assertion (ARCHITECTURE §4) |
| One `Surface` implementation (Chromium via Playwright), single-use per run | `DesktopAccessibilitySurface` behind the same Protocol; descriptors are already role + name + scope (designed, not implemented) |
| Refs resolved by `(role, name, ordinal)` inside the surface | Durable targeting via `TargetDescriptor` strategies (step 7); refs stay observation-local forever |
| `_dispatch()` only counts and invalidates refs | Emits `ACTION_DISPATCHED` through the EvidenceWriter (step 9) |
| `tag_hint` always `None` | Filled only if a concrete consumer appears |
| `ControlOwner` is a state holder with a test-only constructor state | Step 16 adds the transitions (escalate / accept / done / verified / abort); the gate already denies on any non-AUTOMATION state |
| Policy config is a static JSON file with segment-wise route patterns | Tenant overlays (designed only) vary base origin and route aliases; risk/policy fields have no overlay grammar by construction |
| Artifact validators are schema-level (I1, I2, I4, I5, I6-by-construction) | Step 13's compiler adds trace-based checks (a declared discovery input must not survive as a literal) and `compile_report.json` |
| `KnownOutcome.terminal_status` is a string literal | Becomes `replay.TerminalStatus.BUSINESS_OUTCOME` once M4 defines the terminal vocabulary |

## Current risks / unverified assumptions

- `context_hint` is a surface-level convention (table caption / rowheader / group ancestry). It
  is sufficient for this target; how `TargetDescriptor` strategies use it is decided at step 7.
- Unnamed roles (`alert`) resolve by role + ordinal; unique on these pages, asserted by the
  alignment test, but a page with several unnamed same-role elements would rely on ordinal order.
- The AI-mode ARIA snapshot format is observed behaviour of Playwright 1.62, not a stated
  contract; the reader raises on anything unrecognised, so a format change fails loudly.
- Headed (non-headless) operation — needed for HITL — has not been exercised yet.
- Fresh-clone reproducibility depends on `uv` and a Chromium download.
- `DECIMAL` transform must accept currency-formatted cells (`$2,340.50`); untested until step 7.
- The gate has no production caller yet: M4's `ReplayEngine` must route every action through
  `ActionGate.dispatch`; the single-call-site test will fail the moment anything else calls
  `act()`.
- The handwritten artifact's strategies are validated against M2 captures, not executed; M4's
  resolver is the first thing that will resolve them live.

## Next milestone

**Milestone 4 — Deterministic replay core** (ARCHITECTURE §15 step 7 + step-8 proofs):
`TargetResolver` (ordered strategies, one deadline, fail-closed on >1), declarative condition
evaluator, `RunResult` with exactly three terminal statuses, `ReplayEngine` with
`ReplayDeps` that has no LLM field by construction; proofs: M1001 → `15275.00`, same artifact
M1002 → `4120.75`, M404 → BUSINESS_OUTCOME/MEMBER_NOT_FOUND, `ambiguous_savings` →
FAILURE/AMBIGUOUS_TARGET with zero dispatch of the READ, policy denial → FAILURE/POLICY_DENIED
with zero dispatch, zero-LLM reachability of the engine.
