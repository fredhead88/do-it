---
name: verification-loop
description: Use when verifying shipped work on prod, standing up the autonomous verifier, running the verification loop, confirming a spec is actually done end-to-end, or checking whether shipped criteria are hollow. Trigger phrases include "verify shipped work", "stand up the verifier", "run the verification loop", "is this actually done on prod", "check for hollow specs", "autonomous post-ship review".
---

# Verification Loop

## Overview

**Observe the running product. Stay blind to how it was built.**

The verification loop is a standing autonomous reviewer that drives shipped work from "orc says done" to "verified green on prod". It observes the deployed product via a headless browser, assigns typed evidence to each acceptance criterion, judges cross-vendor (Codex primary, Claude fallback), and loops to convergence — filing correctives for hollow work, escalating taste/blockers, and never touching the build.

## Three Core Invariants

1. **Blind-but-watching.** The verifier never sees the build, the diff, or the builder's reasoning. The judge receives only the typed evidence artifact — never the worker's explanation.
2. **Evidence-type-locked-to-criterion-type.** A UI criterion requires a DOM/screenshot observation. A grep is auto-fail for a UI criterion. No criterion closes without observed, type-matched evidence.
3. **Verifier owns the verdict; the builder cannot overwrite it.** Verdicts live in `~/.claude/ledger/verified/<spec_id>.yml` — a separate namespace the builder's `set`/`register` commands never touch.

## The Two-Body Warning

The loop converges **only if orc is running and consuming correctives**. If filed items sit unconsumed for N ticks, escalate to `NEEDS-HUMAN.jsonl` — do not file forever into the void.

## The 8-Step Tick

Run with: `node ~/.claude/verification-loop/tick.mjs [--spec NNN-slug] [--dry-run] [--force]`

1. **Detect new ship** — compare deployed sha vs last `PROGRESS.jsonl` entry. No new sha → idle-cheap return, no browser spun.
2. **Selfcheck** — fail loud if any credential missing/empty or chrome absent. Write to `NEEDS-HUMAN.jsonl` and halt. Never silent-continue.
3. **Auth + load criteria** — `acquire()` storageState once per day (7-day TTL). Pull acceptance criteria from the spec file. `pinSpecSha()` to guard against silent scope reduction.
4. **Observe per criterion** — `selectObservationLayer()` routes to DOM (aria snapshot + innerText), VISION (screenshot + bounded binary question), or DOM_INTERACTION. Run `callApi()` for backend criteria. Both `verify_periods`. Run `runIpt()` when a gaming trigger fires.
5. **Judge** — `judge(criterion, evidenceText, {runCodex, runClaude})`. Token/reason contradiction → flag UNCLEAR → re-judge once → escalate if still unclear. Judge calls are **sequential** (never concurrent — subscription rate limit).
6. **Assign verdict + resolve:**
   - `CONFIRMED` → `recordVerdict()` + `spec_ledger.py verify NNN CONFIRMED --judge codex --evidence <ref>`
   - `HOLLOW / MISSING / REGRESSION` → escalate corrective to `NEEDS-HUMAN.jsonl` (≤3 attempts; on exhaustion → BOUNCED, escalate)
   - `DATA-GAP / NOT-RUN` → ops note
   - `SUSPECTED-GAMING / TASTE / blocker` → escalate, never spin
7. **Re-probe transient** — `probe.mjs` catches 502/503 deploy windows, retries once after 30s, tags `DEPLOY_IN_PROGRESS`. Never cry P0 on a deploy window.
8. **Scope reduction + progress** — `detectScopeReduction()` → escalate any missing-with-no-evidence criteria. `appendProgress()`. Reschedule cost-aware.

## Verdict Taxonomy

| Verdict | Meaning | Action |
|---------|---------|--------|
| `CONFIRMED` | Criterion observed working | Write to verified/ namespace |
| `HOLLOW` | Exists in code, doesn't work | File corrective |
| `MISSING` | Not implemented | File corrective |
| `REGRESSION` | Was working, now broken | File corrective |
| `NOT-RUN` | Operational step skipped | Ops note |
| `DATA-GAP` | Code ok, source data absent | Ops note |
| `TASTE` | Subjective judgement call | Escalate to Ephraim |
| `SUSPECTED-GAMING` | IPT metamorphic relation failed | Escalate; R7 2nd-case rule before labelling systemic |
| `UNCLEAR` | Token/reason contradiction in judge output | Re-judge once; escalate if still unclear |
| `BOUNCED` | Trial budget (≤3) exhausted | Escalate to Ephraim |

## Durable State Files (under `runs/<date>/`)

| File | Purpose |
|------|---------|
| `PROGRESS.jsonl` | Append-only event log — sha, criteria checked, verdicts. Resume by reading. |
| `VERIFICATION-LEDGER.jsonl` | Per-criterion verdict + evidence ref. Source of truth for "checked". |
| `NEEDS-HUMAN.jsonl` | Loud escalation list — taste, blockers, gaming, exhausted budgets. |
| `SPEC-PINS.json` | Criteria set pinned at handover. Scope-reduction guard (U3). |

Verifier-owned verdict files: `~/.claude/ledger/verified/<spec_id>.yml`

## Smoke-Test a Single Criterion

```bash
cd ~/.claude/verification-loop
set -a; source <repo root>/.env; set +a
node tick.mjs --spec 064-asin-page-unmapped-asin-blank --criterion "returns 200" --dry-run
```

## Autonomous Cron Path vs Attended Debugging

- **Cron (autonomous):** `node tick.mjs` — uses `probe.mjs` / `shoot.mjs` / `api.mjs` for observations. No MCP.
- **Attended debugging only:** chrome-devtools MCP is for interactive investigation when you want to drive a browser in the current conversation. Do NOT wire the MCP into the autonomous cron tick.

## Escalation Expiry

An escalation unresolved for >2 ticks should trigger a human notification (append to `NEEDS-HUMAN.jsonl` with `reason: unresolved_escalation`), not another corrective attempt.

## Config Location

`~/.claude/verification-loop/config/<your-project>.json` — the only AS-specific surface. Swap in another project's config to reuse the harness.
