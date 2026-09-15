# Surface Feasibility — Milestone 2 evidence

Implementation evidence, not architecture. Records what a real Chromium, driven by Playwright
through `PlaywrightSurface`, actually exposed of the Legacy Bank Operations Console. Every
"ACTUAL OBSERVATION" row below is copied from snapshot dumps written by
`tests/surface/test_playwright_surface.py` (`CUA_FEASIBILITY_DUMP=<dir>`) during a passing run;
nothing is transcribed from expectations.

## Setup actually used

| Item | Value |
|---|---|
| Playwright Python | 1.62.0 (`playwright>=1.62` in `pyproject.toml`) |
| Browser | Chromium 151.0.7922.34 (Chrome Headless Shell, `playwright install chromium`, build 1234), headless |
| Target | `legacy_bank` served in-process by `werkzeug.serving.make_server` on an ephemeral port, fresh app state per test module; normal mode and `ambiguous_savings` mode |
| Semantic mechanism | `page.aria_snapshot(mode="ai")` — one call per `observe()` — read by `cua.surface.aria.read_snapshot()` (raw text → parse → `SurfaceElement[]`) |
| Other driver calls per observe | `page.title()` — one call. `page.url` is a property (no round trip) |
| **Measured driver calls per `observe()`** | **2, on all 27 observations dumped** (`_measured.driver_calls` in every dump file) |
| Action mechanism | `page.goto`, `locator.click / fill / select_option(label=) / input_value / inner_text`, `page.wait_for_load_state` |
| Ref → element resolution | Internal, observation-local map `ref → (role, accessible_name, ordinal)`; resolved with documented `page.get_by_role(role, name=…, exact=True).nth(ordinal)` (unnamed elements: `get_by_role(role).nth(ordinal)`, ordinal over all elements of that role). No `aria-ref` or other selector syntax used |
| APIs deliberately not used | `page.accessibility` (removed in Playwright 1.57); CSS/XPath selectors; `first()` |
| Dependencies added | `playwright` only. No YAML library: the AI-mode text is one node per line with two-space nesting, parsed by a small line reader (`cua/surface/aria.py`) |

## Per-state results

Element rows are `ref · role · accessible_name · value · context_hint` exactly as dumped
(`enabled=True` and `tag_hint=None` on every element in every state, omitted for brevity).

### A. Member search

| | |
|---|---|
| PAGE / STATE | `GET /members/search`, title `Member Search - LegacyBank Operations Console` |
| EXPECTED SEMANTIC IDENTITY | textbox "Member ID"; button "Search" |
| ACTUAL OBSERVATION | `e10 · textbox · 'Member ID' · None · 'group: Look up member'`<br>`e11 · button · 'Search' · None · 'group: Look up member'`<br>`e6 · heading · 'Member Search' · None · None`<br>`e4 · link · 'Member Search' · None · 'navigation: Main'` |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | `<fieldset><legend>` surfaces as `group "Look up member"` and gives the controls a scope. 4 elements total; 2 driver calls. |

### B. Member detail — M1001

| | |
|---|---|
| PAGE / STATE | `GET /members/M1001`, title `Member M1001 - LegacyBank Operations Console` |
| EXPECTED SEMANTIC IDENTITY | member heading; Checking row/value; Savings row/value; link "Transfer funds" |
| ACTUAL OBSERVATION | `e6 · heading · 'Member M1001 — Alice Morgan' · None · None`<br>`e27 · cell · '$2,340.50' · '$2,340.50' · 'table: Accounts > row: Checking'`<br>`e30 · cell · '$15,275.00' · '$15,275.00' · 'table: Accounts > row: Savings'`<br>`e32 · link · 'Transfer funds' · None · None` |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | Chromium exposes `<tr>` **unnamed** and `<table>` **unnamed**; the row/table relationship is recovered from the `rowheader` child and the `caption` child respectively — this is what `context_hint` encodes. 16 elements; 2 driver calls. |

### C. Member detail — M1002 (same strategy, no member-specific code)

| | |
|---|---|
| PAGE / STATE | `GET /members/M1002`, title `Member M1002 - LegacyBank Operations Console` |
| EXPECTED SEMANTIC IDENTITY | identical query shape as B: heading `Member <id> — <name>`, `cell` with `context_hint = "table: Accounts > row: Checking|Savings"`, link "Transfer funds" |
| ACTUAL OBSERVATION | `e6 · heading · 'Member M1002 — Rahul Iyer' · None · None`<br>`e27 · cell · '$980.00' · '$980.00' · 'table: Accounts > row: Checking'`<br>`e30 · cell · '$4,120.75' · '$4,120.75' · 'table: Accounts > row: Savings'`<br>`e32 · link · 'Transfer funds' · None · None` |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | B and C are asserted by one shared helper (`assert_member_detail`) parametrized over the two members. Different values, same representation. |

### D. Transfer form

| | |
|---|---|
| PAGE / STATE | `GET /members/M1001/transfer`, title `Transfer Funds - Member M1001 - LegacyBank Operations Console` |
| EXPECTED SEMANTIC IDENTITY | From account; To account; Amount; Review transfer |
| ACTUAL OBSERVATION | `e24 · combobox · 'From account' · 'Checking' · 'group: Transfer details'`<br>`e28 · combobox · 'To account' · 'Savings' · 'group: Transfer details'`<br>`e32 · textbox · 'Amount' · None · 'group: Transfer details'`<br>`e33 · button · 'Review transfer' · None · 'group: Transfer details'` |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | `<select>` is exposed as `combobox` with child `option` nodes carrying `[selected]`; the selected option's name becomes the element `value`. `<label for>` supplies the accessible names. 17 elements; 2 driver calls. |

### E. Transfer review

| | |
|---|---|
| PAGE / STATE | after FILL Amount `500.00` → CLICK "Review transfer" (through `Surface.act`), title `Review Transfer - Member M1001 - LegacyBank Operations Console` |
| EXPECTED SEMANTIC IDENTITY | transfer summary; Confirm transfer; Cancel |
| ACTUAL OBSERVATION | `f1e6 · heading · 'Review Transfer — Member M1001' · None · None`<br>`f1e16 · cell · 'Checking' · 'Checking' · 'table: Transfer summary > row: From account'`<br>`f1e19 · cell · 'Savings' · 'Savings' · 'table: Transfer summary > row: To account'`<br>`f1e22 · cell · '$500.00' · '$500.00' · 'table: Transfer summary > row: Amount'`<br>`f1e24 · button · 'Confirm transfer' · None · None`<br>`f1e26 · link · 'Cancel and go back' · None · None` |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | Reached by real actions, not direct navigation. The test then re-observes member detail and confirms balances unchanged (review ≠ commit). Hidden inputs are not exposed (correct). |

### F. Member not found (M404)

| | |
|---|---|
| PAGE / STATE | FILL Member ID `M404` → CLICK "Search"; stays on `/members/search`, title `Member Search - LegacyBank Operations Console` |
| EXPECTED SEMANTIC IDENTITY | the business state "No member found for M404." observable declaratively |
| ACTUAL OBSERVATION | `f1e7 · alert · '' · 'No member found for M404.' · None`<br>`f1e6 · heading · 'Member Search' · None · None`<br>`f1e11 · textbox · 'Member ID' · 'M404' · 'group: Look up member'`<br>outline: `banner: LegacyBank Operations Console — Member Services v2.3 \| h1: Member Search \| alert: No member found for M404. \| Member ID \| paragraph: Enter the member number …` |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | `<p role="alert">` surfaces as an `alert` element whose `value` is the message — a future `known_outcomes[]` detector can match `role=alert` + `text_present`. The textbox retains `M404`, so the requested id is observable alongside the message. |

### G. Ambiguous savings (`ambiguous_savings` mode)

| | |
|---|---|
| PAGE / STATE | `GET /members/M1001` on the fault-mode server |
| EXPECTED SEMANTIC IDENTITY | TWO genuine Savings candidates, neither dropped nor chosen |
| ACTUAL OBSERVATION | `e29 · rowheader · 'Savings' · 'Savings' · 'table: Accounts > row: Savings'`<br>`e30 · cell · '$15,275.00' · '$15,275.00' · 'table: Accounts > row: Savings'`<br>`e32 · rowheader · 'Savings' · 'Savings' · 'table: Accounts > row: Savings'`<br>`e33 · cell · '$250.00' · '$250.00' · 'table: Accounts > row: Savings'`<br>(Checking: exactly one `cell`, `e27 · '$2,340.50' · 'table: Accounts > row: Checking'`) |
| PASS / GAP | **PASS** |
| MARKUP CHANGE REQUIRED? | No |
| NOTES | `find(snapshot, role="cell", context_hint="table: Accounts > row: Savings")` returns **2** elements with distinct refs and values; the same query in normal mode returns 1. Alignment test (`test_G_every_ref_resolves_to_the_element_it_came_from`) resolved all 18 refs on this page and confirmed each maps to the element it came from — the two Savings cells resolve to two different `<tr>`s. The information a later `TargetResolver` needs to return `AMBIGUOUS_TARGET` is preserved; no resolver exists yet. |

## Semantic context (table relationships)

Chromium names neither `<tr>` nor `<table>`. The relationship *row Savings → value $15,275.00* is
therefore expressed by `context_hint`, computed from the aria tree: nearest named ancestors among
`table` (caption or name), `row` (rowheader child name), `group`, `form`, `dialog`, `region`,
`navigation`, outermost first, ` > `-joined. Observed values: `table: Accounts > row: Savings`,
`table: Transfer summary > row: Amount`, `group: Transfer details`, `navigation: Main`. No CSS,
XPath, tag, or DOM path is part of the snapshot.

## Transient refs — observed behaviour

- Refs are minted by the driver per snapshot. In the dumps the *same page* carried `e30` for the
  Savings cell on a first load and `f1e…`-prefixed refs after a form submission in the same
  session — the string is not a stable identity and is never treated as one.
- `observe()` replaces the whole ref map; `act()` invalidates it. Tests prove: acting twice
  without re-observing → `StaleObservationError`; a ref from a previous page → `UnknownRefError`;
  `NAVIGATE` invalidates refs; a closed surface refuses `observe()`, `open()` and its old
  `session_id`; a fresh surface gets a new `session_id` and restarts `step_index` at 1.

## Limitations discovered

- `tag_hint` is `None` on every element (V1 decision): filling it required one extra
  `evaluate_all` per role per observe and an ordinal-alignment fallback; no consumer needs it
  (D13 descriptors are role + name + scope). Revisit only with a concrete consumer.
- Unnamed roles (`alert`) are resolved by role + ordinal over all elements of that role; unique on
  these pages, and the alignment test would fail loudly if it stopped being so.
- The AI-mode snapshot format is observed, not contractually specified; the reader raises on any
  line it does not understand rather than dropping it. The seven captured fixtures under
  `tests/surface/fixtures/` are **regression inputs for the pure reader tests only** — the
  evidence in this document comes from the live browser runs above.
- Only Chromium was run. No claim is made about Firefox, WebKit, or any desktop accessibility
  surface — the `Surface` protocol is the seam, not a demonstrated second implementation.
- `page.wait_for_load_state()` after each action is the only waiting; bounded declarative waits
  are step 7.

## Target markup

**No `legacy_bank` template or HTML was modified.** Every required control and state was
exposed by the existing semantic markup on the first live run; no gap was demonstrated, so the
markup-change policy (capture → explain → smallest standards-based fix → rerun) was not
triggered. Verified by `git diff --stat -- src/legacy_bank` being empty at the end of the
milestone.

## Test evidence

`uv run pytest -q` → 98 passed, 98 collected: `tests/legacy_bank` 32 + `tests/cua/test_domain.py`
6 (Milestone 1, unchanged) + `tests/cua/test_boundaries.py` 24 (Milestone 1's 13 retained; the
blanket "no `playwright` anywhere in cua" rule was replaced by an exact single-file allowlist
plus annotation and public-API checks) + `tests/surface/` 36 (new). Browser subset
`tests/surface/test_playwright_surface.py` → 16 passed in ~5 s against real Chromium.
