# Evidence

Structured, redacted run evidence written by `cua.evidence` (ARCHITECTURE §10, D19). One directory
per run: `evidence/<run_kind>/<run_id>/events.jsonl`, one JSON object per line, append-only,
redacted **before** any byte reaches disk, every event `fsync`ed. No run file is ever overwritten.

## Reading a run

Each line is an `EvidenceEvent` (`schema_version` 1.0 for the Milestone 5 replay samples, 1.1 from
Milestone 6; every version stays readable under its own number): `seq` is the chronology key,
`event_type` is one of

```
RUN_STARTED  GATE_DECISION  ACTION_DISPATCHED  ACTION_COMPLETED  ACTION_FAILED
BUSINESS_OUTCOME  RUN_COMPLETED  RUN_FAILED                                   (replay, 1.0)
DISCOVERY_STARTED  OBSERVATION  MODEL_CALL  DISCOVERY_ENDED                  (discovery, 1.1)
```

`GATE_DECISION` is written by the ActionGate after it decides and before anything reaches the
driver. `ACTION_DISPATCHED` is written by the Surface immediately *before* the driver call: it is
the ground truth that an automated action was attempted, not that it completed. Exactly one
`ACTION_COMPLETED` or `ACTION_FAILED` follows.

**An `ACTION_DISPATCHED` with no paired `ACTION_COMPLETED`/`ACTION_FAILED` must be read as "the
action may have executed."** `ACTION_FAILED` means the driver operation did not complete cleanly,
not that it had no effect.

`RUN_COMPLETED` closes a run that ended in `SUCCESS` **or** a declared `BUSINESS_OUTCOME` (a
legitimate business result is not a crash); `RUN_FAILED` closes a `FAILURE`. The terminal
payload carries every step's record, the failure or outcome detail, and `dispatched_actions`.

## What is redacted

Runtime input values appear only as name-tagged placeholders (`<input:member_id>`), in URLs,
typed values, page titles and messages alike. Values under secret-shaped keys (`password`,
`token`, `api_key`, …) become `[REDACTED]`; URL credentials, query values and fragments are
stripped; transient observation refs never have a field to live in and are additionally rewritten
to `<ref>` in free text. Declared outputs (a balance) are kept — they are what the replay is for.
V1 limit: derived PII in observed prose (a member's *name* in a heading) is not detected; the
target data is synthetic.

## Replay runs (Milestone 5 samples, real Chromium against the local Legacy Bank)

| run | scenario | events | dispatched | terminal |
|---|---|---|---|---|
| `replay/run_1f937cc46214` | `read_savings_balance@1.0.0` for a valid member (L1) | 14 | 4 | `RUN_COMPLETED` · `SUCCESS` · `savings_balance = 15275.00` |
| `replay/run_ba0632aa97f2` | absent member (L3) | 12 | 3 | `BUSINESS_OUTCOME` · `MEMBER_NOT_FOUND` at `s3_search`, READ never dispatched |
| `replay/run_e71cd37cfd6f` | `ambiguous_savings` fault mode (L4) | 11 | 3 | `RUN_FAILED` · `AMBIGUOUS_TARGET` at `s4_read_savings`, both candidates listed, neither read |
| `replay/run_66f4e9961684` | empty policy (L5a) | 3 | 0 | `RUN_FAILED` · `POLICY_DENIED` — one `GATE_DECISION(DENY)`, zero `ACTION_DISPATCHED` |

Produced by `CUA_EVIDENCE_ROOT=evidence uv run pytest tests/replay/test_replay_live.py -k "<test>"`
(the same opt-in mechanism as Milestone 2's `CUA_FEASIBILITY_DUMP`).

## Discovery run — official E01 (Milestone 6, real OpenAI, real Chromium, real Legacy Bank)

| run | scenario | events | model calls | dispatched | terminal |
|---|---|---|---|---|---|
| `discovery/run_e49e4d0cbe09` | `read_savings_balance(member_id)` discovered live by `gpt-5.6-sol` — first masked attempt, `policy/legacy_bank.json` verbatim, entry route `/members/search` only | 24 | 5 (0 corrective retries) | 4 | `DISCOVERY_ENDED` · `GOAL_REACHED` · `goal_satisfied: true` · `savings_balance = 15275.00` |

What it proves, line by line: `DISCOVERY_STARTED` records the goal, the declared input *names*,
provider `openai`, model `gpt-5.6-sol`, the single entry route shown to the model and
`mask_bound_inputs: true`. Each of the five `MODEL_CALL` events is one real provider HTTP attempt
(transport retries are fixed at 0) with the reported model id, a distinct `resp_…` response id,
token counts and latency, the decision in semantic terms (NAVIGATE the entry route → FILL the
"Member ID" textbox with `INPUT_REF(member_id)` → CLICK "Search" → READ the cell in
`table: Accounts > row: Savings` → FINISH) and the application validator's verdict. Every
`ACTION_DISPATCHED` is immediately preceded by a `GATE_DECISION ALLOW` for that action and followed by
`ACTION_COMPLETED`; an `OBSERVATION` precedes every decision. `DISCOVERY_ENDED` carries the
deterministic verifier's reason and the normalized trace, which records the FILL as
`INPUT_REF(member_id)` and observed routes as `/members/{member_id}` — literal-free by construction,
so the persisted trace equals the in-memory one. The runtime value never appears: it is
`<input:member_id>` in the typed value, the URL and the heading. Not persisted, by design: prompts,
completions, provider response objects, outbound HTTP bodies, refs, the credential. The four
provider-boundary checks (no member id in any model request or outbound body, no key in any body,
`store: false` on every body) were verified in memory at run time and are not re-derivable from
this file. `tests/evals/test_e01_evidence.py` re-runs the file audit in the normal suite; the run
was produced by `CUA_LIVE_API=1 CUA_EVIDENCE_ROOT=evidence uv run pytest tests/evals -m live_api`.

Intervention runs will sit beside these under `interventions/` once the HITL milestone lands.
