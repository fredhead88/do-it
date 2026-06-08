# Verifier v2 Execution — DO-IT Review Loop

## Status
Spec + plan written, committed, pushed. **Execution NOT started.** The live verifier
is producing false hard-REJECTs (contained by `rev`, not stopped). Next session:
execute the plan, **kill-switch (T1–T3) first**, to stop the churn, then PAUSE for
Ephraim before T4–T9.

## Goal
Make the standing verification loop trustworthy: **only an executable `dom_assertion`
(or a human `rev`) may write a hard ledger verdict; an LLM judging a snapshot is
advisory; an un-asserted criterion is `NO_ORACLE`.** Stop the verifier from
false-rejecting healthy specs into the orc's queue.

## Current State
- **Review Loop v2 shipped** to the public repo `fredhead88/do-it` (`~/do-it`, branch
  `main`): **v3.4.0** derived-verdict ledger · **v3.5.0** executable verifier (A1
  proof) · **v3.6.0** ops + standing `rev`. All merged/tagged/pushed.
- **Adopted into the live AS instance:** the verifier the cron runs is
  `~/.claude/verification-loop/` (`scripts/run-verifier-tick.sh` →
  `node ~/.claude/verification-loop/tick.mjs --config albert-scott`); `spec_ledger.py`
  in `/opt/albert-scott/scripts/`; skills in `~/.claude/skills/` (`rev` added, `think`
  sheds review). Crons live: orc relay, rev relay (`ROLE=rev`), verifier `*/30`,
  liveness `*/5`. **orc + rev sessions are running.**
- **A1 proof landed live:** spec 106 caught by `dom-assert` + `rev` → REJECTED → top
  of the board under `NEEDS-REWORK`. The design's thesis is proven on prod.
- **Relay-hook fix:** the orc context-watch hook kept getting reverted out of
  `/opt/albert-scott/.claude/settings.json` by tree-clean ops → now durable in
  `.claude/settings.local.json` (gitignored) carrying BOTH orc and `ROLE=rev` hooks.

## Active Problems
- **URGENT — false-reject churn.** ~21 specs hard-REJECTed in 30 min
  (006,008,009,010,022,025,038,052–056,059–064,066,071,078); `rev` ground-truthed
  ALL as false (product is healthy). Root cause: the LLM-on-snapshot path has
  hard-verdict authority **and** broken capture — pre-hydration / shared overview
  snapshot (055 & 056 cite the SAME sidebar-only evidence file). Contained (rev
  quarantines to NEEDS-EPHRAIM; orc not consuming), **not stopped.** The kill-switch
  (plan T1–T3) is the fix.
- **Adoption gap:** only **1/11** review cards (106) carry the executable
  `verifier:criteria` block; the other 10 ride the weak LLM path. The 49 CONFIRMED /
  23 REJECTED verdicts in the store are codex-on-snapshot (soft). The assertion
  backlog (LLM-draft + rev-accept `dom_assertion`s) is the durable fix.
- **DOM_INTERACTION** is downgraded (deferred — no interaction driver; the 558%
  interaction class relies on rev's exploratory pass).

## Key Decisions Made
- **Honest authority model (signal ≠ authority)** — approved by Ephraim 2026-06-08.
  Only `dom-assert` + `rev` move the ledger; LLM advisory for BOTH reject and confirm;
  **missing assertion = `NO_ORACLE`, NOT fail-closed-reject** (this REVERSES Plan 2's
  fail-closed). Asymmetry rationale: a false reject churns orc (loud); a false confirm
  hides a defect (quiet, A1 risk) — so the LLM gets neither authority.
- **Spec-first** (don't touch the live loop until the spec was reviewed). Done — spec
  approved, plan written.
- **Execution model:** subagent-driven for CODE tasks (two-stage review: spec then
  code-quality, like Review Loop v2's plans). **OPERATIONAL steps — T2 (deploy to
  `~/.claude/verification-loop`), T3 Step 6 (live quarantine run), T9 (AS sync) — are
  done by the CONTROLLER, not a subagent.** PAUSE after T3 for Ephraim to confirm the
  churn is stopped before T4–T9.
- `~/.claude/verification-loop` is OUTSIDE the orc git tree → safe to edit as verifier
  infra (it's not `/opt/albert-scott`). It's an independent copy of
  `~/do-it/verification-loop` — fix in `~/do-it` (with tests), then sync to the live
  copy.

## Next Steps (immediately actionable)
1. `cd ~/do-it && git checkout -b feat/verifier-v2` (the prior empty branch was
   deleted at handoff). Subagent-driven, **sonnet** workers.
2. **Task 1 (kill-switch):** implement `verification-loop/lib/authority.mjs`
   (`ledgerActionFor`) + wire `tick.mjs` resolve step (lines ~767–831). TDD per the
   plan. Two-stage review.
3. **Task 3 code (quarantine script):** `verification-loop/scripts/quarantine-advisory-verdicts.mjs`
   + test. (Skip Task 2 and Task 3 Step 6 — those are operational, controller does
   them.)
4. **CONTROLLER — Task 2 (deploy, stops the churn):** `cp` `lib/authority.mjs` +
   `tick.mjs` to `~/.claude/verification-loop/`; `node --check`; force a tick; confirm
   codex criteria log `ADVISORY` (no new hard verdicts).
5. **CONTROLLER — Task 3 Step 6:** run the quarantine script against
   `~/.claude/ledger/verified`; render; confirm the 21 leave NEEDS-REWORK.
6. **PAUSE — confirm with Ephraim** the live churn is stopped + cleaned.
7. Then **T4** (NO_ORACLE render, `spec_ledger.py`), **T5** (readiness gate +
   target-page + per-criterion evidence — kills pre-hydration/shared-snapshot),
   **T6** (trigger model), **T7** (supersession), **T8** (docs + **v3.7.0**), **T9**
   (live AS sync + acceptance) — subagent-driven for code, controller for the T9 sync.

## Reference
- **Plan:** `~/do-it/docs/2026-06-08-verifier-v2-plan.md` (9 tasks; T1–T3 = churn-stopper).
- **Spec:** `~/do-it/docs/2026-06-08-verifier-v2-signal-vs-authority-design.md`.
- **Prior:** `~/do-it/docs/2026-06-08-review-loop-prod-verdict-design.md` +
  `...-review-loop-v2-plan-{1,2,3}-*.md`.
- **Authority signal at the resolve step:** `finalJudgeResult.judge` — `dom-assert`
  (executable, authoritative) · `codex`/`claude-fallback` (LLM advisory) · `schema`
  (missing assertion → NO_ORACLE).
- **Live verifier:** `~/.claude/verification-loop/`; **verdict store:**
  `~/.claude/ledger/verified/*.yml`; **needs-human:** `~/.claude/ledger/needs-human/`;
  **board (orc-rendered):** `/opt/albert-scott/docs/do-it/ledger/OUTSTANDING.md`.
- **Tests:** `~/do-it`: `python3 -m pytest tests/` ; `cd verification-loop && npm test`
  (`node --test`; uses system Chrome via `channel:'chrome'` — bundled chromium won't
  install on ubuntu26.04).
- **AS rollout memo (for orc):** `~/.claude/brief-inbox/memo-review-loop-v2-rollout.md`.
- **Guardrails:** never run do-it `setup.sh` on this box (clobbers AS skills); never
  commit in `/opt/albert-scott` while an orc owns it; the public repo is the source,
  the AS instance is the specialized copy ([[project_do_it_distribution]]).

## Session Log
- 2026-06-08: Built Review Loop v2 (v3.4.0–3.6.0) end-to-end, shipped to the public
  repo + adopted into AS; A1 proof landed live; fixed the relay-hook silent break
  (→`settings.local.json`); diagnosed the 21-spec false-reject churn (LLM-on-snapshot
  authority + broken capture); cross-vendor-researched + Ephraim-approved the honest
  authority model; wrote the verifier-v2 spec + plan. Stopped at execution start
  (context saturated).
