# interface-ai-cua

An LLM discovers a legacy-UI workflow **once**, under policy. A deterministic engine replays it
**forever**, with zero model decisions, a human in the loop where money moves, and evidence for
every claim.

Canonical documents: [REQUIREMENTS.md](REQUIREMENTS.md) → [ARCHITECTURE.md](ARCHITECTURE.md) →
[ENGINEERING_DECISIONS.md](ENGINEERING_DECISIONS.md).

## Setup

Requires [uv](https://docs.astral.sh/uv/). Python 3.12 is installed by uv automatically.

```sh
uv sync
uv run pytest -q
uv run ruff check src tests
```

## Legacy Bank Operations Console (synthetic target)

```sh
uv run legacy-bank                                  # http://127.0.0.1:8000
uv run legacy-bank --fault-mode ambiguous_savings   # duplicate "Savings" row on member detail
```

Synthetic members: `M1001`, `M1002` (valid), `M404` (absent). All data is fake and in-memory.

_Full setup, demo path, and evidence documentation arrive with later milestones._
