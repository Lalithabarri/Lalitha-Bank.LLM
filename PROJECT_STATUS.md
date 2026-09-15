# Project Status

Implementation and presentation ledger. **Not** a source of truth — that order is
[REQUIREMENTS.md](REQUIREMENTS.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md). Updated only after a milestone is verified.

## Project thesis

An LLM discovers a legacy-UI workflow once, under policy. A deterministic engine replays it
forever with zero model decisions, a human in the loop where money moves, and evidence for every
claim. Flagship: `read_savings_balance(member_id)`. Escalation: `transfer_funds(...)`.

## Current milestone

**Milestone 5 — COMPLETE: evidence + redaction foundation.**
Typed, versioned `EvidenceEvent` envelope (vocabulary 1.0: RUN_STARTED, GATE_DECISION,
ACTION_DISPATCHED, ACTION_COMPLETED, ACTION_FAILED, BUSINESS_OUTCOME, RUN_COMPLETED, RUN_FAILED);
deterministic `Redactor` (key / URL / ref / known-value rules); append-only, fsync-per-event
`JsonlEvidenceWriter` behind `EvidenceStore`'s `evidence/<run_kind>/<run_id>/events.jsonl`
layout; `EvidenceRecorder` as the one object the Surface (`DispatchListener`), the ActionGate
(`GateObserver`) and the engine (`ReplayDeps.evidence`) share. `ACTION_DISPATCHED` is persisted
at the Surface boundary immediately before the driver call. Evidence failure is its own domain
(`FailureCode.EVIDENCE_ERROR`), fails closed, never repeats an action, and never writes again for
that run. Every M4 result is unchanged. 563 tests passed (32 real-Chromium); ruff clean; no new
dependency. Committed as `70e053e`, pushed to `origin/main`.

Milestone 4 is COMPLETE: committed as `2386eab`, pushed to `origin/main`.
Milestone 3A is COMPLETE: committed as `057d1d7`, pushed to `origin/main`.
Milestone 2 is COMPLETE: committed as `b0dff2e`, pushed to `origin/main`.
Milestone 1 is COMPLETE: committed as `362b0f1`, pushed to `origin/main`.

Active next milestone: **Milestone 6 — `LLMClient` + structured action output, then
`DiscoveryAgent` + normalized trace and the E01 genuine live discovery** (ARCHITECTURE §15 steps
10–12). Evidence deliberately precedes genuine discovery (step 9 < 10) so that the first real E01
run is captured, from its first action, by an evidence layer that was already proven on replay.

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
  36, boundaries 41 [24 retained + 17 new]; M3 new: policy 60, hitl 6, artifact 78 — the
  per-directory split was first recorded as 65/73; corrected from `--collect-only` at M5, the
  total was always right). ruff clean. No E01–E10 evals exist yet.
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
- **Git commit:** `057d1d7` — feat: policy gate, artifact schema/store, handwritten
  read_savings_balance (Milestone 3A) (pushed to `origin/main`).
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

### Milestone 4 — Deterministic replay core: TargetResolver · binding · transforms · conditions · RunResult · ReplayEngine

Maps to ARCHITECTURE §15 step 7 and the step-8 proofs. The deterministic production runtime,
proven with the handwritten artifact before any model exists.

- **Goal:** execute `read_savings_balance@1.0.0` against the real UI with zero model decisions,
  semantic target resolution that fails closed, every action through `ActionGate`, typed inputs
  and outputs, declarative conditions, declared business outcomes, and exactly one of three
  terminal results.
- **Decisions applied:** D13 (ordered strategies; scope = exact `context_hint`; `text_contains`
  over accessible name or value), D14 (detectors on every observation, before any generic
  failure), D15 (`ReplayDeps = {surface, action_gate, clock}` — no LLM field by construction;
  three terminal statuses; never `first()`), D16 (no automatic re-dispatch in M4; the only
  "retries" are observation polls inside one bounded window each for resolution, postcondition,
  and checkpoint), §7 flow, §8 gate contract (NAVIGATE authorized on the built destination
  `action.url`; every other action on the exact snapshot the resolved ref came from).
  **Session decisions (approved before implementation):** `FailureCode` =
  {AMBIGUOUS_TARGET, TARGET_NOT_FOUND, POLICY_DENIED, INTERVENTION_REQUIRED, POSTCONDITION_FAILED,
  INVALID_INPUT, TRANSFORM_ERROR, SURFACE_ERROR} — `INTERVENTION_REQUIRED` exists because the gate
  can answer REQUIRE_INTERVENTION before HITL suspension (step 16) exists, and conflating it with
  POLICY_DENIED would turn a hard DENY into an approval path; `RunResult` enforces only
  self-contained invariants (SUCCESS: no outcome/failure; BUSINESS_OUTCOME: outcome, `outputs ==
  {}`; FAILURE: failure, `outputs == {}` — partial READ values are discarded, outputs are
  authoritative only on SUCCESS) while the engine proves `set(outputs) == declared outputs`
  before constructing SUCCESS; `RunResult` carries no `inputs` (data minimization; redaction is
  the EvidenceWriter's); `SurfaceDriverError(SurfaceError)` at the driver-neutral boundary with
  `PlaywrightSurface` translating every Playwright `Error` — the engine maps only
  `SurfaceDriverError | UnknownRefError` to SURFACE_ERROR and lets contract violations and
  programming errors propagate (no `except Exception` anywhere in `replay/`, asserted
  structurally); NAVIGATE destinations are built by `build_destination(origin-only base_url,
  bound absolute path)` with segment-safe placeholder binding, never by URL joining;
  `KnownOutcome.terminal_status` stays a string literal (agreement with `TerminalStatus`
  asserted by test) because `artifact/` must not import `replay/`.
- **What was implemented:** `cua/replay/{clock,result,transforms,binding,matching,conditions,
  resolver,engine}.py` (+ exports); `cua/surface/contract.py` `SurfaceDriverError` and error
  classification; `cua/surface/playwright_surface.py` `_driver_calls()` translation at every
  driver call site (observe/act semantics, ref rules, and the dispatch counter unchanged);
  `cua/domain/ids.new_run_id`; `cua/artifact/schema.py` exposes `PLACEHOLDER` (rename of the
  private pattern, no behaviour change). Tests: `tests/replay/` (scripted fixture surface with
  real ref discipline, recording surface, fake clock, zero-model subprocess script, unit tests
  for every module and every terminal path, live proofs), one surface dead-port test, boundary
  extensions. `legacy_bank/`, `policy/`, `capabilities/` untouched; no dependency change.
- **Actual verification performed:** `uv run pytest -q` (429 passed, 9.7 s, including 25 real
  Chromium tests); `uv run pytest -m browser -q` (25 passed); `ruff check` + `ruff format
  --check` (79 files); grep for `.act(` in `src/cua` (one call: `policy/action_gate.py:129`);
  grep for `float` in `transforms.py` (docstring prose only; AST test asserts no `float` name);
  grep for broad `except` in `replay/` (none); `git diff --stat -- pyproject.toml uv.lock
  src/legacy_bank capabilities policy` (empty); fresh-interpreter zero-model replay (scripted
  and live) with a `sys.meta_path` guard installed before any `cua` import.
- **Test/eval results:** 429 passed, 429 collected (M1–M3A 259 unchanged; policy +1 = 61 [the
  committed search-click risk proof]; surface +1 = 37;
  boundaries +14 = 55 [9 replay files in the driver-neutral list, `cua.replay` in the LLM-free
  roots, 4 new checks]; replay 154 = result 11, transforms 38, binding 46, matching 6,
  conditions 9, resolver 8, engine 25, zero_model 3, live 8). ruff clean. Live proofs: L1 M1001
  SUCCESS `15275.00`; L2 M1002 SUCCESS `4120.75` (same artifact object); L3 M404
  BUSINESS_OUTCOME/MEMBER_NOT_FOUND at `s3_search`, 3 dispatches; L4 ambiguous
  FAILURE/AMBIGUOUS_TARGET at `s4_read_savings`, candidates `$15,275.00`/`$250.00` without refs,
  3 dispatches, no READ; L5a empty policy → POLICY_DENIED at `s1`, 0 dispatches, 0 observations;
  L5b READ not allowlisted → POLICY_DENIED at `s4`, 3 dispatches; L6 zero-model live replay in a
  fresh interpreter → SUCCESS, no forbidden module loaded or attempted; L7 artifact
  `model_dump()` identical after every run; L8 dead port → FAILURE/SURFACE_ERROR at `s1`,
  message names `SurfaceDriverError`. No E01–E10 eval scripts exist yet.
- **Freeze checks recorded before commit:**
  - *Search-click risk proof (executable, real `policy/legacy_bank.json`):* for `CLICK` on
    `/members/search` with the `Search` button target, exactly one explicit rule matches —
    `(CLICK, /members/search, target_name=None) -> SAFE_READ` — and `classify_risk` returns
    `SAFE_READ`; `ActionGate.authorize` on the observed search-page snapshot returns `ALLOW /
    SAFE_READ`. Semantics confirmed in `risk.py`: when one or more explicit rules match, the
    result is the highest tier among the **matching explicit rules only**; the generic default
    (`CLICK -> REVERSIBLE_WRITE`) is consulted only when zero explicit rules match, so the
    SAFE_READ rule is live, not dead. Proven for unmatched clicks too: `CLICK /members/M1001
    "Transfer funds"` and `CLICK /members/M1001/transfer "Review transfer"` match no explicit
    rule and classify as `REVERSIBLE_WRITE`. Pinned by
    `tests/policy/test_risk.py::test_committed_search_click_rule_is_live_and_the_default_applies_only_when_nothing_matches`.
  - *V1 retry decision (implementation narrowing, deliberate):* M4 implements **no automatic
    re-dispatch** of any action. ARCHITECTURE §7 *permits* a bounded retry matrix (one retry
    for SAFE_READ, one for demonstrably idempotent REVERSIBLE_WRITE, gated modal dismissal);
    M4 narrows it to zero because the flagship deterministic replay does not need re-dispatch
    to prove any required behaviour. What exists instead: bounded observation polling for
    target resolution, postconditions, and the checkpoint; ambiguity is never retried; policy
    DENY is never retried; REQUIRE_INTERVENTION is never automatically retried; surface and
    transform errors terminate. A SAFE_READ or REVERSIBLE_WRITE re-dispatch rule will be added
    only when evidence shows a concrete recoverable failure mode and its safety conditions
    (D16). ARCHITECTURE.md is not contradicted (it bounds retries; it does not require them) and
    is left unchanged.
  - *Programming-error semantics:* the three `RunResult` statuses describe **reachable runtime
    outcomes** — SUCCESS, BUSINESS_OUTCOME, FAILURE. Internal invariant violations are
    programming errors, not a fourth outcome, and propagate loudly (`RuntimeError` /
    `ValueError`): duplicate output recording, a missing declared output or an undeclared
    output at SUCCESS construction (`ReplayEngine` checks `set(outputs) == set(artifact.outputs)`
    immediately before building SUCCESS and rejects a duplicate at record time), impossible
    `ActionGate` API misuse (its `ValueError`s), and any other condition unreachable from a
    schema-valid artifact and a correctly constructed engine. They are never converted into
    FAILURE, SURFACE_ERROR, TRANSFORM_ERROR, or any other user-facing `FailureCode`, so an
    implementation defect cannot be disguised as an ordinary workflow failure.
- **Bugs or incorrect assumptions discovered:** (1) two test-side mistakes only — a schema-I2
  violation in a binding test fixture (an unreferenced required input) and a percent-encoding
  test that used a space, which the segment rule rejects by design; (2) the first error-boundary
  test expected the first observation to belong to `s2`; it belongs to `s1`'s postcondition
  wait. No engine or surface defect was found by the live proofs; every live test passed on the
  first run.
- **Fixes made:** test fixtures corrected; no production change was needed after the first
  green unit run.
- **Remaining limitations:** no evidence is written (step 9) — `RunResult` is in-memory only;
  `FailureDetail.expected/observed` render bound values (needed for debuggability; the Redactor
  decides what persists); REQUIRE_INTERVENTION ends the run as FAILURE/INTERVENTION_REQUIRED
  until step 16 suspends instead; a non-AUTOMATION `ControlOwner` surfaces as
  POLICY_DENIED/CONTROL_NOT_OWNED (step 16 checks ownership before the gate); no re-dispatch rule
  exists (added only on evidence, D16); locator-path driver timeouts are translated but not
  provoked live (no slow-load fault mode yet); `base_url` is origin-only (no sub-path mounting);
  the gate still matches percent-encoded observed paths as single segments (conditions decode;
  the gate does not); no CLI — replay is driven from tests.
- **Git commit:** `2386eab` — feat: add deterministic capability replay core (rebased onto two
  remote docs-only ARCHITECTURE.md wording commits `7055dfe`, `1b5a3bc`; pushed to `origin/main`).
- **Presentation/pitch takeaway:** the runtime has no place to put a model — `ReplayDeps` has
  three fields and a fresh interpreter that refuses to import an LLM SDK still replays the
  capability end to end; ambiguity is a first-class failure with both candidates reported and
  neither touched; a business outcome is detected before any generic failure can be declared;
  and the same JSON file, byte-identical before and after, serves every member id.

#### Reviewer / benchmark signal

- **Assignment signal:** deterministic replay with stable semantic targeting, checkpoint
  verification, declared outputs, runtime-condition handling, and exactly three caller-facing
  results — "no such member" is a business outcome, never a crash.
- **Reference-project lesson applied:** prove the deterministic core with a handwritten artifact
  before introducing model uncertainty; make "zero LLM" a property of the types and the import
  graph, then falsify it in a clean interpreter.
- **What our implementation improves/clarifies:** outputs are trustworthy only on SUCCESS (no
  partial leaks); the ref used to act is provably from the snapshot the gate judged; driver
  failures cross the boundary as one driver-neutral type while programming errors stay loud.
- **Proof:** `tests/replay/test_replay_live.py` (L1–L8), `tests/replay/test_engine.py` (every
  FailureCode path, error boundary, zero dispatch), `tests/replay/test_zero_model.py` +
  `zero_model_replay.py`, `tests/cua/test_boundaries.py` (M4 section).

### Milestone 5 — Evidence + redaction foundation: envelope · Redactor · JSONL writer · recorder · dispatch chronology

Maps to ARCHITECTURE §15 step 9. Sequenced **before** the LLM client and discovery (steps 10–12)
on purpose: the non-negotiable E01 run must be captured by an evidence layer that was already
trusted on deterministic replay, not by one written afterwards around a model run.

- **Goal:** a small, deterministic, *observational* evidence subsystem — trustworthy action
  chronology with `ACTION_DISPATCHED` as the ground truth at the Surface boundary, redaction
  before any byte reaches disk, no transient ref / credential / raw input persisted — that
  records replay today and discovery/HITL later without touching ActionGate authority, Surface
  semantics, or any M4 result. No screenshots, no Gemini, no compiler, no HITL, no CLI.
- **Decisions applied:** ARCHITECTURE §3 (`surface/` owns `ACTION_DISPATCHED`), §7 (`ReplayDeps`
  gains the evidence dependency the architecture already lists), §10 (envelope fields, JSONL,
  `ACTION_DISPATCHED` immediately before the driver call, `evidence/<kind>/<run_id>/`), §8/D19
  (redaction before disk, never raw-then-redact; `EvidenceWriter` + `ArtifactStore` are the only
  disk writers — now asserted structurally), D09 (the gate stays the sole `Surface.act` caller;
  its observer is a constructor Protocol, never a `dispatch` parameter), D15 (a memory sink
  yields identical replay results; `import cua.evidence` loads no driver and no SDK).
  **Session decisions (approved with the plan and its addendum):** `GATE_DECISION` added to the
  requested minimum vocabulary so a denial is a recorded decision, not just a failure code;
  `RUN_COMPLETED` is the terminal event for SUCCESS *and* BUSINESS_OUTCOME (a business result is
  not a crash), `RUN_FAILED` for FAILURE; identifiers persist as deterministic, non-reversible,
  name-tagged placeholders (`<input:member_id>`) derived from the bound runtime inputs — chosen
  over removal (evidence stays readable) and partial masking (leaks); secret-shaped keys become
  the literal `[REDACTED]`, never a digest; URL userinfo/query values/fragment stripped with query
  keys kept; ref tokens rewritten to `<ref>` as defence in depth behind the structural guarantee
  that no payload model has a ref field; `EvidenceError` is its own failure domain →
  `FailureCode.EVIDENCE_ERROR`, fail closed, one failure per run, never a second write, never a
  redispatch; envelope fields are system-generated so only the payload is redacted;
  screenshots explicitly deferred to the HITL milestone (pixel redaction cannot be guaranteed;
  the failure event's redacted observation summary is the M5 richer signal).
- **What was implemented:** `cua/evidence/{events,redaction,writer,recorder}.py` (+ exports);
  `surface/contract.py` `DispatchRecord` / `DispatchListener` / `NullDispatchListener`;
  `PlaywrightSurface(listener=…)` with the exact order *resolve ref → on_dispatched → counter +
  ref invalidation → driver op → on_completed | on_failed*; `ActionGate(…, observer=…)` notifying
  in `dispatch()` after `authorize`, before `act`; `replay/summaries.py` (RunResult → evidence
  payloads, so `evidence` never imports `replay`); `ReplayEngine` brackets the run, stamps the
  step context, maps `EvidenceError`, derives `dispatched` on the surface-error path from the
  recorder's ground truth (an `UnknownRefError` inside `act()` is no longer recorded as
  dispatched), and fails loudly (`RuntimeError`) if a dispatched step has no `ACTION_DISPATCHED`
  behind it (a surface not wired to the run's recorder); `FailureCode.EVIDENCE_ERROR`;
  `domain/ids.new_event_id`. Tests: `tests/evidence/` (events, redaction, writer, recorder,
  memory sink), M5 sections in the surface, gate, engine, live, zero-model and boundary suites;
  `conftest` `evidence_store` / `evidence_recorder` / `recorded_surface` fixtures;
  `zero_model_replay.py --evidence-root`. `evidence/README.md` + four sample replay runs written
  through the `CUA_EVIDENCE_ROOT` opt-in (M2's dump precedent). `legacy_bank/`, `capabilities/`,
  `policy/`, `pyproject.toml`, `uv.lock` untouched.
- **Actual verification performed:** `uv run pytest -q` (563 passed, 11.1 s); `uv run pytest -m
  browser -q` (32 passed); `ruff check` + `ruff format --check` (90 files); grep for `.act(` in
  `src/cua` (one code call: `policy/action_gate.py:154`); grep for broad `except` in
  `evidence/ replay/ policy/ surface/` (none); `git diff --stat -- pyproject.toml uv.lock
  src/legacy_bank capabilities policy` (empty); fresh-interpreter zero-model replay with the real
  JSONL writer under the import guard (scripted and live): 14 events, no member id in the file;
  scan of `evidence/replay/*/events.jsonl` for `M1001|M1002|M404`, a `"ref"` key, or a ref-shaped
  token (none; every line `redaction_applied: true`).
- **Test/eval results:** 563 passed, 563 collected (M1–M4 429 unchanged except the two
  `ReplayDeps` field-set assertions and the `FailureCode` vocabulary test; new 134: evidence 91 =
  events 8, redaction 58, writer 10, recorder 15; surface +7 = 24 browser; policy +6 = 43 gate;
  engine +18 = 43; boundaries +12 = 67; live and zero-model extended in place). Live proofs L1–L8
  now also assert the on-disk chronology (`[RUN_STARTED, (GATE, DISPATCHED, COMPLETED)×4,
  RUN_COMPLETED]`, seq 1..14, session id on every line, `<input:member_id>` in place of the id).
  No E01–E10 eval scripts exist yet; E10's mechanism (a known runtime value injected into inputs,
  URLs and error text never surviving in persisted JSONL) is exercised in
  `test_engine.py::test_a_sentinel_runtime_input_is_redacted_from_urls_alerts_and_error_text`.
- **Bugs or incorrect assumptions discovered:** (1) the first `Redactor` walked Pydantic models
  only at the top level — a model nested in a dict was refused (`TypeError`); fixed to dump at
  any depth. (2) A first M5 test asserted `RecordingSurface.acts == []` for "evidence refused
  before the driver" — wrong instrument: the wrapper records the gate's call, the driver-attempt
  counter is `dispatched_actions`; the test now asserts the gate asked once, the counter stayed
  0, and the page never changed. (3) M4's engine marked *every* surface runtime error inside
  `act()` as `dispatched=True`; an `UnknownRefError` (raised before the driver op) was therefore
  mis-recorded — now taken from the recorder's ground truth. No production defect was found by
  the live proofs; every browser test passed on the first run.
- **Fixes made:** the three above; two E501 wraps.
- **Remaining limitations:** no screenshots (deferred with the seam named:
  `Surface.capture_screenshot` + an `ARTIFACT_CAPTURED` event, failure + HITL pre/post only);
  no `OBSERVATION` / `STEP_*` / discovery / HITL events (each is a `schema_version` bump);
  derived PII in observed prose (a member's name in a heading) is not detected — synthetic data;
  sensitivity is not declared on artifact inputs/outputs; the ref-token rule is heuristic (the
  structural guarantee is the real one); `RunKind` has only `REPLAY`; the M4 note that "no
  `evidence/` directory exists yet" is superseded — four sample runs + README now exist but the
  directory is not yet referenced by a demo command (no CLI); `INTERVENTION_REQUIRED` still ends
  the run rather than suspending it.
- **Git commit:** `70e053e` — feat: add evidence writer, redactor, and dispatch chronology
  (Milestone 5) (pushed to `origin/main`). Freeze audit before the commit: the approved evidence
  audit (no member id, no `"ref"` key, no ref-shaped token, every line `redaction_applied`, schema
  round-trip, `seq` contiguous) passed on all four sample runs; an additional name scan found the
  synthetic seed name `Alice Morgan` (`legacy_bank/data.py`, D01) in the ambiguity run's
  `failure.observed.outline_excerpt` with the member id already redacted — exactly the documented
  V1 limit (derived text in observed prose is not detected; target data is synthetic), recorded
  here rather than silently passed. `.envrc` added to `.gitignore` in the same commit.
- **Presentation/pitch takeaway:** every automated action now has a durable, ordered, redacted
  trail whose ground truth is written by the surface *before* the driver is touched — and a run
  that cannot write that trail stops rather than acting unrecorded, without ever repeating an
  action. The evidence layer has no authority: a memory sink produces byte-identical replay
  decisions, and neither the gate nor the surface can be reached by it.

#### Reviewer / benchmark signal

- **Assignment signal:** a structured log of what the agent did and why (REQUIREMENTS §7), secrets
  and PII never persisted (§6), and the replay-run evidence half of `/evidence/` (§10) — with a
  richer failure signal (redacted observation summary + candidates) pending screenshots.
- **Reference-project lesson applied:** put the "did an action occur" record at the last
  boundary before the driver, not in the caller; make evidence observational and fail closed so
  it can never become a second authority or a reason to repeat a financial action.
- **What our implementation improves/clarifies:** one recorder object is the only thing the
  surface, the gate and the engine share; redaction is a property of the persistence boundary
  (the in-memory result stays debuggable); evidence failure has its own code and its own
  before/after-dispatch semantics.
- **Proof:** `tests/evidence/*`, `tests/surface/test_playwright_surface.py` (M5 section: order,
  refusal before/after the driver, dead port counts as dispatched), `tests/policy/test_action_gate.py`
  (observer before `act`, refusal prevents dispatch, `dispatch` signature unchanged),
  `tests/replay/test_engine.py` (chronologies, every evidence-failure row, sentinel),
  `tests/replay/test_replay_live.py` (on-disk chronology in real Chromium),
  `tests/cua/test_boundaries.py` (M5 section), `evidence/replay/*/events.jsonl`.

## Decision corrections worth explaining

| Initially proposed | Corrected to | Why it matters |
|---|---|---|
| Implement the full enum vocabulary (`FailureCode`, `GateDecision`, `TerminalStatus`, `ControlOwnerState`, …) in Milestone 1 as "shared vocabulary" | Only `ActionType`, `SurfaceElement`, `SurfaceSnapshot` now; everything else is defined in its owning module at its own milestone | Freezing runtime semantics before the code that exercises them exists invites churn and silently pre-decides later milestones. Empty, docstring-only locations preserve the architecture without that risk. |
| `FailureCode` listed `DEAD_END` and `MODEL_ERROR` | Those belong to `StopReason` (discovery); `FailureCode` (replay) must not include them | Discovery stop reasons and replay failure codes are different vocabularies; mixing them would blur the discovery/replay boundary the whole design rests on. Forward note — `FailureCode` is not built yet. |
| Plan's route table once showed "No member found for ." | Implementation always renders the requested id: "No member found for M404." | The not-found text is a business-outcome detector input; it must be stable and carry the id. |
| `.gitignore` as one of several scaffold files | `.gitignore` with `.env` excluded written **before** any other file | A real Gemini smoke test was completed before Bundle B (D06); credential availability is runtime/environment-specific and no credential is committed, so the first commit had to be incapable of tracking one. (Reworded at M5: the earlier wording claimed a credential "exists locally", which is not a property of the repository.) |
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
| `RunResult` validates `outputs` keys against the artifact's declared outputs | `RunResult` enforces only self-contained invariants; `ReplayEngine` proves output completeness before constructing SUCCESS | The result does not own the artifact's output declaration; a cross-object check belongs to the component that holds both objects. |
| `RunResult.inputs` echoing the bound inputs | Removed; nine fields only | Every terminal result would otherwise repeat member identifiers (and later, sensitive inputs) for no consumer; persistence and redaction are the EvidenceWriter's. |
| Engine `except Exception -> SURFACE_ERROR` | `SurfaceDriverError` at the boundary, translated by `PlaywrightSurface`; engine catches `SurfaceDriverError \| UnknownRefError` only | A programming error mislabelled as a browser failure would hide bugs behind a plausible failure code. |
| `config.base_url + step.route` | `build_destination(origin-only base, validated absolute path)` + segment-safe `bind_route` | Permissive joining lets a route replace the origin; the gate still authorizes the result, but the destination must be well-formed by construction. |
| Partial READ outputs kept on FAILURE "for debugging" | `outputs == {}` on BUSINESS_OUTCOME and FAILURE | A caller must never mistake a value read before a failed checkpoint for a trustworthy capability output. |
| Zero-model proof as an in-process import guard | Fresh-interpreter subprocess with the guard installed before any `cua` import (plus the structural tests) | Modules already imported by the test process would make an in-process guard vacuous. |
| `KnownOutcome.terminal_status` "becomes `replay.TerminalStatus`" | Stays a string literal; agreement asserted by a test | `artifact/` importing `replay/` would invert the dependency direction (`replay -> artifact`). |
| Record `GATE_DECISION` from the engine after `gate.dispatch()` returns | An abstract `GateObserver` notified inside `dispatch()` after `authorize`, before `act` | Recording afterwards would place the decision *after* `ACTION_COMPLETED` in the chronology, which reads as "dispatched before authorised"; authorising twice to record first would be wasteful and let the recorded decision differ from the acted-on one. The observer cannot alter the result and is not a `dispatch` parameter. |
| Count an action as dispatched, then notify evidence | Notify (`on_dispatched`) first; only on success increment `dispatched_actions`, invalidate refs, call the driver | An action must count as attempted only once its attempt is durable; a refused notice must leave the surface untouched. A dead port after a recorded notice still counts (M4 invariant kept). |
| Evidence failure surfaced as a flag on an otherwise successful result | `FAILURE / EVIDENCE_ERROR`, outputs dropped, one failure per run, no second write, no redispatch | "You own the proof": a run whose proof cannot be written is not proven; and an evidence failure must never become a reason to act again, nor recurse into writing about itself. |
| Redact the whole event | Redact the `payload` only | Envelope fields are system-generated identifiers and enums; rewriting them could corrupt the chronology key or the discriminator on an absurd input, while all observed text lives in the payload. |
| Engine marks every surface runtime error inside `act()` as `dispatched=True` (M4) | `dispatched` taken from the recorder's dispatch count on that path | `UnknownRefError` and a failing locator query happen *before* the driver op; the recorder is the ground truth for whether the boundary was crossed. |
| Persist identifiers masked (`M1***`) or removed | Deterministic name-tagged placeholders (`<input:member_id>`) from the bound runtime inputs | Masking leaks; removal makes routes and alerts unreadable; the placeholder is non-reversible, deterministic, and keeps evidence useful. Unknown secrets in arbitrary prose remain a documented V1 limit rather than an implied DLP capability. |

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
| policy/artifact/hitl/surface/domain/replay never reach `cua.llm`, `cua.discovery`, or `google`, transitively | `tests/cua/test_boundaries.py::test_deterministic_layers_never_reach_the_llm_layer`, `::test_replay_never_reaches_playwright_or_the_llm_layer_transitively` |
| The same checked-in artifact replays M1001 → `15275.00` and M1002 → `4120.75` in real Chromium, four dispatches each, artifact byte-identical afterwards | `tests/replay/test_replay_live.py::test_success_reads_the_savings_balance[*]` |
| M404 is BUSINESS_OUTCOME / MEMBER_NOT_FOUND at `s3_search`, never a timeout or target failure; the READ is never dispatched | `::test_m404_is_member_not_found_business_outcome`, `tests/replay/test_engine.py::test_m404_is_a_business_outcome_not_a_failure`, `tests/replay/test_resolver.py::test_known_outcome_detected_while_polling_beats_not_found` |
| `ambiguous_savings` → FAILURE / AMBIGUOUS_TARGET with both candidates reported (no refs) and neither read; a weaker strategy is never consulted after ambiguity | `::test_ambiguous_savings_fails_closed_and_reads_neither_candidate`, `test_resolver.py::test_ambiguity_is_immediate_and_never_falls_through_to_a_weaker_strategy` |
| Policy denial → FAILURE / POLICY_DENIED with zero dispatches (empty policy at `s1`; READ disallowed at `s4` after exactly three) | `::test_empty_policy_denies_before_any_dispatch`, `::test_read_denied_by_policy_after_three_dispatches`, `test_engine.py::test_control_not_owned_denies_with_zero_dispatch[*]` |
| IRREVERSIBLE classification → FAILURE / INTERVENTION_REQUIRED, never dispatched | `test_engine.py::test_irreversible_classification_requires_intervention_and_is_never_dispatched` |
| Replay runs to SUCCESS in a fresh interpreter that refuses to import `google*`, `cua.llm`, `cua.discovery` (scripted and live); `import cua.replay` loads neither Playwright nor an LLM SDK | `tests/replay/test_zero_model.py`, `test_replay_live.py::test_zero_model_live_replay_in_a_fresh_interpreter`, `test_boundaries.py::test_importing_replay_does_not_load_playwright_or_an_llm_sdk` |
| `ReplayDeps` has exactly `{surface, action_gate, clock}`; `replay/engine.py` has no `.act(` call and no broad `except` | `test_boundaries.py::test_replay_deps_has_no_field_that_could_hold_a_model`, `::test_replay_engine_contains_no_act_call_and_no_broad_except`, `::test_surface_act_is_called_only_from_the_action_gate` |
| A real driver failure (dead port) is FAILURE / SURFACE_ERROR naming `SurfaceDriverError`, not an exception; programming errors propagate un-relabelled | `test_replay_live.py::test_failed_load_is_a_surface_error_failure_not_an_exception`, `tests/surface/test_playwright_surface.py::test_driver_failure_is_a_surface_driver_error_not_a_playwright_type`, `test_engine.py::test_unexpected_exceptions_propagate_and_are_never_relabelled[*]` |
| DECIMAL is parsed from the string into `Decimal` with scale preserved; no `float` name in the module; JSON renders it as a string | `tests/replay/test_transforms.py::test_decimal_from_currency_text[*]`, `::test_decimal_never_goes_through_float`, `tests/replay/test_result.py::test_decimal_output_serializes_as_a_string_never_a_float` |
| Outputs are authoritative only on SUCCESS: a READ value is discarded when the final checkpoint fails | `test_engine.py::test_checkpoint_failure_discards_the_partial_output`, `test_result.py::test_failure_requires_failure_and_empty_outputs` |
| Unbound placeholders are errors; route placeholders bind to exactly one segment; external/malformed routes are rejected before any dispatch; base URL is origin-only | `tests/replay/test_binding.py` |
| `route_matches` evaluates the decoded observed path literally; `value_equals` needs exactly one target; checkpoint is ALL-in-order with short-circuit | `tests/replay/test_conditions.py` |
| `ACTION_DISPATCHED` is notified before the driver call; a refusal leaves `dispatched_actions`, the refs and the page untouched; a dead port after a recorded notice counts as dispatched and is followed by `ACTION_FAILED` | `tests/surface/test_playwright_surface.py::test_action_dispatched_precedes_the_driver_call_and_a_refusal_leaves_no_trace`, `::test_refusal_before_a_fill_leaves_the_field_and_the_refs_untouched`, `::test_driver_failure_after_a_recorded_dispatch_is_dispatched_then_failed` |
| A refusal after the driver call propagates (the action happened); `UnknownRefError` produces no dispatch notice; the record carries no ref | `::test_refusal_after_the_driver_call_propagates_and_the_action_did_happen`, `::test_refusal_on_failed_carries_the_driver_error_as_context`, `::test_unknown_ref_produces_no_dispatch_notification`, `::test_successful_action_is_dispatched_then_completed_with_no_ref_in_the_record` |
| The gate notifies its observer once per `dispatch`, after `authorize`, before `act`; a refusing observer prevents the dispatch; `authorize` alone does not notify; `dispatch` has no observer parameter | `tests/policy/test_action_gate.py::test_observer_sees_each_dispatch_decision_once_before_any_act[*]`, `::test_a_refusing_observer_prevents_the_dispatch`, `::test_authorize_alone_does_not_notify_and_the_observer_cannot_change_the_decision`, `::test_the_observer_is_a_constructor_dependency_with_a_null_default` |
| M1001 leaves `[RUN_STARTED, (GATE ALLOW, DISPATCHED, COMPLETED)×4, RUN_COMPLETED]`, seq 1..14, step context on every action event; M404 ends `BUSINESS_OUTCOME, RUN_COMPLETED(status BUSINESS_OUTCOME)` | `tests/replay/test_engine.py::test_m1001_produces_a_coherent_evidence_chronology`, `::test_m404_produces_business_outcome_evidence_and_a_completed_run`, `tests/replay/test_replay_live.py::test_success_reads_the_savings_balance[*]`, `::test_m404_is_member_not_found_business_outcome` |
| Policy denial / REQUIRE_INTERVENTION evidence is `GATE_DECISION` + `RUN_FAILED` with zero `ACTION_DISPATCHED`; ambiguity has no READ dispatch and lists candidates without refs; a driver failure is `DISPATCHED, FAILED` | `test_engine.py::test_policy_denial_evidence_is_a_decision_without_any_dispatch`, `::test_require_intervention_evidence_is_a_decision_without_any_dispatch`, `::test_ambiguity_evidence_has_no_read_dispatch_and_candidates_without_refs`, `::test_driver_failure_evidence_is_dispatched_then_failed`, live L4/L5a/L8 |
| Evidence failure before the driver call → `EVIDENCE_ERROR`, `dispatched=False`, driver-attempt counter 0, page unchanged; after → `dispatched=True`, exactly one attempt, never repeated; on `begin_run` → zero observations; on the terminal write → SUCCESS becomes `EVIDENCE_ERROR` with outputs dropped; nothing is written after the first failure | `test_engine.py::test_evidence_failure_before_the_driver_call_means_the_action_was_not_attempted`, `::test_gate_decision_write_failure_stops_before_the_surface_is_touched`, `::test_evidence_failure_after_the_driver_call_means_attempted_and_never_repeated`, `::test_evidence_failure_after_a_driver_failure_names_both`, `::test_evidence_failure_on_begin_run_fails_before_any_observation`, `::test_terminal_write_failure_turns_success_into_evidence_error_with_outputs_dropped`, `tests/evidence/test_recorder.py::test_after_the_first_failure_nothing_more_is_written_and_every_write_is_refused` |
| `EvidenceError` is never relabelled as a surface or policy failure; a surface not wired to the recorder fails loudly on the first action | `test_engine.py::test_evidence_error_is_never_relabelled_as_a_surface_or_policy_failure`, `::test_a_surface_not_wired_to_the_recorder_fails_loudly_on_the_first_action` |
| A known runtime value (sentinel member id) never survives in persisted evidence — not in the FILL echo, URLs, alert text, or a driver error message that also carries a URL query and a ref | `test_engine.py::test_a_sentinel_runtime_input_is_redacted_from_urls_alerts_and_error_text`, `::test_no_ref_and_no_member_id_survive_in_persisted_evidence` |
| Redaction rules: every listed key and suffix, case/separator-insensitive, whole subtree, look-alikes untouched; URL userinfo/query values/fragment; ref tokens vs identifiers; longest value first; short-value guard; idempotent, deterministic, no digest; unsupported types refused | `tests/evidence/test_redaction.py` (58 cases) |
| Redacted bytes only ever reach disk; one JSON object per line, flushed per event; a run file is never overwritten; append/open/serialise failures are `EvidenceError` with the cause chained; malformed or unredacted lines fail loudly on read | `tests/evidence/test_writer.py` |
| `seq` strictly increasing; step context stamped and cleared; `dispatch_count` increments only after the `ACTION_DISPATCHED` write succeeded; terminal events close the run; events outside a run are refused | `tests/evidence/test_recorder.py` |
| Disk writes exist only in `artifact/store.py` and `evidence/writer.py`; `cua.evidence` reaches neither Playwright nor an LLM layer nor `cua.replay`; no payload model has a ref/element/snapshot field; the listener/observer protocols live below evidence (no cycle) | `tests/cua/test_boundaries.py` (M5 section) |
| The real JSONL writer runs in the zero-model fresh interpreter (scripted and live) with no forbidden module loaded and no member id in the file | `tests/replay/test_zero_model.py`, `test_replay_live.py::test_zero_model_live_replay_in_a_fresh_interpreter` |

`evidence/` now holds `README.md` and four sample replay runs (L1 SUCCESS, L3 BUSINESS_OUTCOME, L4
AMBIGUOUS_TARGET, L5a POLICY_DENIED) written by the live tests through `CUA_EVIDENCE_ROOT`.

## Production and evolution seams

| Deliberately simple in V1 | How it evolves |
|---|---|
| Target app state is in-memory per `create_app()`; `POST /__admin/reset` for test isolation | Stays synthetic by design (D01); a real target replaces the whole app, not the seam |
| Fault mode fixed at process start (CLI/env) | More modes (interstitial, slow load, iframe) added the same way once core evals pass |
| Transfer review state carried in hidden fields, no server session | Intentional legacy-realism; also keeps the app cookie-free for the automation |
| `cua.domain` holds only shapes needed by the next milestone | Each module defines its own models when implemented; boundary test grows into `scripts/verify.sh` |
| Boundary check is a pytest AST scan of `src/cua` | Becomes the fresh-clone `verify.sh` structural assertion (ARCHITECTURE §4) |
| One `Surface` implementation (Chromium via Playwright), single-use per run | `DesktopAccessibilitySurface` behind the same Protocol; descriptors are already role + name + scope (designed, not implemented) |
| Refs resolved by `(role, name, ordinal)` inside the surface | Durable targeting via `TargetDescriptor` strategies is now the `TargetResolver` (M4); refs stay observation-local forever — the resolver returns one only together with the snapshot it belongs to |
| `PlaywrightSurface` notifies a `DispatchListener` before/after each driver op; `NullDispatchListener` for adapter tests | A second surface implements the same three notifications; `ARTIFACT_CAPTURED` (screenshots) joins at the HITL milestone |
| `ReplayDeps = {surface, action_gate, clock, evidence}` — one `EvidenceRecorder` shared with the surface and the gate | `control_owner` is read through the gate today and directly at step 16 for suspension; discovery composes the same recorder with `RunKind.DISCOVERY` (schema bump) |
| REQUIRE_INTERVENTION → FAILURE/INTERVENTION_REQUIRED | Step 16: suspend, create `InterventionRequest`, resume on verified hand-back |
| No re-dispatch of any action; polling only | D16 rules (one SAFE_READ retry, gated modal dismissal) added when a demo run shows the need |
| `RunResult` stays unredacted in memory (debuggable); the terminal event persists its redacted summary | Sensitivity markers on `InputSpec`/`OutputSpec` would let the Redactor treat declared-sensitive outputs like inputs |
| `tag_hint` always `None` | Filled only if a concrete consumer appears |
| `ControlOwner` is a state holder with a test-only constructor state | Step 16 adds the transitions (escalate / accept / done / verified / abort); the gate already denies on any non-AUTOMATION state |
| Policy config is a static JSON file with segment-wise route patterns | Tenant overlays (designed only) vary base origin and route aliases; risk/policy fields have no overlay grammar by construction |
| Artifact validators are schema-level (I1, I2, I4, I5, I6-by-construction) | Step 13's compiler adds trace-based checks (a declared discovery input must not survive as a literal) and `compile_report.json` |
| `KnownOutcome.terminal_status` is a string literal | Stays a literal (`artifact/` must not import `replay/`); agreement asserted by a test |
| Evidence vocabulary 1.0: eight event types, `RunKind.REPLAY` only, no screenshots | Each addition is a `schema_version` bump; readers validate strictly, old files stay valid under their own version |
| Evidence failure = `FAILURE / EVIDENCE_ERROR`, fail closed, one failure per run | `UNKNOWN_COMMIT_STATE` (HITL milestone) reuses the same after-dispatch fact for irreversible steps |

## Current risks / unverified assumptions

- `context_hint` is a surface-level convention (table caption / rowheader / group ancestry). It
  is sufficient for this target; how `TargetDescriptor` strategies use it is decided at step 7.
- Unnamed roles (`alert`) resolve by role + ordinal; unique on these pages, asserted by the
  alignment test, but a page with several unnamed same-role elements would rely on ordinal order.
- The AI-mode ARIA snapshot format is observed behaviour of Playwright 1.62, not a stated
  contract; the reader raises on anything unrecognised, so a format change fails loudly.
- Headed (non-headless) operation — needed for HITL — has not been exercised yet.
- Fresh-clone reproducibility depends on `uv` and a Chromium download.
- Locator-path driver timeouts (`click`/`fill` on a slow page) are translated to
  `SurfaceDriverError` by the same helper as navigation failures, but only the navigation path is
  provoked live; a slow-load fault mode would exercise the other.
- The engine's postcondition polling has only been needed for zero extra observations against
  the local target (every postcondition held on the first observation); the bounded windows are
  proven with the fake clock, not with a slow live page.
- Timing defaults (5 s resolution, 5 s condition, 100 ms poll) are operational guesses; a real
  target may need tuning (tenant overlay "wait tuning" seam).
- The value-redaction rule replaces *known* values only; a very short or very common input value
  could over-redact (guarded below 3 characters), and derived PII in observed prose is not
  detected. Both are documented V1 limits, acceptable only because the target data is synthetic.
- Per-event `fsync` is negligible at ~14 events per run; a long discovery run (25 steps × a few
  events) is still well under a second of I/O, but this has not been measured against a slow disk.
- A surface not wired to the run's recorder is detected on the first *successful* dispatch; if
  the very first driver operation fails on an unwired surface the step is recorded as
  `dispatched=False` (the run fails anyway). The composition root must wire the recorder.

## Next milestone

**Milestone 6 — `LLMClient` + structured action output, `DiscoveryAgent` + normalized trace,
E01** (ARCHITECTURE §15 steps 10–12). Discovery composes the same `EvidenceRecorder` (a
`RunKind.DISCOVERY` schema bump plus the observation/model events it needs), drives every action
through the same `ActionGate.dispatch`, and reuses `Redactor.redact_text` on model intent text.
The boundary test's `google` rule narrows to `llm/` at that point; no provider SDK or credential
is committed. Screenshots and HITL transitions follow at step 16.
