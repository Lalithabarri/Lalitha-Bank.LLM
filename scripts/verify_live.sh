#!/usr/bin/env bash
# Live verification — the two proofs that cannot run offline. Each writes a NEW run under an
# evidence root of your choice (default: a temporary directory); the committed official runs
# (E01 run_e49e4d0cbe09, E08/E09 run_af80d82668bc) are never rewritten.
#
#   OPENAI_API_KEY=... scripts/verify_live.sh e01          real gpt-5.6-sol discovery of M1001
#   scripts/verify_live.sh e08                             headed browser, you click Confirm
#   scripts/verify_live.sh all
#   EVIDENCE_ROOT=/path scripts/verify_live.sh e01         keep the run
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo "uv is required" >&2; exit 2; }

what=${1:-all}
case "$what" in
  e01|e08|all) ;;
  *) echo "usage: $0 [e01|e08|all]" >&2; exit 2 ;;
esac

ROOT_ARG=()
if [ -n "${EVIDENCE_ROOT:-}" ]; then ROOT_ARG=(--evidence-root "$EVIDENCE_ROOT"); fi

if [ "$what" = e01 ] || [ "$what" = all ]; then
  echo "== E01 live discovery (real OpenAI request; needs OPENAI_API_KEY)"
  [ -n "${OPENAI_API_KEY:-}" ] || { echo "OPENAI_API_KEY is not set" >&2; exit 2; }
  CUA_LIVE_API=1 uv run python -m tests.evals.e01_live_discovery --live ${ROOT_ARG[@]+"${ROOT_ARG[@]}"}
fi

if [ "$what" = e08 ] || [ "$what" = all ]; then
  echo "== E08/E09 same-session human handoff (headed Chromium; needs a terminal)"
  [ -t 0 ] || { echo "stdin must be a terminal for the hand-back" >&2; exit 2; }
  CUA_LIVE_HITL=1 uv run python -m tests.evals.e08_hitl_live --live ${ROOT_ARG[@]+"${ROOT_ARG[@]}"}
fi
