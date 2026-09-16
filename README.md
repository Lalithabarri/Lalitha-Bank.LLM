# interface-ai-cua

An LLM discovers a legacy-UI workflow **once**, under policy. A deterministic engine replays the
compiled capability with **zero model decisions**. Irreversible actions are designed to require
human intervention, and every production claim is backed by explicit verification evidence.

Canonical documents: [REQUIREMENTS.md](REQUIREMENTS.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/). The project targets Python 3.12; uv can install a
managed Python version when needed.

```sh
uv sync
uv run playwright install chromium   # one-time browser download (Chromium only)
uv run pytest -q
uv run ruff check src tests
```

Browser-driven tests (`-m browser`) skip with an explicit reason if Chromium is not installed.
Surface feasibility evidence: [SURFACE_FEASIBILITY.md](SURFACE_FEASIBILITY.md).

## Legacy Bank Operations Console (synthetic target)

```sh
uv run legacy-bank                                  # http://127.0.0.1:8000
uv run legacy-bank --fault-mode ambiguous_savings   # duplicate "Savings" row on member detail
```

Synthetic members: `M1001`, `M1002` (valid), `M404` (absent). All data is fake and in-memory.

## Human-in-the-loop (verified, same session)

The irreversible "Confirm transfer" is never dispatched by automation. The replay engine suspends
inside the same run, the human clicks it in the **same headed browser session**, hands back in the
terminal, and the engine verifies the real page state before continuing past the step.

```sh
# manual, headed, no model — needs a real terminal; click "Confirm transfer" when prompted, type done
CUA_LIVE_HITL=1 uv run python -m tests.evals.e08_hitl_live --live --evidence-root evidence
```

Official E08/E09 evidence: `evidence/replay/run_af80d82668bc/` (`events.jsonl` + `artifacts/`
pre/post screenshots); re-audited offline by `tests/evals/test_e08_evidence.py`. Simulated
handoff tests: `tests/hitl/test_handoff.py`.

_Full demo path and evidence documentation arrive with later milestones._
