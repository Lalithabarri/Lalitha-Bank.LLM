# Project Status

Implementation and presentation ledger. **Not** a source of truth — that order is
[REQUIREMENTS.md](REQUIREMENTS.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md). Updated only after a milestone is verified.

## Project thesis

An LLM discovers a legacy-UI workflow once, under policy. A deterministic engine replays it
forever with zero model decisions, a human in the loop where money moves, and evidence for every
claim. Flagship: `read_savings_balance(member_id)`. Escalation: `transfer_funds(...)`.

## Current milestone

**Milestone 1 — verified, awaiting commit.** Final verification passed (tests, ruff, boundary,
secret/git-safety, scope, and a real GUI browser check). Working tree holds the full milestone
as untracked files; nothing committed yet.

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
- **Git commit:** none yet (all files untracked at time of writing).
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

## Decision corrections worth explaining

| Initially proposed | Corrected to | Why it matters |
|---|---|---|
| Implement the full enum vocabulary (`FailureCode`, `GateDecision`, `TerminalStatus`, `ControlOwnerState`, …) in Milestone 1 as "shared vocabulary" | Only `ActionType`, `SurfaceElement`, `SurfaceSnapshot` now; everything else is defined in its owning module at its own milestone | Freezing runtime semantics before the code that exercises them exists invites churn and silently pre-decides later milestones. Empty, docstring-only locations preserve the architecture without that risk. |
| `FailureCode` listed `DEAD_END` and `MODEL_ERROR` | Those belong to `StopReason` (discovery); `FailureCode` (replay) must not include them | Discovery stop reasons and replay failure codes are different vocabularies; mixing them would blur the discovery/replay boundary the whole design rests on. Forward note — `FailureCode` is not built yet. |
| Plan's route table once showed "No member found for ." | Implementation always renders the requested id: "No member found for M404." | The not-found text is a business-outcome detector input; it must be stable and carry the id. |
| `.gitignore` as one of several scaffold files | `.gitignore` with `.env` excluded written **before** any other file | A real Gemini credential exists locally; the first commit must be incapable of tracking it. |

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
| Every ARCHITECTURE §3 package exists; `cua` imports no `legacy_bank`/`flask`/`playwright`/`google` | `tests/cua/test_boundaries.py` |

No `evidence/` directory exists yet (arrives with the EvidenceWriter milestone).

## Production and evolution seams

| Deliberately simple in V1 | How it evolves |
|---|---|
| Target app state is in-memory per `create_app()`; `POST /__admin/reset` for test isolation | Stays synthetic by design (D01); a real target replaces the whole app, not the seam |
| Fault mode fixed at process start (CLI/env) | More modes (interstitial, slow load, iframe) added the same way once core evals pass |
| Transfer review state carried in hidden fields, no server session | Intentional legacy-realism; also keeps the app cookie-free for the automation |
| `cua.domain` holds only shapes needed by the next milestone | Each module defines its own models when implemented; boundary test grows into `scripts/verify.sh` |
| Boundary check is a pytest AST scan of `src/cua` | Becomes the fresh-clone `verify.sh` structural assertion (ARCHITECTURE §4) |

## Current risks / unverified assumptions

- **ARIA feasibility not yet checked:** the whole D04/D13 targeting strategy assumes Playwright's
  accessibility view exposes `th[scope=row]` + adjacent cell, labelled inputs, and named buttons
  well enough to identify every control. Verified at step 3, not before.
- Fresh-clone reproducibility depends on `uv` being installable; the machine had only Python
  3.9.6 before this milestone.
- `DECIMAL` transform must accept currency-formatted cells (`$2,340.50`); untested until step 7.

## Next milestone

**ARCHITECTURE §15 step 3:** `Surface` contract + `PlaywrightSurface` (add `playwright` dependency
then, not before) + the feasibility check that semantic/ARIA observation identifies every
control the flagship and transfer flows need. If it finds gaps, adjust the target's markup then.
