#!/usr/bin/env bash
# Offline verification — fresh clone, no API key, no human, no network beyond loopback.
#
#   scripts/verify.sh              full: lint, structure, every named eval, the whole suite, audit
#   scripts/verify.sh --no-browser skip the real-Chromium proofs (they otherwise run headless)
#
# Every eval below is a committed test that re-derives its claim from code and committed evidence.
# E01 and E08/E09 need a live provider or a human and are therefore audited here from their
# committed runs; scripts/verify_live.sh re-runs them for real.
set -euo pipefail
cd "$(dirname "$0")/.."

# Offline by construction: whatever the shell had, none of it reaches the run.
unset OPENAI_API_KEY CUA_LIVE_API CUA_LIVE_HITL CUA_EVIDENCE_ROOT CUA_CAPABILITIES_ROOT || true

BROWSER=1
for arg in "$@"; do
  case "$arg" in
    --no-browser) BROWSER=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
MARK="not live_api"
if [ "$BROWSER" = 0 ]; then MARK="not live_api and not browser"; fi

command -v uv >/dev/null || { echo "uv is required (https://docs.astral.sh/uv/)" >&2; exit 2; }

step() { printf '\n== %s\n' "$*"; }
PASSED=()
run_eval() {  # run_eval <label> <pytest args...>
  local label=$1; shift
  step "$label"
  uv run pytest -q -m "$MARK" "$@"
  PASSED+=("$label")
}

step "environment"
uv sync --frozen
uv run python -c "import sys; assert sys.version_info >= (3, 12), sys.version"

step "lint"
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts

run_eval "structure: dependency direction, single act() call site, zero-model layers" tests/cua

run_eval "E01  genuine live OpenAI discovery (committed run, offline audit)" \
  tests/evals/test_e01_evidence.py
run_eval "Compiler  deterministic, independent of the handwritten artifact, reproducible" \
  tests/artifact/test_compiler.py tests/artifact/test_compiler_independence.py \
  "tests/evals/test_e02_compile_replay.py::test_committed_generated_artifact_is_reproducible_from_the_committed_e01_evidence"
run_eval "E02  generated artifact replays M1002 with zero model decisions" \
  "tests/evals/test_e02_compile_replay.py::test_e02_generated_artifact_replays_m1002_with_zero_model_decisions" \
  "tests/replay/test_zero_model.py::test_the_generated_artifact_replays_zero_model_on_the_scripted_surface"
run_eval "E03  zero-LLM replay under an import guard (fresh interpreter) + structural" \
  tests/replay/test_zero_model.py \
  "tests/replay/test_replay_live.py::test_zero_model_live_replay_in_a_fresh_interpreter"
run_eval "E04  'no such member' is a BUSINESS_OUTCOME, never a failure" \
  "tests/replay/test_replay_live.py::test_m404_is_member_not_found_business_outcome" \
  "tests/evals/test_e02_compile_replay.py::test_generated_artifact_reports_m404_as_the_declared_business_outcome" \
  "tests/replay/test_zero_model.py::test_business_outcome_under_the_import_guard"
run_eval "E05  ambiguous target fails closed, zero dispatch, never first()" \
  "tests/replay/test_replay_live.py::test_ambiguous_savings_fails_closed_and_reads_neither_candidate" \
  tests/discovery/test_ambiguity.py
run_eval "E06  policy denial before any dispatch, on the resolved target" \
  "tests/replay/test_replay_live.py::test_empty_policy_denies_before_any_dispatch" \
  "tests/replay/test_replay_live.py::test_read_denied_by_policy_after_three_dispatches" \
  tests/policy/test_action_gate.py
run_eval "E07  HUMAN / RETURNING ownership denies automation" \
  "tests/replay/test_engine.py::test_control_not_owned_denies_with_zero_dispatch" \
  "tests/hitl/test_handoff.py::test_the_gate_denies_automation_while_the_shared_owner_is_human_and_returning" \
  "tests/hitl/test_handoff.py::test_returning_still_denies_automation"
run_eval "E08/E09  same-session handoff + no-repeat (committed headed run audit + simulated)" \
  tests/evals/test_e08_evidence.py tests/hitl \
  "tests/replay/test_replay_live.py::test_transfer_reaches_confirm_and_the_gate_requires_intervention_with_zero_dispatch"
run_eval "E10  sentinel runtime inputs and secrets never persist" \
  "tests/replay/test_engine.py::test_a_sentinel_runtime_input_is_redacted_from_urls_alerts_and_error_text" \
  "tests/evals/test_e01_evidence.py::test_public_safety_scan" \
  tests/evidence/test_redaction.py tests/cua/test_offline.py

step "full suite"
uv run pytest -q -m "$MARK"

step "public-artifact audit (committed evidence, capabilities, docs)"
uv run python scripts/public_audit.py

step "summary"
for label in "${PASSED[@]}"; do echo "PASS  $label"; done
echo "PASS  full suite"
echo "PASS  public audit"
if [ "$BROWSER" = 0 ]; then
  echo "NOTE  real-Chromium proofs were skipped (--no-browser); run without it after 'uv run playwright install chromium'"
fi
echo "LIVE/MANUAL — see committed evidence: E01 evidence/discovery/run_e49e4d0cbe09, E08/E09 evidence/replay/run_af80d82668bc (scripts/verify_live.sh re-runs them)"
