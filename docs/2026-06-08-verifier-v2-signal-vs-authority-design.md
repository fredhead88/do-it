# Verifier v2 — Signal ≠ Authority

**Status:** design, direction approved 2026-06-08 (honest authority model). **Live
loop untouched** pending review of this spec.
**Supersedes:** the *verdict-authority* behaviour of Review Loop v2 (v3.5.0/v3.6.0),
which built the executable path but left the LLM-on-snapshot path with hard-verdict
authority — the bug this spec fixes. Target: a verifier-v2 release (v3.7.0).
**Inputs:** the live failure (below), a Codex cross-vendor strategy review
(2026-06-08), and `docs/2026-06-08-review-loop-prod-verdict-design.md`.

## The problem, proven live

Thirty minutes of the standing verifier produced **21 hard REJECTs**; the `rev`
session ground-truthed all 21 on prod as **false** — the product is healthy. The
evidence is unambiguous: specs **055 and 056 cite the *same* evidence file**, and
its content is **only the sidebar nav** ("Profit & Sales / Cash View" links) — no
page content. So the artifact is pre-hydration *and* a shared/overview snapshot
judged against unrelated criteria; Codex honestly reported "I don't see X" because X
wasn't in the frame → `HOLLOW` → written as a hard `REJECTED` → `needs-rework` →
would pull orc into "fixing" healthy features and **bury a real regression when one
lands.**

Capture timing and wrong-page are symptoms. **The disease: observation confidence
and verdict authority were never separated.** A flaky screenshot judged by an LLM
was granted the power to move the ledger.

## The invariant

> **Observation confidence ≠ verdict authority.** A verdict carries both a *signal*
> (what was observed) and an *authority tier*. Only **deterministic,
> criterion-bound, page-bound, current** evidence — **or a human** — may move the
> ledger. An LLM judging a snapshot is **advisory, never authoritative**, for both
> reject *and* confirm.

## Verdict authority — three tiers

| Tier | Evidence | May it move the ledger? |
|------|----------|--------------------------|
| **Authoritative** | executable `dom_assertion` (selector+predicate on the criterion's *target* page, after readiness, tagged with the deployed sha), API/route assertion, **or a `rev` human verdict** | **Yes** — hard `CONFIRMED` / `REJECTED` |
| **Advisory** | an LLM judging a screenshot / aria / text snapshot | **No** — `NOT_SATISFIED` → `NEEDS-ASSERTION` / `INSUFFICIENT-EVIDENCE`; `SATISFIED` → "looks-present" triage hint |
| **NO_ORACLE** | a criterion with neither an executable assertion nor a human verdict | **No** — rests as *awaiting assertion/human*; never auto-rejected, never auto-confirmed |

**The asymmetry is the whole point.** A false *reject* churns the orc (loud — what
we just watched). A false *confirm* hides a real defect (quiet — the A1 risk). The
LLM-on-snapshot path gets **neither** authority. A human *is* a real oracle; a
screenshot-LLM is not.

## The observation contract

Stop "capture a page, ask a model." Start "run the criterion's oracle on the
criterion's target surface." Every criterion carries:

- `target.page` — a `page_map` key; the verifier observes **that** surface, not a
  blanket overview. (Today's always-`overview` fallback is the wrong-page bug.)
- `kind` — `dom | api | interaction | visual | data | taste`.
- an **executable assertion** for every hard-verifiable kind (the `dom_assertion`
  model from `assert-dom.mjs` — proven on spec 106).
- a **readiness gate** before any capture: wait for the *target selector* or an
  explicit app marker (e.g. `[data-testid="cash-view-ready"]`) — **not** the generic
  `main`/`#__next` sentinel, which fires when the shell mounts, pre-data. If the
  target never appears, that is deterministic evidence; if there was never a target,
  the result is `NO_ORACLE`, not `REJECTED`.
- evidence bound to `{spec_id, criterion_id, page, deployed_sha, assertion_id}` —
  so two specs can never share one snapshot (the 055/056 collision).

`probe.mjs` is demoted to a **health probe** (is the surface up?); per-criterion
verification runs the criterion's own oracle.

## The criteria contract (prose → checkable)

Prose criteria are **not executable** and confer **no authority**. A spec is not
verifier-ready until each *hard* criterion has a machine block (the existing
`verifier:criteria` schema, extended with `target.page` + `kind`). You cannot safely
invent authority from prose — but you can **assist**:

- An LLM **drafts** the assertion from the prose + a page inventory (selector +
  predicate + target page). It proposes; it does not judge production.
- `orc`/`rev` **accepts** the drafted selector at card-write/review time. Shipped
  surfaces carry stable `data-testid`s (unavoidable engineering load — cheaper than
  recurring false rejects).
- A missing assertion → `NO_ORACLE`, **never** a prose→LLM→hard-verdict fallback.

## The human (`rev`)

`rev` verifies *the verifier*, not every criterion. It receives **only**: drafted
assertions to approve, supersession conflicts to rule on, taste/product judgment,
*repeated deterministic* failures, and suspected gaming. It **never** receives
weak-observation noise — `NO_ORACLE` / `WRONG_PAGE_RISK` / `INSUFFICIENT_EVIDENCE`
are *internal verifier states*, not product defects surfaced to a human.

## Cost & cadence

Not every criterion every tick (expensive + noisy). Trigger model:

- a new deployed sha touching the spec's route/surface,
- a newly-shipped spec, after a hydration grace,
- an assertion/schema change,
- an explicit orc/rev request,
- a small **daily canary** set for broad regressions.

LLM calls are budgeted and exceptional: assertion *drafting*, advisory triage of
`NO_ORACLE`, summarizing evidence bundles for `rev`, analyzing flaky deterministic
failures. Keep Playwright sequential, reuse the browser context, never spawn Codex
for a routine criterion — the cheapest verifier is a selector count.

## Supersession

A criterion superseded by a later shipped spec (e.g. the ghost-overlay criteria
replaced by 071's paired bars) is marked `SUPERSEDED`, **not failed** — a
ledger/versioning concern, not an observation one.

## Migration — cheapest, highest-leverage first

1. **Kill-switch (the immediate stop):** the LLM-on-snapshot path (`DOM`, `VISION`,
   downgraded `DOM_INTERACTION`) may **not** write hard verdicts. Only `DOM_ASSERT`
   (executable) + `rev` (human) write `CONFIRMED`/`REJECTED`. Ends the churn.
2. **Quarantine:** re-tag the 21 false rejects (and distrust the 49 LLM
   soft-confirms) as verifier-advisory/`NO_ORACLE` so orc consumes none of them.
3. **Readiness + target-page:** wait for the criterion's selector / an app ready
   marker; bind evidence to `{spec, criterion, page, sha}`; remove the always-overview
   fallback.
4. **Assertion backlog:** LLM-draft + human-accept `dom_assertion`s for the 10/11
   un-asserted cards; add `data-testid`s; prioritize by blast radius / recent churn.
5. **Trigger model:** replace every-criterion-every-tick with the triggers above.
6. **Supersession handling** in the ledger.
7. **Docs:** `rev` SKILL + `verification-loop` SKILL + DO-IT updated to the
   authority model.

## Acceptance criteria

1. A `NOT_SATISFIED` from any non-`DOM_ASSERT` layer **never** produces a ledger
   `REJECTED` — it produces an advisory escalation. (Test.)
2. A `SATISFIED` from any non-`DOM_ASSERT` layer **never** produces a durable
   `CONFIRMED`. (Test.)
3. A criterion with no executable assertion and no human verdict renders `NO_ORACLE`
   — never `accepted`, never `needs-rework`. (Test.)
4. `dom_assert` and `rev` still produce hard verdicts (the 106 path intact). (Test.)
5. Per-criterion observation captures the criterion's `target.page` after a
   readiness gate; the evidence artifact carries `{spec, criterion, page, sha}`; no
   two criteria share an artifact. (Test.)
6. The 21 quarantined specs no longer render under `NEEDS-REWORK`.
7. `rev`'s queue contains no `NO_ORACLE` / wrong-page items as product defects.
8. A tick with no new sha and no request performs **zero** LLM judging. (Cost test.)
9. A criterion superseded by a later shipped spec renders `SUPERSEDED`, not failed.
10. `verification-loop` SKILL, `rev` SKILL, DO-IT, CHANGELOG updated; the live
    `~/.claude/verification-loop` and the public repo both carry the change (AS
    instance synced per the standing rollout rule).

## Out of scope / deferred

- **Visual-diff / screenshot-baseline** oracle — later, only where a real visual
  oracle exists; LLM vision stays advisory triage.
- **Interaction driver** — still deferred; `DOM_INTERACTION` criteria are
  `NO_ORACLE` (or rev-verified) until a real driver exists.
- **Auto-accepting** LLM-drafted assertions — a human/orc must accept; drafting is
  assistance, not authority.

## What this corrects (own it)

Review Loop v2 (v3.5/3.6) built the executable `dom_assert` path — the right idea,
proven on 106 — but left the legacy LLM-on-snapshot path **hard-authoritative**.
That gap is the 21 false rejects. Verifier v2 finishes the separation the design
always implied: *executable + human own the verdict; the LLM advises.*
