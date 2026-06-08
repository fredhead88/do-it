# Review Loop v2 — The Rendered-Page Verdict Owns Closure

**Status:** design, approved 2026-06-08 · target release: v3.4.0
**Supersedes the close-out behaviour in:** `skills/orc/SKILL.md` (close-out gate),
`skills/think/SKILL.md` (Shape B review), `skills/verification-loop/SKILL.md`,
`scripts/spec_ledger.py` (render + status model). Design lineage:
[2026-06-02 do-it v2](2026-06-02-doit-v2-design.md),
[2026-06-03 build-status ledger](2026-06-03-doit-build-status-ledger-design.md).

## The problem, with proof on screen

The current loop is a solid *"did orc build the spec"* check and a bad *"does it
work for the user"* check — and we treat a pass as if it were both. The two are
different claims, and the gap between them is where shipped work goes hollow.

The living proof is spec 106's A1 (net region-flow), observed 2026-06-08:

- The endpoint `/flow-matrix` returns `200` with correct data.
- The review card's evidence was a `curl`. The blind grader re-ran the `curl`.
- The verifier re-confirmed A1 `RESOLVED` the same day (it reads endpoints + DB).
- **Every gate in the loop says A1 is done and good.**
- And it is **dead on the rendered page** — a frontend Zod parse throw
  (conservation object vs `NetworkConservationSchema`) blanks the section.

Nothing in the `card → grade → accept` path ever loaded the rendered, deployed
page as a user. A frontend throw that blanks a section is invisible to a curl, to
a re-run of that curl, and to an endpoint/DB reader. Every hollow defect caught by
hand on 2026-06-08 was of this class — the 558% projected-coverage on the multi-FC
toggle, the broken returns thumbnails, the contained-vs-page overflow — all
render/interaction items the loop waved through as "done, eyeball deferred."

Two more structural symptoms from the same day:

- **Two ledgers that disagree, build-side wins.** orc marks `shipped`/`accepted`
  on merge+deploy; the verifier marks the same spec `HOLLOW` on prod; they never
  reconcile, and orc pulls its next task from the build ledger. Real-effect fixes
  literally weren't in orc's queue until the thinker learned to bounce them into
  Outstanding by hand.
- **Stale closure.** 33 cards sit awaiting a thinker walk; prod drifts within the
  day (108 backend committed but the live Hetzner process stale; 109 union-fix
  committed but the live Vercel page still 558%). A card graded against a state
  that has since changed certifies the past.
- **`rework` has never fired** (zero rework records). A return path that never
  returns is not yet a loop — the walk isn't rigorous or frequent enough, and
  there is no automatic back-pressure when prod is wrong.

## The invariant

> **A spec is closed only by an independent observation of the rendered, deployed
> product — never by the builder's evidence or a re-run of it.**

Everything below is a corollary of that one rule.

## What we keep (it works — do not regress it)

- **Card as a 1:1 spec mirror** — one `components:` row per acceptance criterion,
  no omissions. It is a real forcing function (it made orc account for all 7 of
  074's criteria) and the diffable artifact the reconcile gate needs.
- **The blind grader's independence** — sees only criterion + evidence, never the
  builder's reasoning. For backend, reproducible-evidence criteria, it is good.
- **`evidence_type` must match `criterion_type`** — the right instinct against
  "grep says it's there" hollowness. v2 makes it binding, not advisory.
- **Finding → registered spec drains reliably.** The thinker's 558% finding became
  109 and orc shipped a fix within the hour; 100's `transfer_out=0` became 106.
  The machinery downstream of a registered spec is not the problem.

## The spine decision: terminal state is *derived*, never hand-written (Approach A)

The hard constraint is the 076 rule: **the verifier must never mutate the build
ledger.** So "the verifier owns the verdict" cannot mean "the verifier writes
`accepted` into the build record."

**Chosen — A, derived/computed.** Nobody hand-writes `accepted`. The build ledger
keeps orc's lifecycle (`registered → planned → building → merged → shipped`). The
verifier writes **only** its own `ledger/verified/<spec_id>.yml` (the namespace
`cmd_verify` already owns). `Outstanding` becomes a **join** of the two, and the
effective status is *computed*. The join key is the ledger verdict vocabulary —
`VALID_VERDICT = {CONFIRMED, REJECTED}` in `spec_ledger.py`. The verification
loop's richer internal labels (`HOLLOW` / `MISSING` / `REGRESSION`) all resolve to
a written `REJECTED` verdict with the label carried as the reason; `UNCLEAR` and
`TASTE` never write a verdict — they raise a `needs-human` escalation instead:

| build status | `verified/<id>.yml` verdict       | effective (rendered) status |
|--------------|-----------------------------------|-----------------------------|
| `shipped`    | `CONFIRMED`                       | `accepted`                  |
| `shipped`    | `REJECTED` (hollow/missing/regr.) | `needs-rework` (loud, top)  |
| `shipped`    | none yet / SHA-stale              | `awaiting-prod`             |
| `shipped`    | none + open `needs-human`         | `needs-human`               |
| < `shipped`  | (any)                             | orc's build status as today |

The two-ledger disagreement becomes **structurally impossible**: there is one
computed view, and the prod verdict wins by construction. The verifier touches
only its own namespace, so 076 holds.

**Rejected:** *B — reconciliation write* (a step promotes `shipped → accepted` on
CONFIRMED): simpler to read, but reintroduces a writer that can drift/race and
flirts with the 076 hazard. *C — status quo + manual walk*: it is precisely what
A1 disproves.

## Design

### 1. State machine / one reconciled view (#1, #4)

- `shipped` is **non-terminal** by definition — "orc built and deployed it,"
  nothing more. Closure is `accepted`, and only the table above produces it.
- `spec_ledger.py render` joins `ledger/*.yml` with `ledger/verified/*.yml`,
  computes the effective-status column, and prints a top section
  **`❌ NEEDS-REWORK (prod-verified hollow)`** above everything else.
- A new `awaiting-prod` computed bucket replaces "shipped-awaiting-review" as the
  normal post-deploy resting state. It is *verifier-paced* (automatic), not
  human-paced.
- orc First-moves: `needs-rework` outranks new specs (same precedence `rework`
  has today), and within it P0/P1 outrank P2/P3 (see §4).

### 2. Evidence contract — type-locked, render-graded for UI (#2)

- `criterion_type: ui` **requires** evidence
  `{deployed_url, deployed_sha, dom_assertion, screenshot_sha256, look_for}`.
  `dom_assertion` is a concrete predicate on the rendered DOM (e.g. "the A1 region
  table renders ≥1 row", "no error boundary / empty-section fallback present").
- A `curl`/`grep` is an **auto-fail** for a UI criterion — enforced at the verdict
  gate, not merely advised.
- `eyeball: yes` is **blocking**. The criterion is `pending-eyeball` until a
  rendered observation exists; "deferred to thinker" + done is banned, fails the
  blind close-out grader, and **cannot produce a CONFIRMED verdict**. This single
  rule is what catches A1.
- Backend criteria keep the signed `{url, status, body_sha256, body_excerpt}`
  record; UI gets the rendered equivalent above. Both are re-executable, not prose.

### 3. Post-deploy SHA gate — the verifier's tick-0 check (#1, staleness)

- Every verdict records `deployed_sha`. Before judging anything, the verifier
  asks: **does the live URL serve this SHA's behaviour?**
- If live ≠ expected (Hetzner process stale, Vercel build lag), the spec is
  `awaiting-prod` and surfaced as *"deployed SHA not live yet"* — never
  `accepted`. This is exactly the 108-BE-stale / 109-FE-lag class hit on
  2026-06-08, made un-closable until prod actually serves the build.

### 4. Severity + drain rule (#5, #6)

- Every spec carries `severity: P0|P1|P2|P3`:
  - **P0** — prod-down or money-wrong *now*.
  - **P1** — correctness / wrong numbers (e.g. 088's 47% ads undercount).
  - **P2** — incomplete / UX defect.
  - **P3** — cosmetic.
  Set at handover (`think` / `spec-handover`) or on a verifier corrective.
- `Outstanding` sorts by severity within each bucket; `needs-rework` P0/P1 float
  to the absolute top of the board.
- **orc drain rule** (First-moves): clear all P0/P1 (rework + needs-rework + new)
  before pulling any P2/P3 or any new feature. A money-wrong bug never sits behind
  a cosmetic one.

### 5. Exercise-beyond-written-criteria (#3, the 105 class)

The 558% defect appeared only when the multi-FC toggle was exercised — no written
criterion covered it. Two complementary mechanisms:

- **Declared paths, mechanical.** UI specs must declare `interaction_traces` — the
  toggles to toggle, the drilldowns to drill. The verifier drives them in
  Playwright, so a defect that only appears after an interaction is covered for
  every declared path.
- **Undeclared residual, human.** The thinker keeps a short "use it like a rep"
  exploratory pass. When it finds something no criterion covered, it files a **new
  spec** (an unhappy walk produces a spec — already doctrine), tagged with
  severity. This is exactly how the 558% → 109 path already worked.

### 6. The thinker, narrowed (#6)

The thinker **stops re-verifying cards for closure** — the verifier owns that.
Its job becomes:

- new intake / brainstorm / spec authoring (unchanged);
- consuming the verifier's `needs-human` / taste escalations;
- the short exploratory "exercise it" pass (§5) → new specs;
- the human-facing **compressed** verdict to the operator ("7 criteria,
  prod-verified green; 1 taste call: X") — not the raw card.

The card walk is now *read the verifier's rendered evidence*, not re-load the
page. This is what drains the 33-card backlog: closure no longer waits on a human
walk; it waits on the verifier's tick, which is automatic and severity-ordered.

This also fixes the seam where the bug-finder can't file (076) and the filer isn't
looking at prod: the verifier observes and files correctives; orc registers and
builds; the thinker authors net-new specs. No actor both grades and closes its own
work.

### 7. Loop ceilings — no infinite dance, no silent stall (Gap B, #4, #5)

- Keep the verifier's `TRIAL_BUDGET = 3` per-criterion correctives and the
  escalation-expiry (unresolved > 2 ticks → human notification).
- **Add a dance ceiling:** a spec oscillating `shipped ↔ needs-rework ≥ 3×` →
  `needs-human` (a `rework_count` on the build record, surfaced in the
  needs-human render section that already exists).
- **"rework never fires" fixes itself:** with an automatic prod verdict, `HOLLOW`
  produces `needs-rework` *every tick* — back-pressure is continuous, not
  contingent on a human remembering to walk. The Two-Body Warning still holds: the
  loop converges only if orc consumes correctives; if filed items sit unconsumed,
  escalate rather than file into the void.

### 8. Migration / sequencing

- The public repo ships the render-join, the skill changes, and the verifier
  wiring **together** as v3.4.0 (self-consistent, the next-num precedent).
- The AS running instance follows the next-num ordering rule: orc lands the
  `spec_ledger.py` join + verifier wiring in `/opt/albert-scott/scripts/` and
  **deploys** them, *then* flips `~/.claude/skills/{orc,think,verification-loop}`.
  Flipping skills first hard-fails. Do not touch the AS tree while an orc owns it.
- Existing cards re-key from "shipped-awaiting-review" to `awaiting-prod`; the
  verifier sweeps them by severity. A1 (106) is the first conscript — it should
  come back `needs-rework` on the first tick under the new UI evidence contract.

## Acceptance criteria

1. `spec_ledger.py render` joins build + `verified/` and shows the computed
   effective status; a `shipped` record with a `REJECTED` verdict renders under a
   top `NEEDS-REWORK` section, never as `accepted`.
2. No code path sets `accepted` by hand; it is computed only from
   `shipped ∧ CONFIRMED`. A unit test asserts the join table in full.
3. A `criterion_type: ui` cannot reach a CONFIRMED verdict without
   `{deployed_url, deployed_sha, dom_assertion, screenshot_sha256, look_for}`
   evidence; a curl-only UI criterion auto-fails. Test covers both.
4. `eyeball: yes` blocks closure: the criterion renders `pending-eyeball` until a
   rendered observation exists; "deferred + done" is rejected by the close-out
   grader. Test covers the rejection.
5. The verifier refuses to judge until the live URL serves the expected
   `deployed_sha`; a SHA mismatch yields `awaiting-prod`, not `accepted`. Test
   covers the mismatch path.
6. Every spec record carries a valid `severity`; `Outstanding` sorts by it;
   `--check` rejects a missing/invalid severity. orc's First-moves doc states the
   P0/P1 drain rule.
7. UI specs declare `interaction_traces`; the verifier drives at least one
   declared trace and attaches its rendered evidence. The think skill documents
   the exploratory-pass → new-spec path.
8. The think skill no longer instructs a closure walk; it reads the verifier's
   evidence and emits a compressed verdict. orc/verifier/think role boundaries are
   restated in DO-IT.md.
9. A `rework_count ≥ 3` oscillation flips a spec to `needs-human`. Test covers the
   ceiling.
10. DO-IT.md, CHANGELOG (v3.4.0), and the DESIGN.md decision log are updated; the
    AS-instance sequencing note is recorded.

## Out of scope

- Replacing Playwright with another browser driver (it works; keep it).
- Cross-machine orchestration of the verifier (single box, as today).
- Re-architecting the cross-vendor judge (Codex primary / Claude fallback stays).
- Any change to the number-allocation path (shipped in v3.3.0).
