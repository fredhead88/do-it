# Review Loop v2 (MVP) — Executable Rendered-Page Verdict Owns Closure

**Status:** revised post-audit 2026-06-08 · target release: v3.4.0
**Audit:** 3 Claude lenses (correctness / operational / bloat) + Codex cross-vendor,
2026-06-08. The first draft was cut hard: most of its mechanisms did not exist in
the code, and as mapped to the code it *recreated* the A1 failure. This MVP keeps
the invariant and ships the smallest executable core that actually catches A1.
**Touches:** `scripts/spec_ledger.py` (status model, render, verify), the
`verification-loop/` harness (`tick.mjs`, `lib/probe.mjs`, the criterion schema),
`relay-watch/` (second pane), `skills/rev/SKILL.md` (new), `skills/orc/SKILL.md`,
`skills/think/SKILL.md` (sheds review), `skills/verification-loop/SKILL.md`,
`DO-IT.md` (role map). Lineage:
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

## The invariant (unchanged)

> **A spec is closed only by an independent, *executable* observation of the
> rendered, deployed product — never by the builder's evidence, a re-run of it, or
> an LLM asked "does this look right?"**

The audit's central lesson: an LLM reading a whole-page text dump and judging
"looks satisfied" has the *same* structural weakness as the curl→grader gate it
replaces. The fix is a deterministic assertion, with the LLM demoted to a
secondary, bounded role.

## What the audit changed (so the next reader knows why this is small)

The first draft assumed a substrate that isn't there:

- The verifier writes a **single spec-level** `verdict` and writes `CONFIRMED`
  inside the per-criterion loop on *any* confirmed criterion — so a spec with one
  hollow criterion still resolves `accepted`. **It recreates A1.**
- HOLLOW/MISSING/REGRESSION criteria only append to `NEEDS-HUMAN.jsonl`; they
  **never write `REJECTED`** — so a derived join has no input.
- `dom_assertion` was prose handed to an LLM; **nothing executes it.** probe.mjs
  captures a whole-page aria dump + screenshot; the vision judge is handed a
  *string saying a screenshot exists*, not image bytes.
- The verifier **never reads the card schema** (`criterion_type`, `evidence_type`,
  `eyeball`); `loadCriteria()` regex-guesses from prose — so "curl auto-fails a UI
  criterion" was unenforceable.
- `deployed_sha` falls back to local `git HEAD` (the committed sha, not the running
  process) — the SHA gate would "confirm" against a stale binary.
- `DOM_INTERACTION` is silently downgraded to static DOM — `interaction_traces`
  are never driven.
- **The verifier cron is not installed on the box** — it is not running at all.
- `accepted` is still a hand-writable status; `rework_count`, `severity`, the
  `NEEDS-REWORK` section, `awaiting-prod` do not exist.

So the MVP fixes the *substrate* and ships one executable enforcement point.
Anticipatory machinery is deferred until the loop demonstrably works once.

## What we keep from today (do not regress)

- **Card as a 1:1 spec mirror** — one row per acceptance criterion. Real forcing
  function, the diffable artifact.
- **The blind grader's independence** for backend/reproducible criteria.
- **Finding → registered spec drains reliably** (558% → 109, transfer_out → 106).
- **The human page spot-check stays** — but it moves to a dedicated home (`rev`,
  see Roles), not the thinker. It is the only path that has caught A1, so it stays a
  standing check; what is deferred is *closure on the verifier alone* (a
  compressed-verdict-only flow), never the check itself.

## The spine (unchanged): terminal state is *derived*, never hand-written

The 076 rule stands: the verifier must never mutate the build ledger. So
"the verifier owns the verdict" means the build ledger keeps orc's lifecycle
(`registered → … → merged → shipped`), the verifier writes **only**
`ledger/verified/<spec_id>.yml`, and the rendered view computes closure by joining
the two. The two-ledger disagreement becomes un-representable.

| build status | computed `verified/<id>.yml` spec verdict     | effective status            |
|--------------|-----------------------------------------------|-----------------------------|
| `shipped`    | `CONFIRMED` (every criterion confirmed)       | `accepted`                  |
| `shipped`    | `REJECTED` (any criterion failed)             | `needs-rework` (loud, top)  |
| `shipped`    | none yet                                       | `awaiting-prod`             |
| `shipped`    | none + open `needs-human`                      | `needs-human`               |
| < `shipped`  | (any)                                          | orc's build status, as today|

## Roles: `orc` does, `rev` reviews (the standing pair)

The review role becomes **first-class** — a persistent session that *only*
reviews, sitting next to orc the way a pair does: one builds, one reviews.

- **`rev` — the review session (new).** Boots with `/rev`. It is the brain that
  *drives* the verification-loop: it runs the ticks, reads the rendered-page
  evidence, performs the human spot-check, and files correctives / writes the
  `REJECTED` verdicts that flip specs to `needs-rework`. It is the standing "second
  body" that notices when orc stops consuming correctives or when the verifier dies.
  It never touches the build tree or commits (that is orc's alone) and never authors
  specs (the 076 rule) — an unhappy review produces a corrective for orc, or a memo.
- **`rev` self-relays exactly like orc.** It gets the same context-watch →
  auto-`/clear` → reboot loop, generalized from orc's `relay-watch/`
  (`orc-token-watch.py` + `relay-watch.sh`) by parameterizing the watcher on a
  second pane sentinel (`/tmp/rev-active`) that reboots with `/rev`. orc and rev are
  the two standing, self-clearing panes; `think` is on-demand.
- **`think` sheds the review shape.** With `rev` owning review, the thinker returns
  to pure intake / brainstorm / spec-authoring. Its old "Shape B — Review" is
  removed and re-homed in `rev`.
- **The autonomous engine stays.** `verification-loop/tick.mjs` (Playwright + the
  deterministic assertions) is `rev`'s *hands*; `rev` is the *judgment + liveness*
  on top. The executable assertion still owns the mechanical verdict; `rev` owns the
  residual taste calls and keeps the loop alive and consumed.

## The MVP — six items

### 1. The verifier actually runs, with a dead-man's switch

Nothing else matters if it's dead. Install the cron from `verification-loop/SETUP.md`
on the box. Add a **liveness sentinel**: a small check (its own cron) that fails
loudly — appends a `VERIFIER_DOWN` line surfaced by `spec_ledger.py render` and
notifies — if `PROGRESS.jsonl` has not advanced in > 90 minutes (3 missed 30-min
ticks). The verifier cannot self-report being dead, so the watchdog is a separate
process from the verifier.

### 2. Executable DOM assertions per UI criterion; LLM demoted to secondary

- A `criterion_type: ui` carries a **machine-readable** `dom_assertion`:
  `{ url, selector, predicate, forbid_console }` where `predicate` is one of
  `present | min_rows:N | text_matches:<re> | count_gte:N`, and `forbid_console`
  lists patterns that must NOT appear in the console (e.g. `ZodError`,
  `Unhandled`). This is the field A1 needed.
- The verifier **runs the assertion in Playwright** (`page.locator(selector)`,
  `.count()`, console capture) **before any LLM**. Fail → write `REJECTED` for that
  criterion. A `curl`/`grep` is an auto-fail for a `ui` criterion — now enforceable
  because the verifier reads the field.
- **The verifier must parse the card schema**, not regex-guess from prose:
  `loadCriteria()` reads the review-card `components` rows (`criterion_type`,
  `dom_assertion`, `evidence_type`). One machine-readable criterion schema, shared
  by handover/card/verifier.
- The LLM judge becomes **secondary**: only for bounded visual questions, and only
  when handed real evidence. It can never *override* a failed deterministic
  assertion to CONFIRMED.
- `eyeball` collapses into `criterion_type`: a `ui` criterion *requires* the
  rendered assertion; there is no separate, route-around-able `eyeball` flag.

### 3. Per-criterion verdicts + an aggregation rule (REJECTED actually written)

- Stop writing spec-level `CONFIRMED` inside the per-criterion loop. Persist
  **per-criterion** verdicts in `verified/<id>.yml` under a `criteria:` map
  (extend `cmd_verify` to take/record `--criterion <id>=<verdict>`).
- **Aggregation rule:** spec verdict = `CONFIRMED` **iff every criterion is
  CONFIRMED and none is REJECTED/not-run**; `REJECTED` if any criterion is REJECTED;
  otherwise no spec verdict yet (→ `awaiting-prod`). Write the spec-level verdict
  once, derived from the map — never as a side effect of the first green criterion.
- On any criterion failure, a durable `REJECTED` reaches `verified/` — the input
  the join needs. (Today failures only hit `NEEDS-HUMAN.jsonl`.)

### 4. Derived join in the render; `accepted` no longer hand-writable

- `spec_ledger.py render` joins `ledger/*.yml` with `ledger/verified/*.yml`, computes
  the effective-status column per the table, prints a top
  **`❌ NEEDS-REWORK (prod-verified hollow)`** section, and labels the normal
  post-deploy resting state `awaiting-prod`.
- **`accepted` becomes computed-only:** remove it from the writable lifecycle —
  `cmd_set` refuses `accepted` (it is derived), and the old
  `set <id> accepted --by think-review` instruction is removed from the skills.
- **Loudness floor:** `render`/`--check` flags any spec `awaiting-prod` for
  > 48h with no `PROGRESS.jsonl` advance — so `awaiting-prod` can't become a quiet
  graveyard (the failure mode that replaced the 33-card backlog otherwise).

### 5. Route `NEEDS-HUMAN` into the ledger view

`NEEDS-HUMAN.jsonl` is a flat file in `runs/` that orc's first-moves never reads —
the corrective→orc pipeline the thinker used to bridge. `render` consumes
`NEEDS-HUMAN.jsonl` and projects unresolved escalations into the board, so orc sees
correctives in its normal first-moves scan, and `rev` (item 6) is the standing
session that ensures they get consumed — closing the Two-Body gap.

### 6. The `rev` session — orc's paired review twin, self-relaying

Stand up `rev` as a first-class role (see Roles above):

- A `rev` skill (`skills/rev/SKILL.md`) booting the review session: drive the
  verifier, read rendered evidence, spot-check, file correctives / write `REJECTED`,
  hand the operator the compressed verdict. Read-only on code, never commits, never
  authors specs.
- Generalize `relay-watch/` to a second pane: the watcher arms on `/tmp/rev-active`
  and reboots that pane with `/rev` on the same context-ceiling trigger orc uses.
  orc and rev are the two standing self-clearing panes.
- Remove "Shape B — Review" from `skills/think/SKILL.md`; the thinker is now pure
  intake/authoring. Update DO-IT.md's role map (3 roles → orc / rev / think, with
  rev as the standing review twin).

## Deferred — and the condition to revisit each

- **`deployed_sha` staleness gate** — needs a real per-surface version endpoint
  (`/version` returning the *running* sha); local `git HEAD` is a no-op.
  *Residual risk accepted for MVP:* a stale Hetzner binary / Vercel lag can still be
  judged; partly mitigated by item 2's `forbid_console` + probe's existing 502
  retry. **Revisit first**, once a `/version` endpoint exists.
- **`interaction_traces` + Playwright interaction driver** — `DOM_INTERACTION` is
  unimplemented; the human exploratory pass already caught the 558% class. Revisit
  after the DOM-assertion path is proven on ≥5 real specs.
- **`rework_count` dance ceiling** — `rework` has never fired once; don't build a
  ceiling for a loop that hasn't run. Revisit after rework fires on ≥2 specs.
- **`severity` P0–P3 + drain rule** — real triage value but unrelated to A1 and
  prone to inflation; revisit as a small follow-on (P0/P1/P2, default P2, sort not
  hard-gate).
- **Closure on the verifier alone** (compressed-verdict-only, dropping `rev`'s
  rendered-page spot-check from the closure path) — revisit once the executable
  verifier is proven on ≥5 real specs. *Narrowing the thinker* off review is done in
  this release; what stays for now is `rev`'s human spot-check.
- **Vision judge with real image bytes** — known weakness; item 2 demotes the LLM
  so it matters less. Track it; fix alongside the interaction driver.

## Acceptance criteria

1. **A1 is the proof.** With the executable assertion for 106's region-flow
   criterion, a forced verifier tick writes `REJECTED` to `ledger/verified/106-*.yml`,
   and `spec_ledger.py render` shows 106 under the top `NEEDS-REWORK` section, never
   `accepted`. (End-to-end, on the real deployed page.)
2. The verifier cron is installed and a liveness sentinel surfaces `VERIFIER_DOWN`
   when `PROGRESS.jsonl` is stale > N minutes. Demonstrated by stopping the verifier
   and seeing the flag.
3. A `criterion_type: ui` cannot reach a `CONFIRMED` per-criterion verdict without a
   passing executable `dom_assertion`; a curl-only UI criterion auto-fails. The
   verifier reads `criterion_type`/`dom_assertion` from the card schema (no prose
   regex for closure). Test covers both.
4. Spec-level verdict is computed from the per-criterion `criteria:` map by the
   aggregation rule; a spec with ≥1 REJECTED criterion is `REJECTED` even if others
   are CONFIRMED. A unit test asserts the aggregation and the full join table.
5. Any criterion failure writes a durable `REJECTED` to `verified/`; nothing relies
   on `NEEDS-HUMAN.jsonl` to represent a rejection.
6. `accepted` is computed-only: `cmd_set accepted` is refused; no skill instructs a
   hand-write; `accepted` renders solely from `shipped ∧ CONFIRMED`. Test covers the
   refusal.
7. `render` shows a top `NEEDS-REWORK` section and an `awaiting-prod` bucket; `--check`
   fails on any spec `awaiting-prod` > 48h with no progress. Test covers the floor.
8. `render` projects unresolved `NEEDS-HUMAN.jsonl` escalations into the board; orc's
   first-moves doc states it reads them.
9. A `rev` session boots from `skills/rev/SKILL.md` and drives the verifier;
   `relay-watch` arms on `/tmp/rev-active` and reboots that pane with `/rev` on the
   context-ceiling trigger (demonstrated by an arming + reboot cycle). "Shape B —
   Review" is removed from the think skill, and DO-IT.md's role map reads
   orc / rev / think.
10. DO-IT.md, CHANGELOG (v3.4.0), and the DESIGN.md decision log are updated; deferred
    items and their revisit conditions are recorded.

## Out of scope

- The deferred list above (each with its named revisit trigger).
- Replacing Playwright; re-architecting the cross-vendor judge.
- Number-allocation (shipped v3.3.0).
- AS-instance rollout choreography beyond the standing rule: land + deploy the
  `spec_ledger.py`/verifier changes in `/opt/albert-scott` first, then flip skills;
  never touch that tree while an orc owns it.
