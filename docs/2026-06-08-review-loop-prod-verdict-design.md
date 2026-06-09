# Review Loop v2 — Executable Rendered-Page Verdict + the Standing `rev` Session

**Status:** revised after two adversarial rounds, 2026-06-08 · target release: v3.4.0
**Audits:** two rounds, each 3 Claude lenses (correctness / operational / bloat) +
Codex cross-vendor. Round 1 cut the design to an executable core (most of the first
draft didn't exist in the code and, as mapped to the code, recreated A1). Round 2
found two design holes that would let A1 recur after build (the `present` predicate;
the not-run freeze), the relay-clone hazards, and several substrate gaps — all folded
in below.
**Operator decision (2026-06-08):** `rev` **is** a standing, self-relaying session
(the operator wants continuous, clean-headed eyes on what's being scrutinized, and
already runs many sessions). The audit's job here was not to veto that but to make a
standing `rev` *safe*: its own relay built from scratch (never a clone bolted onto
orc's), its own liveness watch, atomic verdict writes, and a tick lock.
**Touches:** `scripts/spec_ledger.py` (status model, render, verify), the
`verification-loop/` harness (`tick.mjs`, `lib/probe.mjs`, card-schema parsing,
assertion runner), a NEW `rev-watch/` relay (separate from `relay-watch/`),
`skills/rev/SKILL.md` (new), `skills/orc/SKILL.md`, `skills/think/SKILL.md` (sheds
review), `skills/verification-loop/SKILL.md`, `DO-IT.md` (role map). Lineage:
[do-it v2](2026-06-02-doit-v2-design.md),
[build-status ledger](2026-06-03-doit-build-status-ledger-design.md).

## The problem, with proof on screen

The loop certifies *"code matches spec"* but not *"works for a user on the deployed
page,"* and we read a pass as both. Proof, 2026-06-08 — spec 106's A1 (net
region-flow): `/flow-matrix` returns `200` with correct data; the card's evidence
was a `curl`; the blind grader re-ran the `curl`; the verifier marked it RESOLVED.
Every gate says done. It is **dead on the rendered page** — a frontend Zod parse
throw blanks the section. Nothing in the loop ever loaded the rendered page as a
user. Every hollow defect caught by hand that day (558% coverage on the multi-FC
toggle, broken returns thumbnails, overflow) was the same render/interaction class.

## The invariant

> **A spec is closed only by an independent, *executable* observation of the
> rendered, deployed product — never by the builder's evidence, a re-run of it, or
> an LLM asked "does this look right?"**

An LLM reading a whole-page text dump and judging "looks satisfied" has the *same*
structural weakness as the curl→grader gate it replaces. The fix is a deterministic
assertion, with the LLM demoted to a secondary, bounded role.

## What the two audits established (so the next reader knows why these choices)

The current code does not yet have the substrate the invariant needs:

- The verifier writes a **single spec-level** `verdict` and writes `CONFIRMED` inside
  the per-criterion loop on *any* confirmed criterion → a spec with one hollow
  criterion still resolves `accepted`. **It recreates A1.**
- HOLLOW/MISSING/REGRESSION criteria only append to `NEEDS-HUMAN.jsonl`; they **never
  write `REJECTED`** → a derived join has no negative input.
- `dom_assertion` was prose handed to an LLM; **nothing executes it.** `probe.mjs`
  captures a whole-page aria dump + screenshot; the vision judge gets a *string
  saying a screenshot exists*, not image bytes.
- The verifier **never reads the card schema**; `loadCriteria()` regex-guesses type
  from prose → "curl auto-fails a UI criterion" is unenforceable.
- `deployed_sha` falls back to local `git HEAD` → a SHA gate would "confirm" a stale
  binary. `DOM_INTERACTION` is silently downgraded → `interaction_traces` never run.
- **The verifier cron is not installed** — nothing ticks today.
- `accepted` is still hand-writable; the relay watcher (`relay-watch/`) is hardwired
  to orc (`/tmp/orc-active`, reboots with `/orc`) — cloning it naively would reboot a
  `rev` pane **as an orchestrator** (two orcs, one checkout).
- Round 2 design holes: `dom_assertion: present` passes on a blank-but-mounted
  container (A1 recurs); "CONFIRMED iff none not-run" freezes a spec forever when a
  criterion is legitimately unobservable.

## What we keep from today (do not regress)

- **Card as a 1:1 spec mirror** — one row per acceptance criterion. The diffable
  forcing function.
- **The blind grader's independence** for backend/reproducible criteria.
- **Finding → registered spec drains reliably** (558% → 109, transfer_out → 106).
- **The human page spot-check** — now owned by `rev` (see Roles), kept in the closure
  path; what's deferred is closure on the verifier *alone*.

## The spine: terminal state is *derived*, never hand-written

The 076 rule stands: the verifier must never mutate the build ledger. The build
ledger keeps orc's lifecycle (`registered → … → merged → shipped`); the verdict is
written **only** to `ledger/verified/<spec_id>.yml`; the rendered view computes
closure by joining the two, so the two-ledger disagreement is un-representable.

| build status | computed `verified/<id>.yml` spec verdict   | effective status            |
|--------------|---------------------------------------------|-----------------------------|
| `shipped`    | `CONFIRMED` (all observable criteria pass)  | `accepted`                  |
| `shipped`    | `REJECTED` (any criterion failed)           | `needs-rework` (loud, top)  |
| `shipped`    | none yet / incomplete                       | `awaiting-prod`             |
| `shipped`    | none + open `needs-human`                   | `needs-human`               |
| < `shipped`  | (any)                                       | orc's build status, as today|

## Roles: `orc` does, `rev` reviews — two standing, self-relaying sessions

- **`rev` — the standing review session (new).** Boots with `/rev`; a session you
  watch and chat with. It is the continuous supervisor of the verification loop: it
  reads each tick's rendered-page evidence, runs spot-checks, **writes the per-criterion
  `REJECTED`/`CONFIRMED` verdicts** (via `cmd_verify` into the verifier namespace —
  this does *not* touch the build ledger, so 076 holds), files correctives for orc,
  and surfaces the board to the operator. It never touches the build tree, never
  commits, never authors specs.
- **Cron ticks; `rev` supervises — and a lock makes them safe.** The deterministic
  Playwright ticks run on a **cron** (always-on, survives any `rev` reboot). `rev`
  may also trigger an ad-hoc tick when it wants one. Every tick takes a **lockfile**,
  so cron and `rev` can never run Playwright concurrently (no double-run, no torn
  state on the 1-vCPU box). `rev` is the judgment + liveness on top; the executable
  assertion owns the mechanical verdict.
- **`rev` self-relays via its OWN mechanism, built from scratch — not a clone of
  orc's.** A separate `rev-watch/` with its own sentinel (`/tmp/rev-active`), its own
  due-glob, its own relay baton (`docs/sessions/rev-relay.md`), its own log/lock, and
  a hardcoded `/rev` boot — so the watcher physically *cannot* reboot a `rev` pane as
  `/orc`. `rev`'s boot arms `/tmp/rev-active` **and clears stale `rev` sentinels for
  its pane** (the v3.2.1 stale-sentinel fix, applied to `rev` from day one).
- **`rev` has its own liveness watch.** A standing session can't report its own
  death, and the verifier's dead-man's switch only watches *the verifier*. A small
  external check surfaces `REV_DOWN` (and orc's first-moves notes `REV: pane dead` if
  `/tmp/rev-active` no longer maps to a live pane). Two standing sessions ⇒ two things
  that can die silently ⇒ two watches.
- **`rev` writes only to safe places.** The verifier namespace
  (`~/.claude/ledger/verified/`), the verifier `runs/` dir, and its own relay baton.
  It never writes into a project source tree orc may be mid-commit on.
- **`think` sheds the review shape** → pure intake / brainstorm / spec-authoring; its
  "Shape B — Review" moves to `rev`. DO-IT.md's role map becomes orc / rev / think.

## The MVP

### 1. The verifier runs, with dead-man's switches for *both* the verifier and `rev`

Install the cron from `verification-loop/SETUP.md`. Add a **liveness sentinel** (its
own cron) that surfaces `VERIFIER_DOWN` via `spec_ledger.py render` if `PROGRESS.jsonl`
has not advanced in > 90 minutes (3 missed 30-min ticks), and `REV_DOWN` if
`/tmp/rev-active` no longer maps to a live pane. Both watchdogs are separate processes
from the things they watch. (No-cost hygiene on the small box: run the tick's Chromium
under `systemd-run --scope -p MemoryMax=1G`, and offset the tick off the pipeline
windows.)

### 2. Executable DOM assertions per UI criterion; LLM demoted to secondary

- A `criterion_type: ui` carries a **machine-readable** `dom_assertion`:
  `{ url, selector, predicate, forbid_console }`. `predicate` ∈
  `min_rows:N | count_gte:N | text_matches:<re>` for content-bearing sections.
  **`present` alone is forbidden for `ui` criteria** — a blank-but-mounted container
  satisfies `present`, which is exactly the A1 failure. `forbid_console` must include
  `["ZodError","Unhandled"]` for render-throw classes.
- The verifier **runs the assertion in Playwright** (`page.locator(selector).count()`,
  console capture) **before any LLM**. Fail, **or a selector that matches 0
  elements**, → `REJECTED` for that criterion. A `curl`/`grep` auto-fails a `ui`
  criterion.
- **The verifier parses the card schema** (`loadCriteria()` follows the ledger
  `review_card` pointer and reads the `components` rows: `criterion_type`,
  `dom_assertion`, `evidence_type`) — no prose regex for closure. It **fails closed**:
  a UI criterion with no `dom_assertion` row cannot reach CONFIRMED.
- **`orc` authors the `dom_assertion` at card-write time**, not the thinker at
  spec-write time — orc has loaded the rendered page and can pick a stable
  **`data-testid`** selector and a non-trivial predicate. Selectors must be
  `data-testid` (orc adds the attribute when missing), never structural CSS, so they
  don't rot silently. This keeps `dom_assertion` from becoming the new ignored
  `eyeball: yes`.
- The LLM judge is **secondary**: bounded visual questions, real evidence only; it can
  never override a failed deterministic assertion to CONFIRMED.
- `eyeball` collapses into `criterion_type` — no separate route-around-able flag.

### 3. Per-criterion verdicts + an aggregation rule (REJECTED actually written)

- Stop writing spec-level `CONFIRMED` in the per-criterion loop. Persist
  **per-criterion** verdicts in `verified/<id>.yml` under a `criteria:` map;
  `cmd_verify` accepts only `--criterion <id>=<verdict>` from the verifier and
  **computes the spec-level verdict from the full map** (it refuses a caller-supplied
  spec-level verdict).
- **Aggregation rule:** spec = `CONFIRMED` iff every observable criterion is CONFIRMED
  and none REJECTED; `REJECTED` if any criterion REJECTED; else no spec verdict yet
  (→ `awaiting-prod`). A criterion that is genuinely unobservable (no test-tenant
  data) is marked **`not-applicable`** (by `rev`, with a reason) and **excluded** from
  the "every criterion" test — so one perpetual data-gap can't freeze a spec forever.
  A spec with any `not-applicable` renders `accepted (incomplete: N)` so the gap is
  visible, never hidden.
- Any criterion failure (assertion or judge) writes a durable `REJECTED` to
  `verified/` — the input the join needs. Writes are **atomic (tmp-then-rename)** so a
  `rev`/cron context-reset can't tear a verdict file; `render` reads each `verified/`
  file in a try/except so one malformed file can't block the whole board.

### 4. Derived join in the render; `accepted` no longer hand-writable

- `spec_ledger.py render` joins `ledger/*.yml` with `ledger/verified/*.yml`, computes
  the effective-status column per the table, prints a top
  **`❌ NEEDS-REWORK (prod-verified hollow)`** section, and labels the normal
  post-deploy resting state `awaiting-prod`.
- **`accepted` is computed-only:** `cmd_set` refuses `accepted` with a direct error;
  the old `set <id> accepted --by think-review` instruction is removed. Existing
  records already at `status: accepted` are migrated/displayed as **legacy-accepted**
  so `--check` doesn't break on them.
- **Loudness floor as a *separate* alert, not `--check`.** A new
  `spec_ledger.py alert` (run by the liveness cron + shown in `render`) flags any spec
  `awaiting-prod` > 48h with no `PROGRESS.jsonl` advance. It is **not** in `--check` —
  `--check` must stay deterministic for CI (a green run today must not fail in 49h).

### 5. Route `NEEDS-HUMAN` into the ledger view — with resolution state

`NEEDS-HUMAN` items are date-sharded in `runs/<date>/` with no "resolved" marker, so a
naive render would either miss old ones or spam stale ones. Instead, **promote
unresolved escalations into the verdict namespace** (a `needs_human:` block on
`verified/<id>.yml`, or a durable `ledger/needs-human/` store with ids + resolution
state). `render` projects the *unresolved* set into the board; orc consumes them in
first-moves and `rev` ensures they get consumed. No scraping of unbounded date dirs.

### 6. The `rev` session + its own relay (built from scratch)

- `skills/rev/SKILL.md`: boots the standing review session per Roles — supervise the
  loop, read rendered evidence, spot-check, write `REJECTED`/`CONFIRMED`/`not-applicable`
  per criterion, file correctives, hand the operator the board. Read-only on code.
- `rev-watch/` (new, separate from `relay-watch/`): a role-parameterized relay — own
  sentinel `/tmp/rev-active`, own due-glob, own baton `docs/sessions/rev-relay.md`,
  own log/lock, boot `/rev`. The relay scripts take `ROLE / ACTIVE_FILE / DUE_GLOB /
  RELAY_FILE / BOOT_COMMAND / LOG / LOCK` so orc's and rev's never cross. `rev`'s boot
  arms its sentinel and clears its own stale sentinels.
- Remove "Shape B — Review" from `skills/think/SKILL.md`; update DO-IT.md's role map
  to orc / rev / think.

## Deferred — and the condition to revisit each

- **`deployed_sha` staleness gate** — needs a real per-surface `/version` endpoint
  returning the *running* sha. **Concrete MVP interim (not just "accepted risk"):** a
  minimum **10-minute delay** between a spec's `shipped` timestamp and its first
  verifier tick, so a Vercel/Hetzner deploy is actually live before assertion. Revisit
  with a real `/version` gate (then: no `CONFIRMED` without a SHA match).
- **`interaction_traces` + Playwright interaction driver** — `DOM_INTERACTION` is
  unimplemented; the human exploratory pass already caught the 558% class. Revisit
  after the DOM-assertion path is proven on ≥5 real specs.
- **`rework_count` dance ceiling** — `rework` has never fired once; don't build a
  ceiling for a loop that hasn't run. Revisit after rework fires on ≥2 specs.
- **`severity` P0–P3 + drain rule** — real triage value but unrelated to A1 and prone
  to inflation; revisit as a small follow-on (P0/P1/P2, default P2, sort not gate).
- **Closure on the verifier alone** (dropping `rev`'s spot-check from the closure
  path) — revisit once the executable verifier is proven on ≥5 real specs.
- **Vision judge with real image bytes** — known weakness; item 2 demotes the LLM so
  it matters less. Fix alongside the interaction driver.

## Acceptance criteria

1. **A1 is the proof.** With an executable `dom_assertion` (`min_rows`/`text_matches`,
   `forbid_console:[ZodError]`) for 106's region-flow criterion, a forced tick writes
   a per-criterion `REJECTED` to `ledger/verified/106-*.yml`, the aggregation makes the
   spec `REJECTED`, and `render` shows 106 under the top `NEEDS-REWORK` section, never
   `accepted`. End-to-end, on the real deployed page.
2. The verifier cron is installed; the liveness cron surfaces `VERIFIER_DOWN` when
   `PROGRESS.jsonl` is stale > 90 min, and `REV_DOWN` when `/tmp/rev-active` maps to no
   live pane. Demonstrated by stopping each.
3. A `criterion_type: ui` cannot reach a `CONFIRMED` per-criterion verdict without a
   passing executable `dom_assertion`; `present`-only is rejected for ui; a 0-match
   selector is `REJECTED`; a curl-only ui criterion auto-fails; a ui criterion with no
   `dom_assertion` row fails closed. The verifier reads the card-schema `components`
   (no prose regex). Tests cover each.
4. The spec verdict is computed in `cmd_verify` from the `criteria:` map; a
   caller-supplied spec-level verdict is refused; a spec with ≥1 REJECTED criterion is
   REJECTED even if others CONFIRMED; a `not-applicable` criterion is excluded from
   the all-pass test and renders `accepted (incomplete:N)`. Unit test asserts the
   aggregation and the full join table.
5. Any criterion failure writes a durable, **atomic** `REJECTED` to `verified/`;
   `render` tolerates a malformed `verified/` file without aborting; nothing relies on
   `NEEDS-HUMAN.jsonl` to represent a rejection.
6. `accepted` is computed-only: `cmd_set accepted` is refused; no skill hand-writes it;
   legacy `accepted` records are migrated/displayed without breaking `--check`. Test
   covers the refusal.
7. `render` shows a top `NEEDS-REWORK` section and an `awaiting-prod` bucket; the 48h
   loudness floor lives in `spec_ledger.py alert`, **not** `--check` (a `--check` run
   is time-invariant). Tests cover both.
8. Unresolved escalations are promoted to a durable store with resolution state and
   projected into the board; orc's first-moves doc states it reads them; resolved ones
   drop off.
9. `rev` boots from `skills/rev/SKILL.md`; `rev-watch/` (separate from `relay-watch/`)
   arms `/tmp/rev-active`, clears its own stale sentinels, and reboots the pane with
   `/rev` (never `/orc`) on the context-ceiling trigger — demonstrated by an
   arming+reboot cycle that does **not** disturb orc's pane. Cron and `rev` ticks are
   mutually excluded by a lock. "Shape B — Review" is removed from think; DO-IT.md's
   role map reads orc / rev / think.
10. DO-IT.md, CHANGELOG (v3.4.0), and the DESIGN.md decision log are updated; deferred
    items and their revisit conditions are recorded.

## Out of scope

- The deferred list above (each with its named revisit trigger).
- Replacing Playwright; re-architecting the cross-vendor judge.
- Number-allocation (shipped v3.3.0).
- reference-instance rollout beyond the standing rule: land + deploy the
  `spec_ledger.py`/verifier/`rev-watch` changes in `$REPO_ROOT` first, then
  flip skills; never touch that tree while an orc owns it.
