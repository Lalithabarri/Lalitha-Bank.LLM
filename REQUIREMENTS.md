# REQUIREMENTS

Source: official Interface.ai assignment (authoritative) + approved
03_REQUIREMENTS_BASELINE. Where anything conflicts, the assignment wins.
No requirements added beyond these sources.

---

## 1. Project objective

**REQUIRED.** Build a small, real system that lets an AI agent operate a
legacy back-office UI that has no API: an LLM discovers a workflow live,
once, against a real UI; the discovered flow is saved as a typed, reusable
capability; that capability replays later with zero LLM involvement,
deterministically; risky/irreversible actions are guarded and secrets never
leak; when the system can't safely proceed, a human takes over the live
session and hands it back.

The target application is a stand-in the implementer chooses, not a real
bank system.

---

## 2. Required end-to-end behavior

**REQUIRED.** A complete vertical slice touching every item in Section 3,
not a polished subset:

goal → LLM-driven discovery run that completes it → saved capability
artifact → deterministic replay with input params, outputs, and
error/outcome handling → a human-escalation path that can take over the
live session → evidence for both runs.

Cutting depth or polish on any one piece is allowed; omitting a whole
Section 3 capability is not.

---

## 3. Discovery requirements

**REQUIRED**
- A genuine, live, LLM-driven loop: observe the real UI → decide → act,
  repeated until the goal is met or a stopping condition is hit (max
  steps, timeout, dead-end).
- The agent must actually interact with a real UI — click, type, navigate,
  read state. It must not be pre-scripted or simulated.
- Bounded execution: the loop must terminate on some condition; it cannot
  run unbounded.
- The mechanism for perceiving/acting on the UI is the implementer's
  choice, but must be biased toward working with no clean DOM, no stable
  selectors, and no API — legacy apps cannot be assumed to have any of
  these.
- At least one genuine discovery run against a live surface, with its own
  API access, is non-negotiable: "the discovery run has to be real," and
  cannot be described instead of demonstrated.

**DESIGN CHOICE** (assignment explicitly leaves open)
- LLM provider/model and how the agent loop is prompted/structured.
- The specific computer-use mechanism (Playwright, Selenium, screenshot +
  coordinates, accessibility APIs, OS automation, etc.).
- The target application itself, provided it exercises a non-trivial
  multi-step flow (e.g. search → detail → action, or a multi-field form
  with confirmation).

---

## 4. CapabilityArtifact requirements

**REQUIRED.** After a successful discovery run, the system must emit a
capability artifact that is:
- **Typed** — not a raw transcript.
- **Serializable.**
- **Versioned.**
- **Parameterized** — callable later with different input values, not
  hardcoded to the values used at discovery time.
- Composed of **ordered actions/steps**.
- Built with **robust target/element descriptions**, including reasoning
  about robustness (i.e., not merely whatever the discovery run happened
  to click).
- Declares **typed input parameters** and **typed outputs/data to
  extract**, including their shape.
- Declares a **checkpoint or success condition**.
- **Reviewable** — both a human and a calling agent must be able to
  understand what it does, what it needs, and what it returns.
- **Decoupled from the raw model transcript** — the artifact must stand
  on its own; replay must not silently require transcript-only
  information.

The assignment names this schema directly as "a focal point of the
evaluation."

**DESIGN CHOICE** — the exact schema, its field names, its serialization
format, and how it is stored are all the implementer's call.

---

## 5. Deterministic replay requirements

**REQUIRED**
- Given a saved artifact plus input parameters, replay it **without
  invoking the LLM for any decision**.
- Use **stable element/control targeting** at replay time.
- **Verify the checkpoint/success condition** rather than assuming an
  action worked.
- **Return the declared outputs.**
- **Detect and handle runtime conditions**: validation errors, "record
  not found," permission denials, unexpected dialogs, session timeouts,
  slow/failed loads.
- The terminal, caller-facing result must be **exactly one of three
  things**: success (with outputs), a known business outcome, or a
  failure with enough detail to debug (what step, what was expected, what
  was observed).
- A **recoverable condition** (e.g. dismissing a known interstitial,
  waiting/retrying a transient load) is a runtime-handling category the
  engine resolves *during* execution — it is never itself a fourth
  terminal category returned to the caller. If it resolves, execution
  continues; if it can't be resolved, it becomes a failure (or grounds
  for escalation per Section 8).
- A legitimate business result (e.g. "no such member") must be reported
  as a business outcome, not misreported as a crash — the assignment
  names conflating the two as "the most common design mistake."

**DESIGN CHOICE** — the specific locator/fallback strategy, the exact wait
mechanism, and the internal retry policy are the implementer's call, as
long as the above outcomes hold.

---

## 6. Safety requirements

**REQUIRED**
- Enforce an **explicit, configurable allowlist**: permitted
  domains/routes and allowed action types. The agent must not be able to
  act outside it.
- **Distinguish safe/reversible actions from risky/irreversible ones**,
  and handle the risky class conservatively.
- **Never persist secrets or raw sensitive data** (credentials, tokens,
  full PII) into artifacts or logs; redact appropriately. This is
  regulated financial data, per the assignment's own framing.

**DESIGN CHOICE** — exactly how risky/irreversible actions are handled
(block, require confirmation, or flag) is the implementer's call, but
must be justified in the write-up.

---

## 7. Evidence / observability requirements

**REQUIRED**
- Produce a **structured log** of what the agent did and why, sufficient
  to understand and debug a run.
- Produce **at least one richer signal on failure** (screenshot, DOM
  snapshot, trace — implementer's choice).

**DESIGN CHOICE** — exact log schema, storage format, and which richer
signal to capture.

**OPTIONAL** — a replay that deliberately hits an error or exceptional
state, and a short screen recording, are both explicitly "ideally
include" / "welcome but optional," not required.

---

## 8. Human-in-the-loop requirements

**REQUIRED**
- **Detect a stuck/blocked state** and raise an intervention request to a
  human operator, carrying context: which capability/goal, the current
  step, the current state or screenshot, and why it stopped.
- Let the human operate the **same live session** the automation was
  using — not a fresh one.
- The human must be able to **perform manual steps**, then **hand control
  back** so the run can resume or complete.
- **Preserve context and evidence across the handoff**, and **record what
  the human did**.
- There must be a **clear way to know who is (or should be) in control**
  of the session at any point.
- The handoff mechanism and control-transfer model must be **real**, not
  a TODO — a mocked operator UI is acceptable, but the pause/cede/resume
  behavior itself is not.
- A full real-time co-browsing console is explicitly **out of scope** —
  only a minimal but real handoff is required.

**DESIGN CHOICE** — the operator interface itself may be minimal or
mocked, as long as the underlying pause/handoff/resume mechanism is real.

---

## 9. Heterogeneity / multi-tenant design requirements

**REQUIRED, as a written design only — not an implementation.**
REPORT.md heading 4 must present a credible design covering:
- **Surface abstraction**: how the artifact schema and replay engine
  would extend from the chosen surface to a legacy web app and/or a
  desktop app, and what the seam is between "how we perceive/act on a
  surface" and "the recorded flow."
- **Multi-tenant reuse**: how an artifact could be represented for
  reuse/safe specialization across tenants running the same vendor app,
  rather than being re-recorded per tenant, and how per-tenant/version
  drift would be detected/managed.

**EXPLICIT NON-REQUIREMENT** — actually implementing multi-tenant support
or a desktop surface is not expected. The core abstractions simply must
not be architected in a way that forecloses this design story.

---

## 10. Required repository deliverables

**REQUIRED**
- Public GitHub repository.
- `/README.md` covering: how to set up and run the system (keys/config
  needed, how to run without live services if applicable), and a demo
  path — the exact command(s) to run the agent on a goal, then replay the
  resulting artifact.
- `/REPORT.md` (~1–3 pages), using **exactly these seven headings, in
  exactly this order**: Architecture; Artifact schema; Determinism &
  error handling; Heterogeneity & multi-tenant; Escalation & handoff;
  Safety; Cuts.
- `/evidence/` — a saved example artifact, plus logs from both a
  discovery run and a replay run.
- At least one genuine LLM-driven discovery run against a live surface,
  with evidence in `/evidence/` — non-negotiable.
- Submission: push to the public repo and email the link to
  assignments@interface.ai, repo URL on its own line, no zip.

**OPTIONAL**
- A replay that hits an error or exceptional state (bad input, not-found
  result, or injected/simulated failure).
- A short screen recording.

---

## 11. Evaluation priorities

**REQUIRED context, not an action item.** The assignment weighs these
roughly in this order:
1. System design (artifact schema and replay contract are central).
2. Correctness of the core loop.
3. Robustness & error handling.
4. Human-in-the-loop escalation.
5. Generalization to the real environment.
6. Safety & data handling.
7. Code quality.
8. Communication.

**Explicit anti-signal**: feature breadth, framework name-dropping, and
building scaling infrastructure (queues, clusters, multi-tenant plumbing)
are not rewarded. Designing abstractions that *could* scale is valuable;
prematurely *building* that infrastructure is not.

---

## 12. Explicit non-requirements / cuts allowed by the assignment

**EXPLICIT NON-REQUIREMENTS**
- Actual multi-tenant implementation.
- Actual desktop-surface implementation.
- Queues, clusters, or multi-tenant plumbing/infrastructure — named
  directly as an anti-signal.
- A full real-time co-browsing console.
- A polished product — the assignment asks for "a focused effort, not a
  polished product."

**OPTIONAL/STRETCH** (pick at most one or two; explicitly lower priority
than the core, per the assignment's own ordering)
- Agent-facing capability interface (callable capability catalog).
- Code generation from an artifact.
- Confidence & approval scoring/gating.
- Assisted fallback (bounded, single-step, policy-checked LLM recovery on
  replay failure).
- Canonicalization / cross-tenant reuse demonstration.
- Multi-run stability signal.

Cuts are acceptable if intentional, documented, and left at a clean,
real seam — but a cut may reduce *depth or polish*, never eliminate a
whole Section 3 capability.

---

## 13. V1 acceptance checklist

A submission satisfies these requirements when all of the following are
true:

- [ ] A real, live, LLM-driven discovery run completes a genuine goal
      against a real UI, with evidence.
- [ ] A typed, versioned, serializable, parameterized capability artifact
      is produced from that run, decoupled from the raw transcript.
- [ ] The artifact replays with the same inputs and produces the same
      outputs, deterministically, with **zero LLM calls**.
- [ ] Replay correctly separates a business outcome, a resolved
      recoverable condition, and a hard failure — never conflating a
      legitimate business result with a crash.
- [ ] An out-of-allowlist action is actually blocked at runtime.
- [ ] Risky/irreversible actions are handled conservatively, per a
      justified policy.
- [ ] No secret, token, or full PII appears in any persisted artifact or
      log.
- [ ] A structured log exists for both runs, plus at least one richer
      failure signal.
- [ ] A stuck/blocked state triggers a real handoff to a human operating
      the same live session, with context preserved, the human's actions
      recorded, and a working hand-back.
- [ ] REPORT.md exists with the seven required headings, in order,
      including a credible (not implemented) heterogeneity/multi-tenant
      design.
- [ ] README documents setup and the exact demo command sequence.
- [ ] `/evidence/` contains a saved artifact and logs from both a
      discovery run and a replay run.
- [ ] Repository is public; submission follows the stated format.

---

REQUIREMENTS FROZEN
ASSIGNMENT REMAINS ULTIMATE AUTHORITY
