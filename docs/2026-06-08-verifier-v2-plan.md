# Verifier v2 (Signal ≠ Authority) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make verdict authority follow evidence strength — only an executable `dom_assertion` (or a human `rev`) may write a hard ledger verdict; an LLM judging a snapshot is advisory; an un-asserted criterion is `NO_ORACLE` — so the verifier stops false-rejecting healthy specs into the orc's queue.

**Architecture:** A pure decision function (`ledgerActionFor`) gates every ledger write in `tick.mjs`'s resolve step on the judge that produced it. The LLM-on-snapshot and missing-assertion paths escalate *advisory* signals (`NEEDS-ASSERTION`/`NO_ORACLE`/`looks-present`) instead of writing `REJECTED`/`CONFIRMED`. The ledger renders `NO_ORACLE` distinctly. Then observation is made criterion-bound (target page + readiness gate + per-criterion evidence), judging becomes trigger-driven, and stale criteria are `SUPERSEDED`.

**Tech Stack:** Node 24 (`node --test`, Playwright fixtures), Python 3.14/pytest (`spec_ledger.py`), Bash. Tasks 1–4 are unit-testable; 5 uses `file://` fixtures; 6 is a pure trigger fn; 7 is python; 8 docs; 9 is the live AS sync.

**Two verifiers, both must change.** The product source is `~/do-it/verification-loop`; the **live** one the cron runs is **`~/.claude/verification-loop`** (independent copy). Build + test in `~/do-it`; **Task 2 syncs the kill-switch to the live copy to stop the churn** (machine-global, not the orc git tree — safe). Spec: `docs/2026-06-08-verifier-v2-signal-vs-authority-design.md`.

**Ship order:** Tasks 1–3 are the urgent, independently-deployable unit (kill-switch + deploy + quarantine) — **deploy after Task 3 to stop the live false-reject churn**, then continue 4–9. Release is **v3.7.0** at Task 8.

---

## File Structure
- **Create** `verification-loop/lib/authority.mjs` — `ledgerActionFor(verdict, judge)` (the kill-switch decision).
- **Modify** `verification-loop/tick.mjs` — resolve step calls `ledgerActionFor`; advisory escalations replace LLM/​schema hard writes.
- **Create** `verification-loop/test/authority.test.mjs`.
- **Create** `verification-loop/scripts/quarantine-advisory-verdicts.mjs` + test — one-time sweep of existing codex-judge verdicts.
- **Modify** `scripts/spec_ledger.py` — `effective_status` + render represent `NO_ORACLE`; **Create** `tests/test_no_oracle.py`.
- **Modify** `verification-loop/lib/probe.mjs` + `tick.mjs` (`observeCriterion`) — readiness gate + target-page + per-criterion evidence; fixtures + test.
- **Create** `verification-loop/lib/triggers.mjs` + test — the trigger model.
- **Modify** `scripts/spec_ledger.py` — supersession; test.
- **Modify** `skills/verification-loop/SKILL.md`, `skills/rev/SKILL.md`, `DO-IT.md`, `CHANGELOG.md`, version.

---

## Task 1 — The kill-switch: authority decision function

**Files:** Create `verification-loop/lib/authority.mjs`, `verification-loop/test/authority.test.mjs`; Modify `verification-loop/tick.mjs` (resolve step, lines 767–831).

- [ ] **Step 1: Write the failing test**

`verification-loop/test/authority.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { ledgerActionFor } from '../lib/authority.mjs';

// Executable assertion (or human via cmd_verify) is the only authoritative path.
test('dom-assert CONFIRMED writes a hard CONFIRMED', () => {
  assert.deepEqual(ledgerActionFor('CONFIRMED', 'dom-assert'), { ledger: 'CONFIRMED', escalate: null });
});
test('dom-assert HOLLOW writes a hard REJECTED + corrective', () => {
  assert.deepEqual(ledgerActionFor('HOLLOW', 'dom-assert'), { ledger: 'REJECTED', escalate: 'CORRECTIVE_NEEDED' });
});

// LLM-on-snapshot: NEVER a hard verdict — advisory only (both directions).
test('codex HOLLOW is advisory NEEDS-ASSERTION, no ledger write', () => {
  assert.deepEqual(ledgerActionFor('HOLLOW', 'codex'), { ledger: null, escalate: 'NEEDS_ASSERTION' });
});
test('claude-fallback MISSING is advisory, no ledger write', () => {
  assert.equal(ledgerActionFor('MISSING', 'claude-fallback').ledger, null);
});
test('codex CONFIRMED is advisory looks-present, no durable CONFIRMED', () => {
  assert.deepEqual(ledgerActionFor('CONFIRMED', 'codex'), { ledger: null, escalate: 'ADVISORY_LOOKS_PRESENT' });
});

// Missing/invalid assertion: NO_ORACLE (NOT the old fail-closed REJECTED).
test('schema (no assertion) is NO_ORACLE, never REJECTED', () => {
  assert.deepEqual(ledgerActionFor('HOLLOW', 'schema'), { ledger: null, escalate: 'NO_ORACLE' });
});
test('DATA-GAP via dom-assert marks not-applicable', () => {
  assert.equal(ledgerActionFor('DATA-GAP', 'dom-assert').ledger, 'not-applicable');
});
```

- [ ] **Step 2: Run — expect fail**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — `../lib/authority.mjs` not found.

- [ ] **Step 3: Implement `verification-loop/lib/authority.mjs`**

```javascript
// Verdict AUTHORITY follows evidence strength (signal != authority).
// Only an executable assertion (judge 'dom-assert') may write a hard ledger
// verdict from the tick. The human reviewer (rev) writes hard verdicts directly
// via spec_ledger.py verify, never through this path. Everything else is advisory.
const HOLLOW = new Set(['HOLLOW', 'MISSING', 'REGRESSION']);

export function ledgerActionFor(verdict, judge) {
  if (judge === 'dom-assert') {            // executable, criterion-bound, authoritative
    if (verdict === 'CONFIRMED') return { ledger: 'CONFIRMED', escalate: null };
    if (HOLLOW.has(verdict))     return { ledger: 'REJECTED', escalate: 'CORRECTIVE_NEEDED' };
    if (verdict === 'DATA-GAP')  return { ledger: 'not-applicable', escalate: 'OPS_NOTE' };
    return { ledger: null, escalate: 'OPS_NOTE' };
  }
  if (judge === 'schema') {                // ui criterion with no/invalid assertion
    return { ledger: null, escalate: 'NO_ORACLE' };   // can't verify — NOT "broken"
  }
  // LLM-on-snapshot (codex / claude-fallback): advisory only, never moves the ledger.
  if (verdict === 'CONFIRMED') return { ledger: null, escalate: 'ADVISORY_LOOKS_PRESENT' };
  if (HOLLOW.has(verdict))     return { ledger: null, escalate: 'NEEDS_ASSERTION' };
  return { ledger: null, escalate: 'ADVISORY' };
}
```

- [ ] **Step 4: Run — expect pass**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: PASS (authority tests).

- [ ] **Step 5: Wire the resolve step in `tick.mjs`**

Add the import at the top with the other `lib/` imports:

```javascript
import { ledgerActionFor } from './lib/authority.mjs';
```

Replace the resolve block (the `if (verdict === 'CONFIRMED') … else if HOLLOW/MISSING/REGRESSION … else if DATA-GAP …` chain, lines 768–797) with an authority-gated version. Keep the `SUSPECTED-GAMING`, `TASTE`, `NOT-RUN`, and final `else` branches (lines 799–831) unchanged:

```javascript
      // ── Step 6: resolve — authority follows evidence strength ────────────────
      const action = ledgerActionFor(verdict, finalJudgeResult.judge);
      if (action.ledger) {
        // Authoritative (dom-assert): write the hard verdict.
        log(`  ${verdict} via ${finalJudgeResult.judge} → ledger ${action.ledger}`);
        await callSpecLedgerVerify(spec.spec_id, criterion.id, action.ledger, finalJudgeResult.judge, evidenceRef || 'none');
        if (action.escalate === 'CORRECTIVE_NEEDED') {
          escalate(dir, {
            event: 'CORRECTIVE_NEEDED', spec: spec.spec_id, criterionId: criterion.id,
            criterion: criterion.text, verdict, judge: finalJudgeResult.judge,
            reason: finalJudgeResult.reason, attempts: attempts + 1, deployed_sha: currentSha,
            note: `File a corrective spec with observable criteria targeting: ${criterion.text}`,
          });
        }
      } else if (action.escalate === 'NEEDS_ASSERTION' || action.escalate === 'NO_ORACLE') {
        // NON-authoritative reject/missing-assertion: advisory ONLY — no ledger write,
        // and NOT surfaced to rev as a product defect. This is the kill-switch.
        log(`  ${verdict} via ${finalJudgeResult.judge} → ADVISORY ${action.escalate} (no ledger write)`);
        appendProgress(dir, {
          event: action.escalate, spec: spec.spec_id, criterionId: criterion.id,
          criterion: criterion.text, observed_verdict: verdict, judge: finalJudgeResult.judge,
          reason: finalJudgeResult.reason.slice(0, 200), deployed_sha: currentSha,
          note: 'advisory: LLM-on-snapshot / no executable assertion — needs a dom_assertion or a rev look; NOT a product defect',
        });
      } else if (action.escalate === 'ADVISORY_LOOKS_PRESENT' || action.escalate === 'ADVISORY') {
        // NON-authoritative confirm: a soft hint, never a durable CONFIRMED.
        appendProgress(dir, {
          event: 'ADVISORY_LOOKS_PRESENT', spec: spec.spec_id, criterionId: criterion.id,
          criterion: criterion.text, judge: finalJudgeResult.judge, deployed_sha: currentSha,
        });
      } else if (action.escalate === 'OPS_NOTE') {
        escalate(dir, {
          event: 'OPS_NOTE', spec: spec.spec_id, criterionId: criterion.id,
          criterion: criterion.text, verdict, deployed_sha: currentSha,
        });
      } else if (verdict === 'NOT-RUN') {
        escalate(dir, { event: 'OPS_NOTE', spec: spec.spec_id, criterionId: criterion.id, criterion: criterion.text, verdict, deployed_sha: currentSha });
      } else if (verdict === 'SUSPECTED-GAMING') {
        // intentional: no spec_ledger verdict — a human must adjudicate a gaming claim
        escalate(dir, { event: 'SUSPECTED_GAMING', spec: spec.spec_id, criterionId: criterion.id, criterion: criterion.text, deployed_sha: currentSha, note: 'Metamorphic relation failed — possible builder gaming. Human review required.' });
      } else if (verdict === 'TASTE') {
        escalate(dir, { event: 'TASTE_ESCALATION', spec: spec.spec_id, criterionId: criterion.id, criterion: criterion.text, deployed_sha: currentSha });
      } else {
        log(`  unhandled verdict ${verdict} for ${spec.spec_id}:${criterion.id} — escalating`);
        escalate(dir, { event: 'UNHANDLED_VERDICT', spec: spec.spec_id, criterionId: criterion.id, verdict });
      }
```

Also remove the now-obsolete `schema_error → NOT_SATISFIED` fail-closed *hard reject* in `observeCriterion`: a `schema_error` criterion should carry `judge: 'schema'` (it already does via the judgeResult), so `ledgerActionFor` routes it to `NO_ORACLE` — no code change needed there beyond confirming the judge tag is `'schema'`. (Verify: the schema_error branch in `observeCriterion` sets `judge: 'schema'`.)

- [ ] **Step 6: Syntax + suite**

Run: `cd /home/albert/do-it/verification-loop && node --check tick.mjs && npm test`
Expected: clean syntax; all node tests pass.

- [ ] **Step 7: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/authority.mjs verification-loop/test/authority.test.mjs verification-loop/tick.mjs
git commit -m "feat(verifier): kill-switch — only dom-assert writes hard verdicts; LLM-on-snapshot is advisory"
```

---

## Task 2 — Deploy the kill-switch to the LIVE verifier (stop the churn)

**Files:** none in-repo — an operational sync to `~/.claude/verification-loop`.

- [ ] **Step 1: Sync the changed files to the live copy**

```bash
cp /home/albert/do-it/verification-loop/lib/authority.mjs ~/.claude/verification-loop/lib/authority.mjs
cp /home/albert/do-it/verification-loop/tick.mjs ~/.claude/verification-loop/tick.mjs
node --check ~/.claude/verification-loop/tick.mjs && echo "live tick.mjs OK"
```

- [ ] **Step 2: Confirm the live loop no longer writes hard verdicts from codex**

Force one tick and confirm no new `verdict: REJECTED`/`CONFIRMED` with `judge: codex` is written:

```bash
node ~/.claude/verification-loop/tick.mjs --config your-project --force 2>&1 | tail -20
# then check the newest verified writes are only dom-assert/rev:
grep -L "judge: dom-assert\|judge: rev" ~/.claude/ledger/verified/*.yml | xargs -r grep -l "judge: codex" | head
```

Expected: the tick logs `ADVISORY NEEDS_ASSERTION (no ledger write)` for codex-judged criteria; no new codex hard verdicts appear. (Pre-existing codex verdicts are cleaned in Task 3.)

This is the checkpoint that **stops the live false-reject churn.**

---

## Task 3 — Quarantine the existing false verdicts

**Files:** Create `verification-loop/scripts/quarantine-advisory-verdicts.mjs` + `verification-loop/test/quarantine.test.mjs`.

- [ ] **Step 1: Write the failing test**

`verification-loop/test/quarantine.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { quarantineDir } from '../scripts/quarantine-advisory-verdicts.mjs';

test('codex-judged hard verdicts are demoted; dom-assert/rev kept', () => {
  const d = fs.mkdtempSync(path.join(os.tmpdir(), 'verq-'));
  fs.writeFileSync(path.join(d, '055-x.yml'), 'spec_id: 055-x\nverdict: REJECTED\njudge: codex\ncriteria:\n  c1: REJECTED\n');
  fs.writeFileSync(path.join(d, '106-y.yml'), 'spec_id: 106-y\nverdict: REJECTED\njudge: dom-assert\ncriteria:\n  c1: REJECTED\n');
  fs.writeFileSync(path.join(d, '073-z.yml'), 'spec_id: 073-z\nverdict: CONFIRMED\njudge: rev\n');
  const res = quarantineDir(d);
  const x = fs.readFileSync(path.join(d, '055-x.yml'), 'utf8');
  assert.match(x, /verifier_advisory: true/);
  assert.doesNotMatch(x, /^verdict: REJECTED/m);   // hard verdict demoted
  const y = fs.readFileSync(path.join(d, '106-y.yml'), 'utf8');
  assert.match(y, /^verdict: REJECTED/m);          // dom-assert kept
  const z = fs.readFileSync(path.join(d, '073-z.yml'), 'utf8');
  assert.match(z, /^verdict: CONFIRMED/m);         // rev kept
  assert.equal(res.demoted, 1);
});
```

- [ ] **Step 2: Run — expect fail.** `cd /home/albert/do-it/verification-loop && npm test` → FAIL (no module).

- [ ] **Step 3: Implement `verification-loop/scripts/quarantine-advisory-verdicts.mjs`**

```javascript
import fs from 'node:fs';
import path from 'node:path';

/** Demote hard verdicts written by the LLM-on-snapshot path (judge codex/claude)
 *  to verifier-advisory: they were never authoritative. Keep dom-assert + rev. */
export function quarantineDir(dir) {
  let demoted = 0;
  for (const f of fs.readdirSync(dir).filter(f => f.endsWith('.yml'))) {
    const p = path.join(dir, f);
    const text = fs.readFileSync(p, 'utf8');
    const judge = (text.match(/^judge:\s*(.+)$/m) || [])[1]?.trim();
    if (judge === 'dom-assert' || judge === 'rev') continue;
    if (!/^verdict:\s*(CONFIRMED|REJECTED)/m.test(text)) continue;
    const out = text
      .replace(/^verdict:\s*(CONFIRMED|REJECTED).*$/m, 'verdict: advisory  # demoted: LLM-on-snapshot, not authoritative')
      + (text.includes('verifier_advisory:') ? '' : 'verifier_advisory: true\n');
    fs.writeFileSync(p, out);
    demoted++;
  }
  return { demoted };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const dir = process.argv[2] || `${process.env.HOME}/.claude/ledger/verified`;
  console.log(JSON.stringify(quarantineDir(dir)));
}
```

- [ ] **Step 4: Run — expect pass.** `npm test` → PASS.

- [ ] **Step 5: Commit.**

```bash
cd /home/albert/do-it
git add verification-loop/scripts/quarantine-advisory-verdicts.mjs verification-loop/test/quarantine.test.mjs
git commit -m "feat(verifier): one-time quarantine — demote LLM-on-snapshot hard verdicts to advisory"
```

- [ ] **Step 6: Run it against the LIVE verdict store (operational; stops the 21 rendering as needs-rework)**

```bash
cp /home/albert/do-it/verification-loop/scripts/quarantine-advisory-verdicts.mjs ~/.claude/verification-loop/scripts/ 2>/dev/null || mkdir -p ~/.claude/verification-loop/scripts && cp /home/albert/do-it/verification-loop/scripts/quarantine-advisory-verdicts.mjs ~/.claude/verification-loop/scripts/
node ~/.claude/verification-loop/scripts/quarantine-advisory-verdicts.mjs ~/.claude/ledger/verified
python3 ~/.claude/verification-loop/../.. 2>/dev/null; python3 $REPO_ROOT/scripts/spec_ledger.py --render 2>/dev/null | grep -A3 NEEDS-REWORK
```

Expected: `NEEDS-REWORK` no longer lists the codex-rejected specs (only dom-assert/rev REJECTEDs remain, e.g. 106 if still failing). **Once this lands, the churn is both stopped (Task 2) and cleaned (Task 3) — checkpoint to confirm with the operator before Tasks 4+.**

---

## Task 4 — `NO_ORACLE` in the ledger render

**Files:** Modify `scripts/spec_ledger.py` (`effective_status` + render); Create `tests/test_no_oracle.py`.

- [ ] **Step 1: Write the failing test**

`tests/test_no_oracle.py`:

```python
import importlib.util
from pathlib import Path
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "spec_ledger.py"

def _load(monkeypatch, tmp_path):
    monkeypatch.setenv("DOIT_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("DOIT_MIRROR_DIR", str(tmp_path / "mirror"))
    spec = importlib.util.spec_from_file_location("spec_ledger_nooracle", SCRIPT)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); return mod

def test_advisory_verdict_is_no_oracle_not_accepted_or_rework(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    # a shipped spec whose only verdict is the demoted advisory marker
    assert sl.effective_status({"status": "shipped"}, {"verdict": "advisory"}) == "no-oracle"
    # unchanged: real verdicts still resolve
    assert sl.effective_status({"status": "shipped"}, {"verdict": "CONFIRMED"}) == "accepted"
    assert sl.effective_status({"status": "shipped"}, {"verdict": "REJECTED"}) == "needs-rework"
```

- [ ] **Step 2: Run — expect fail.** `cd /home/albert/do-it && python3 -m pytest tests/test_no_oracle.py -q` → FAIL.

- [ ] **Step 3: Implement.** In `effective_status` (after the `REJECTED`/`needs_human` checks, before the `awaiting-prod` fallback):

```python
    if v == "advisory":
        return "no-oracle"
```

In `render`, add a `no-oracle` bucket computed from `_eff`, rendered as an informational section (NOT under NEEDS-REWORK, NOT under Accepted):

```python
    no_oracle = [r for r in records if _eff(r) == "no-oracle"]
    ...
    if no_oracle:
        L.append(f"## ◌ NO-ORACLE — shipped, not machine-verifiable yet ({len(no_oracle)})")
        L.append("_Needs a dom_assertion authored or a rev look — NOT a defect._")
        for r in no_oracle:
            L.append(f"- ◌ {_line(r)}")
        L.append("")
```

- [ ] **Step 4: Run — expect pass + no regression.** `python3 -m pytest tests/ -q` → all green.

- [ ] **Step 5: Commit.**

```bash
cd /home/albert/do-it
git add scripts/spec_ledger.py tests/test_no_oracle.py
git commit -m "feat(ledger): NO_ORACLE effective status — shipped-but-unverifiable, not a defect"
```

---

## Task 5 — Observation: readiness gate + target page + per-criterion evidence

**Files:** Modify `verification-loop/lib/probe.mjs` (readiness), `verification-loop/tick.mjs` (`observeCriterion` uses `criterion.dom_assertion.page`/`target.page` and a per-criterion evidence name keyed by `{spec,criterion,page,sha}`); Create `verification-loop/test/readiness.test.mjs` + a late-render fixture.

- [ ] **Step 1: Fixture — a page that renders content only after a delay** (`verification-loop/test/fixtures/late-render.html`):

```html
<!doctype html><html><body><main><section data-testid="kpis"></section></main>
<script>setTimeout(()=>{document.querySelector('[data-testid=kpis]').innerHTML='<div class="card">$62,980</div>';},400);</script>
</body></html>
```

- [ ] **Step 2: Failing test** (`verification-loop/test/readiness.test.mjs`) — a helper `waitForReady(page, selector)` resolves only once the target selector has content; assert it waits past the 400ms and then sees the card (a capture without it would see an empty section):

```javascript
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { launchBrowser } from '../lib/browser.mjs';
import { waitForReady } from '../lib/probe.mjs';

let browser; before(async()=>{browser=await launchBrowser();}); after(async()=>{await browser.close();});

test('waitForReady waits for the target selector to have content', async () => {
  const page = await browser.newPage();
  await page.goto(pathToFileURL(path.resolve('test/fixtures/late-render.html')).href);
  await waitForReady(page, '[data-testid="kpis"] .card', 5000);
  const txt = await page.locator('[data-testid="kpis"]').innerText();
  await page.close();
  assert.match(txt, /62,980/);   // content present after readiness, not the empty pre-hydration frame
});
```

- [ ] **Step 3: Run — expect fail** (no `waitForReady` export). `cd /home/albert/do-it/verification-loop && npm test`.

- [ ] **Step 4: Implement.** Export `waitForReady` from `probe.mjs`:

```javascript
/** Resolve once the criterion's target selector exists AND has non-empty text —
 *  defeats pre-hydration capture (the "only sidebar" artifact). Falls back to a
 *  short settle if no selector is given. */
export async function waitForReady(page, selector, timeout = 15000) {
  if (selector) {
    await page.locator(selector).first().waitFor({ state: 'visible', timeout });
    await page.waitForFunction(
      (sel) => { const el = document.querySelector(sel); return el && el.innerText.trim().length > 0; },
      selector, { timeout },
    ).catch(() => {});
  }
  await page.waitForLoadState('networkidle', { timeout: 5000 }).catch(() => {});
}
```

In `observeCriterion`'s `DOM_ASSERT` path (Task-1 verified), before `runDomAssertion`, call `await waitForReady(page, a.selector)` (the assertion's own selector is the readiness target). For the advisory DOM/VISION path, observe `cfg.page_map[criterion.target?.page || criterion.dom_assertion?.page || 'overview']` — the criterion's page, not always overview — and name the evidence `${spec_id}-${criterion.id}-${page}-${sha}.json` so two criteria can never share an artifact.

- [ ] **Step 5: Run — expect pass; syntax.** `npm test && node --check tick.mjs`.

- [ ] **Step 6: Commit.**

```bash
cd /home/albert/do-it
git add verification-loop/lib/probe.mjs verification-loop/tick.mjs verification-loop/test/readiness.test.mjs verification-loop/test/fixtures/late-render.html
git commit -m "feat(verifier): readiness gate + target-page + per-criterion evidence (kills pre-hydration/shared-snapshot)"
```

---

## Task 6 — Trigger model (no LLM judging on an idle tick)

**Files:** Create `verification-loop/lib/triggers.mjs` + `verification-loop/test/triggers.test.mjs`; wire into `tick.mjs` Step 1.

- [ ] **Step 1: Failing test** — `shouldJudge({newSha, shippedSinceLastTick, requested, isCanaryWindow})` returns true only when something warrants judging:

```javascript
import { test } from 'node:test'; import assert from 'node:assert/strict';
import { shouldJudge } from '../lib/triggers.mjs';
test('idle tick: no new sha, nothing shipped, not requested, not canary → skip', () => {
  assert.equal(shouldJudge({ newSha:false, shippedSinceLastTick:false, requested:false, isCanaryWindow:false }), false);
});
test('new sha → judge', () => { assert.equal(shouldJudge({ newSha:true }), true); });
test('explicit request → judge', () => { assert.equal(shouldJudge({ requested:true }), true); });
test('daily canary window → judge', () => { assert.equal(shouldJudge({ isCanaryWindow:true }), true); });
```

- [ ] **Step 2: Run — expect fail.** **Step 3: Implement** `verification-loop/lib/triggers.mjs`:

```javascript
/** Judge only when warranted — not every criterion every tick. */
export function shouldJudge({ newSha = false, shippedSinceLastTick = false, requested = false, isCanaryWindow = false } = {}) {
  return Boolean(newSha || shippedSinceLastTick || requested || isCanaryWindow);
}
```

Wire into `tick.mjs` Step 1: after computing `currentSha`/`prevSha`, compute `newSha = currentSha !== prevSha`, read a `requested` flag (a `/tmp/verifier-request` sentinel or `--force`), an `isCanaryWindow` (e.g. one tick/day), and `if (!shouldJudge({newSha, requested: FORCE, isCanaryWindow})) { log('idle — no judging'); return; }` — replacing the bare idle-sha early-return so the canary/request paths exist.

- [ ] **Step 4: Run — pass; syntax. Step 5: Commit** `feat(verifier): trigger model — judge on new-sha/request/canary, not every tick`.

---

## Task 7 — Supersession (stale criteria are SUPERSEDED, not failed)

**Files:** Modify `scripts/spec_ledger.py`; Create test in `tests/test_no_oracle.py` (append).

- [ ] **Step 1: Failing test** — a verdict file carrying `superseded_by: <id>` renders the spec as `superseded`, never `needs-rework`, even with a REJECTED criterion:

```python
def test_superseded_criterion_not_rework(monkeypatch, tmp_path):
    sl = _load(monkeypatch, tmp_path)
    assert sl.effective_status({"status": "shipped"}, {"verdict": "REJECTED", "superseded_by": "071-x"}) == "superseded"
```

- [ ] **Step 2: Run — fail. Step 3: Implement** — in `effective_status`, first line of the shipped branch: `if (verdict or {}).get("superseded_by"): return "superseded"`. **Step 4: Run — pass. Step 5: Commit** `feat(ledger): superseded verdict beats reject (stale criteria not failed)`.

---

## Task 8 — Docs, version v3.7.0, CHANGELOG

**Files:** Modify `skills/verification-loop/SKILL.md`, `skills/rev/SKILL.md`, `DO-IT.md`, `CHANGELOG.md`.

- [ ] **Step 1:** Update `skills/verification-loop/SKILL.md` to the authority model: the loop emits *signals*; only `dom-assert` + `rev` produce *authority*; LLM-on-snapshot is advisory; `NO_ORACLE` is the honest resting state; the trigger model; rev never gets weak-observation noise.
- [ ] **Step 2:** Update `skills/rev/SKILL.md` — rev verifies *the verifier*: approves drafted assertions, rules on supersession, handles repeated *deterministic* failures + taste; never consumes `NO_ORACLE`/`NEEDS_ASSERTION` as product defects.
- [ ] **Step 3:** `DO-IT.md` §2 evidence-gate paragraph → the authority tiers. `**Version:** 3.6.0` → `3.7.0`. Add `CHANGELOG.md`:

```markdown
## [3.7.0] — 2026-06-08

**Verifier v2 — signal ≠ authority.** Only an executable `dom_assertion` (or a
human `rev`) writes a hard ledger verdict; LLM-on-snapshot is advisory for both
reject and confirm; an un-asserted criterion is `NO_ORACLE` (shipped-but-not-
machine-verifiable, never a defect). Fixes the false-reject churn (21 healthy
specs hard-rejected from sidebar-only snapshots). Adds readiness gate + target-page
+ per-criterion evidence binding (kills pre-hydration/shared-snapshot), a trigger
model (no judging on idle ticks), and supersession for stale criteria. Corrects
the v3.5/3.6 gap that left the LLM path hard-authoritative.
```

- [ ] **Step 4:** Full matrix green — `python3 -m pytest tests/ -q && cd verification-loop && npm test`. **Commit** `chore(release): v3.7.0 — verifier v2 (signal != authority)`.

---

## Task 9 — Live AS sync + acceptance (operational, at deploy)

**Does NOT run in the public repo as a unit test.**
- [ ] Sync the full v3.7.0 `verification-loop/` + `spec_ledger.py` to the live `~/.claude/verification-loop` and `$REPO_ROOT/scripts/` (the latter via orc, per the rollout rule).
- [ ] Re-run a tick: confirm idle-skip works, codex criteria emit advisory `NO_ORACLE`/`NEEDS_ASSERTION` (no hard verdicts), and a `dom_assert` criterion (106) still produces a hard verdict.
- [ ] Confirm the board: the 21 are gone from NEEDS-REWORK; un-asserted shipped specs show under NO-ORACLE; rev's queue carries no weak-observation noise.
- [ ] Begin the assertion backlog (separate, ongoing): LLM-draft + rev-accept `dom_assertion`s for high-blast-radius cards.

---

## Self-Review (plan author)
- **Spec coverage:** kill-switch (T1) + deploy (T2) + quarantine (T3) = the urgent authority fix; `NO_ORACLE` render (T4); observation readiness/target-page/evidence-binding (T5); trigger model (T6); supersession (T7); docs/version (T8); live sync + acceptance (T9). The assertion-drafting *backlog* and visual-diff/interaction-driver stay deferred per the spec.
- **Placeholder scan:** none in T1–T8; T9 is the named operational acceptance.
- **Type consistency:** `ledgerActionFor(verdict, judge) → {ledger, escalate}` used identically in the test and the resolve wiring; `effective_status` new returns (`no-oracle`, `superseded`) are covered by tests and rendered; `waitForReady(page, selector, timeout)` and `shouldJudge({...})` signatures match their tests.
- **Ship order honored:** T1–T3 are the standalone churn-stopper (deploy after T3); T4–T9 follow.
