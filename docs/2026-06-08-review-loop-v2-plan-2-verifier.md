# Review Loop v2 — Plan 2 (Executable Verifier) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the verifier produce verdicts from an *executable* observation of the rendered page — a Playwright `dom_assertion` that runs before any LLM — and write per-criterion `CONFIRMED`/`REJECTED` into the ledger, so the v3.4.0 derived join has real input and A1 (a blank-but-present section) comes back `needs-rework`.

**Architecture:** Two new pure-ish modules — `lib/predicate.mjs` (parse/evaluate a predicate string) and `lib/assert-dom.mjs` (run a `dom_assertion` in Playwright, fail on 0-match / failed predicate / forbidden console) — plus `lib/cardschema.mjs` (parse the machine-readable `criteria:` block authored in the review card). `tick.mjs` is wired to: load criteria from the card schema (fail closed for a UI criterion with no assertion), run the assertion before the LLM, write per-criterion verdicts via `spec_ledger.py verify --criterion`, and skip a spec until 10 min after it shipped.

**Tech Stack:** Node 24 (built-in `node:test`), Playwright (chromium, already a dep), `js-yaml` (new dep) for the card block. Pure modules + the assertion runner are tested against local `file://` HTML fixtures — no live app, no network. The **live A1 proof** against the real deployed page is the acceptance gate, run at sync time (Plan 3 / AS rollout), not in this repo's unit tests.

**Scope note:** Plan 2 of 3 for the v3.4.0 design (`docs/2026-06-08-review-loop-prod-verdict-design.md`), building on the shipped v3.4.0 ledger substrate (Plan 1). It ships as **v3.5.0**. Plan 3 (ops crons + the standing `rev` session + `rev-watch/`) follows as v3.6.0. Do not bump the version until Task 8.

---

## The machine-readable criterion schema (the contract this plan introduces)

The review card (`~/.claude/brief-inbox/<slug>.review.md`, authored by orc) carries a fenced YAML block the verifier parses. The human markdown of the card is unchanged; this block is added by orc at card-write time (design item 2 — orc has seen the rendered page and picks a stable `data-testid` selector).

````markdown
```yaml verifier:criteria
criteria:
  - id: c1
    text: "A1 region-flow table renders with at least one data row"
    criterion_type: ui                 # ui | backend
    dom_assertion:                      # REQUIRED when criterion_type == ui
      page: overview                    # a key in cfg.page_map (or an absolute path)
      selector: "[data-testid='a1-region-table'] tbody tr"
      predicate: "min_rows:1"          # min_rows:N | count_gte:N | text_matches:<regex>
      forbid_console: ["ZodError", "Unhandled"]
  - id: c2
    text: "GET /flow-matrix returns total_out > 0"
    criterion_type: backend            # backend criteria keep the curl/judge path
```
````

Rules the verifier enforces: a `ui` criterion **must** have a `dom_assertion`; `predicate: present` is **rejected** for `ui` (a blank-but-mounted container satisfies "present" — the A1 trap); a selector matching **0 elements** is a REJECTED; a `forbid_console` hit is a REJECTED.

---

## File Structure

- **Create** `verification-loop/lib/predicate.mjs` — `parsePredicate(str)` + `evalPredicate(predicate, {count, text})`.
- **Create** `verification-loop/lib/cardschema.mjs` — `extractCriteriaBlock(cardText)` (parse the fenced `verifier:criteria` YAML) + `validateCriterion(c)`.
- **Create** `verification-loop/lib/assert-dom.mjs` — `runDomAssertion(page, assertion)` (operates on an open Playwright page; returns `{pass, reason, observed}`).
- **Modify** `verification-loop/tick.mjs` — `loadShippedSpecs` (capture `review_card` + `shipped_at`), `loadCriteria` (prefer the card schema; fail closed), `observeCriterion` (assertion-first for `ui`), the resolve step (write per-criterion verdicts), `callSpecLedgerVerify` (use `--criterion`), and a 10-minute post-ship delay.
- **Modify** `verification-loop/package.json` — add `js-yaml` dep + `"test": "node --test test/"`.
- **Create** `verification-loop/test/predicate.test.mjs`, `verification-loop/test/cardschema.test.mjs`, `verification-loop/test/assert-dom.test.mjs`, and `verification-loop/test/fixtures/{a1-blank.html,a1-populated.html}`.

---

## Task 1: `lib/predicate.mjs` — predicate parsing + evaluation

**Files:**
- Create: `verification-loop/lib/predicate.mjs`
- Create: `verification-loop/test/predicate.test.mjs`
- Modify: `verification-loop/package.json` (add the test script)

- [ ] **Step 1: Add the test script to package.json**

Edit `verification-loop/package.json` so it has a `scripts.test` (keep existing fields; add `js-yaml` now too, used in Task 2):

```json
{
  "name": "verification-loop",
  "type": "module",
  "version": "0.1.0",
  "scripts": { "test": "node --test test/" },
  "dependencies": { "playwright": "^1.60.0", "js-yaml": "^4.1.0" }
}
```

Then install: `cd /home/albert/do-it/verification-loop && npm install`.

- [ ] **Step 2: Write the failing test**

Create `verification-loop/test/predicate.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parsePredicate, evalPredicate } from '../lib/predicate.mjs';

test('parsePredicate min_rows', () => {
  assert.deepEqual(parsePredicate('min_rows:1'), { kind: 'min_rows', n: 1 });
});
test('parsePredicate count_gte', () => {
  assert.deepEqual(parsePredicate('count_gte:3'), { kind: 'count_gte', n: 3 });
});
test('parsePredicate text_matches', () => {
  assert.deepEqual(parsePredicate('text_matches:\\d+%'), { kind: 'text_matches', re: '\\d+%' });
});
test('parsePredicate rejects present (the A1 trap)', () => {
  assert.throws(() => parsePredicate('present'), /present.*not allowed|forbidden/i);
});
test('parsePredicate rejects unknown', () => {
  assert.throws(() => parsePredicate('whatever:1'), /unknown predicate/i);
});

test('evalPredicate min_rows passes/fails on count', () => {
  assert.equal(evalPredicate({ kind: 'min_rows', n: 1 }, { count: 1 }).pass, true);
  assert.equal(evalPredicate({ kind: 'min_rows', n: 1 }, { count: 0 }).pass, false);
});
test('evalPredicate text_matches against observed text', () => {
  assert.equal(evalPredicate({ kind: 'text_matches', re: '\\d+%' }, { text: 'up 12%' }).pass, true);
  assert.equal(evalPredicate({ kind: 'text_matches', re: '\\d+%' }, { text: 'no data' }).pass, false);
});
```

- [ ] **Step 3: Run it — expect failure**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — cannot find `../lib/predicate.mjs`.

- [ ] **Step 4: Implement `lib/predicate.mjs`**

```javascript
// Predicate grammar for dom_assertion. `present` is deliberately NOT supported:
// a blank-but-mounted container satisfies "present", which is the exact A1 failure.
export function parsePredicate(str) {
  const s = String(str || '').trim();
  if (s === 'present' || s.startsWith('present')) {
    throw new Error("predicate 'present' is not allowed for a ui criterion — use min_rows:N, count_gte:N, or text_matches:<re>");
  }
  const i = s.indexOf(':');
  const kind = i === -1 ? s : s.slice(0, i);
  const arg = i === -1 ? '' : s.slice(i + 1);
  if (kind === 'min_rows') return { kind, n: parseInt(arg, 10) };
  if (kind === 'count_gte') return { kind, n: parseInt(arg, 10) };
  if (kind === 'text_matches') return { kind, re: arg };
  throw new Error(`unknown predicate: ${str}`);
}

export function evalPredicate(predicate, observed) {
  const { count = 0, text = '' } = observed || {};
  switch (predicate.kind) {
    case 'min_rows':
    case 'count_gte':
      return { pass: count >= predicate.n, reason: `count=${count} vs ${predicate.kind} ${predicate.n}` };
    case 'text_matches': {
      const ok = new RegExp(predicate.re).test(text);
      return { pass: ok, reason: ok ? `text matched /${predicate.re}/` : `text did not match /${predicate.re}/` };
    }
    default:
      return { pass: false, reason: `uncheckable predicate ${predicate.kind}` };
  }
}
```

- [ ] **Step 5: Run it — expect pass**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: PASS (predicate tests).

- [ ] **Step 6: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/predicate.mjs verification-loop/test/predicate.test.mjs verification-loop/package.json verification-loop/package-lock.json
git commit -m "feat(verifier): predicate grammar — present forbidden for ui criteria"
```

---

## Task 2: `lib/cardschema.mjs` — parse the machine-readable criteria block

**Files:**
- Create: `verification-loop/lib/cardschema.mjs`
- Create: `verification-loop/test/cardschema.test.mjs`

- [ ] **Step 1: Write the failing test**

Create `verification-loop/test/cardschema.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extractCriteriaBlock, validateCriterion } from '../lib/cardschema.mjs';

const CARD = [
  '# Review card', 'human prose here',
  '```yaml verifier:criteria',
  'criteria:',
  '  - id: c1',
  '    text: "A1 table renders"',
  '    criterion_type: ui',
  '    dom_assertion:',
  '      page: overview',
  "      selector: \"[data-testid='a1'] tbody tr\"",
  '      predicate: "min_rows:1"',
  '      forbid_console: ["ZodError"]',
  '  - id: c2',
  '    text: "endpoint returns data"',
  '    criterion_type: backend',
  '```',
  'more prose',
].join('\n');

test('extractCriteriaBlock parses the fenced block', () => {
  const crits = extractCriteriaBlock(CARD);
  assert.equal(crits.length, 2);
  assert.equal(crits[0].id, 'c1');
  assert.equal(crits[0].criterion_type, 'ui');
  assert.equal(crits[0].dom_assertion.predicate, 'min_rows:1');
  assert.equal(crits[1].criterion_type, 'backend');
});

test('extractCriteriaBlock returns null when no block present', () => {
  assert.equal(extractCriteriaBlock('# just prose\nno block'), null);
});

test('validateCriterion: ui without dom_assertion fails closed', () => {
  const e = validateCriterion({ id: 'c1', text: 't', criterion_type: 'ui' });
  assert.match(e, /ui criterion.*dom_assertion/i);
});

test('validateCriterion: ui with present predicate is rejected', () => {
  const e = validateCriterion({ id: 'c1', text: 't', criterion_type: 'ui',
    dom_assertion: { page: 'overview', selector: 'x', predicate: 'present' } });
  assert.match(e, /present/i);
});

test('validateCriterion: a valid ui criterion returns null', () => {
  const e = validateCriterion({ id: 'c1', text: 't', criterion_type: 'ui',
    dom_assertion: { page: 'overview', selector: 'x', predicate: 'min_rows:1' } });
  assert.equal(e, null);
});

test('validateCriterion: backend needs no dom_assertion', () => {
  assert.equal(validateCriterion({ id: 'c2', text: 't', criterion_type: 'backend' }), null);
});
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — cannot find `../lib/cardschema.mjs`.

- [ ] **Step 3: Implement `lib/cardschema.mjs`**

```javascript
import yaml from 'js-yaml';
import { parsePredicate } from './predicate.mjs';

const BLOCK_RE = /```ya?ml\s+verifier:criteria\s*\n([\s\S]*?)\n```/;

/** Return the criteria array from the card's fenced `verifier:criteria` block, or null. */
export function extractCriteriaBlock(cardText) {
  const m = BLOCK_RE.exec(String(cardText || ''));
  if (!m) return null;
  const doc = yaml.load(m[1]);
  if (!doc || !Array.isArray(doc.criteria)) return null;
  return doc.criteria;
}

/** Return an error string if the criterion is invalid, else null. */
export function validateCriterion(c) {
  if (!c || !c.id || !c.text) return 'criterion missing id or text';
  if (c.criterion_type === 'ui') {
    const a = c.dom_assertion;
    if (!a) return `ui criterion ${c.id} requires a dom_assertion (fail closed)`;
    if (!a.selector || !a.predicate || !a.page) {
      return `ui criterion ${c.id} dom_assertion needs page, selector, predicate`;
    }
    try { parsePredicate(a.predicate); }
    catch (e) { return `ui criterion ${c.id}: ${e.message}`; }
  }
  return null;
}
```

- [ ] **Step 4: Run it — expect pass**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/cardschema.mjs verification-loop/test/cardschema.test.mjs
git commit -m "feat(verifier): parse machine-readable criteria block from review card"
```

---

## Task 3: `lib/assert-dom.mjs` — the executable assertion (the A1 catcher)

**Files:**
- Create: `verification-loop/lib/assert-dom.mjs`
- Create: `verification-loop/test/assert-dom.test.mjs`
- Create: `verification-loop/test/fixtures/a1-blank.html`, `verification-loop/test/fixtures/a1-populated.html`

- [ ] **Step 1: Create the fixtures**

`verification-loop/test/fixtures/a1-populated.html` — the section rendered with rows:

```html
<!doctype html><html><body>
<main>
  <section data-testid="a1-region-table">
    <table><tbody>
      <tr><td>West</td><td>12%</td></tr>
      <tr><td>East</td><td>8%</td></tr>
    </tbody></table>
  </section>
</main>
</body></html>
```

`verification-loop/test/fixtures/a1-blank.html` — the A1 failure shape: the container is PRESENT but empty, and a Zod error was logged:

```html
<!doctype html><html><body>
<main>
  <section data-testid="a1-region-table">
    <table><tbody></tbody></table>
  </section>
</main>
<script>console.error('ZodError: conservation object did not match NetworkConservationSchema');</script>
</body></html>
```

- [ ] **Step 2: Write the failing test**

Create `verification-loop/test/assert-dom.test.mjs`:

```javascript
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { runDomAssertion } from '../lib/assert-dom.mjs';

const fixture = (f) => pathToFileURL(path.resolve('test/fixtures', f)).href;
let browser;
before(async () => { browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] }); });
after(async () => { await browser.close(); });

async function check(file, assertion) {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  await page.goto(fixture(file), { waitUntil: 'domcontentloaded' });
  const res = await runDomAssertion(page, assertion, consoleErrors);
  await page.close();
  return res;
}

const A1 = { selector: "[data-testid='a1-region-table'] tbody tr", predicate: 'min_rows:1', forbid_console: ['ZodError'] };

test('populated page PASSES the A1 assertion', async () => {
  const res = await check('a1-populated.html', A1);
  assert.equal(res.pass, true, res.reason);
});

test('blank-but-present container FAILS (min_rows:1 sees 0 rows)', async () => {
  const res = await check('a1-blank.html', { ...A1, forbid_console: [] });
  assert.equal(res.pass, false);
  assert.match(res.reason, /count=0|0 rows|min_rows/i);
});

test('Zod console error FAILS via forbid_console', async () => {
  const res = await check('a1-blank.html', A1);
  assert.equal(res.pass, false);
  assert.match(res.reason, /ZodError|console/i);
});

test('selector matching 0 elements FAILS, never silently passes', async () => {
  const res = await check('a1-populated.html', { selector: "[data-testid='does-not-exist']", predicate: 'min_rows:1', forbid_console: [] });
  assert.equal(res.pass, false);
});
```

- [ ] **Step 3: Run it — expect failure**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — cannot find `../lib/assert-dom.mjs`.

- [ ] **Step 4: Implement `lib/assert-dom.mjs`**

```javascript
import { parsePredicate, evalPredicate } from './predicate.mjs';

/**
 * Run a dom_assertion against an already-open Playwright `page`.
 * `consoleErrors` is an array the caller fills from page 'console' error events.
 * Returns { pass, reason, observed }. A 0-count selector or any forbidden console
 * pattern is a FAIL — never a silent pass (this is the A1 guard).
 */
export async function runDomAssertion(page, assertion, consoleErrors = []) {
  // 1. forbidden console patterns are an immediate fail (render-throw class)
  for (const pat of assertion.forbid_console || []) {
    const hit = consoleErrors.find((e) => e.includes(pat));
    if (hit) return { pass: false, reason: `forbidden console output: ${hit.slice(0, 120)}`, observed: { console: hit } };
  }

  let predicate;
  try { predicate = parsePredicate(assertion.predicate); }
  catch (e) { return { pass: false, reason: e.message, observed: {} }; }

  const loc = page.locator(assertion.selector);
  const count = await loc.count();
  let text = '';
  if (count > 0 && predicate.kind === 'text_matches') {
    try { text = (await loc.first().innerText()).slice(0, 2000); } catch { /* leave '' */ }
  }
  const { pass, reason } = evalPredicate(predicate, { count, text });
  return { pass, reason, observed: { count, text: text.slice(0, 200) } };
}
```

- [ ] **Step 5: Run it — expect pass**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: PASS — all four assert-dom tests, including the two that prove A1's shape (blank container + Zod console) is caught.

- [ ] **Step 6: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/assert-dom.mjs verification-loop/test/assert-dom.test.mjs verification-loop/test/fixtures/
git commit -m "feat(verifier): executable dom_assertion runner — catches A1 blank-but-present + console throw"
```

---

## Task 4: `loadCriteria` prefers the card schema and fails closed

**Files:**
- Modify: `verification-loop/tick.mjs` — `loadShippedSpecs` (line 136) and `loadCriteria` (line 170)
- Test: `verification-loop/test/loadcriteria.test.mjs` (create)

- [ ] **Step 1: Write the failing test**

Create `verification-loop/test/loadcriteria.test.mjs`. It tests the new `criteriaFromCard(cardText)` helper (a pure function we extract so it is unit-testable without the filesystem/tick globals):

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { criteriaFromCard } from '../lib/cardschema.mjs';

const CARD = [
  '```yaml verifier:criteria',
  'criteria:',
  '  - id: c1', '    text: "ui one"', '    criterion_type: ui',
  '    dom_assertion: { page: overview, selector: "x", predicate: "min_rows:1" }',
  '  - id: c2', '    text: "bad ui"', '    criterion_type: ui',  // no dom_assertion -> fail closed
  '```',
].join('\n');

test('criteriaFromCard returns parsed criteria and surfaces validation errors', () => {
  const { criteria, errors } = criteriaFromCard(CARD);
  assert.equal(criteria.length, 2);
  assert.equal(criteria[0].criterion_type, 'ui');
  assert.ok(errors.some((e) => /c2.*dom_assertion/i.test(e)));  // fail-closed surfaced
});

test('criteriaFromCard on a card with no block returns null criteria', () => {
  const { criteria } = criteriaFromCard('no block here');
  assert.equal(criteria, null);
});
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — `criteriaFromCard` not exported.

- [ ] **Step 3: Add `criteriaFromCard` to `lib/cardschema.mjs`**

Append to `verification-loop/lib/cardschema.mjs`:

```javascript
/** Parse a review card's criteria block and validate each. Returns
 *  { criteria: [...] | null, errors: [...] }. `criteria` is null when the card has
 *  no machine block (caller falls back to prose parsing). */
export function criteriaFromCard(cardText) {
  const criteria = extractCriteriaBlock(cardText);
  if (!criteria) return { criteria: null, errors: [] };
  const errors = [];
  for (const c of criteria) {
    const e = validateCriterion(c);
    if (e) errors.push(e);
  }
  return { criteria, errors };
}
```

- [ ] **Step 4: Wire `tick.mjs` to use it**

In `loadShippedSpecs` (line 155-160), also capture the review card pointer — add a `cardM` match and field:

```javascript
      const cardM = text.match(/^review_card:\s*(.+)$/m);
      records.push({
        spec_id,
        spec_file: fileM ? fileM[1].trim() : null,
        shipped_sha: shaM ? shaM[1].trim() : null,
        review_card: cardM ? cardM[1].trim() : null,
        status,
      });
```

At the TOP of `loadCriteria` (line 170), before the spec-file logic, prefer the card. Change the signature to `loadCriteria(specFile, specId, reviewCard)` and add:

```javascript
function loadCriteria(specFile, specId, reviewCard) {
  // Prefer the machine-readable criteria block in the review card (authored by orc).
  if (reviewCard) {
    const cardPath = path.isAbsolute(reviewCard)
      ? reviewCard
      : path.join(process.env.HOME, '.claude', 'brief-inbox', reviewCard);
    if (fs.existsSync(cardPath)) {
      const { criteria, errors } = criteriaFromCard(fs.readFileSync(cardPath, 'utf8'));
      if (criteria) {
        if (errors.length) {
          log(`  card schema errors for ${specId}: ${errors.join('; ')}`);
        }
        // Mark invalid criteria so the loop fails them closed rather than confirming.
        return criteria.map((c) => ({ ...c, schema_error: validateCriterion(c) || null }));
      }
    }
  }
  // ... existing spec-file / prose fallback unchanged ...
```

Add the import near the other `lib/` imports at the top of tick.mjs:

```javascript
import { criteriaFromCard, validateCriterion } from './lib/cardschema.mjs';
```

And update the call site (in `tick`, where `loadCriteria(spec.spec_file, spec.spec_id)` is invoked) to pass `spec.review_card`.

- [ ] **Step 5: Run the suite**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: PASS (the new `criteriaFromCard` tests; tick.mjs still imports cleanly — verify with `node --check tick.mjs`).

Run: `cd /home/albert/do-it/verification-loop && node --check tick.mjs`
Expected: no output (syntax OK).

- [ ] **Step 6: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/cardschema.mjs verification-loop/test/loadcriteria.test.mjs verification-loop/tick.mjs
git commit -m "feat(verifier): loadCriteria prefers card schema, carries schema_error for fail-closed"
```

---

## Task 5: `observeCriterion` runs the assertion before the LLM (assertion-first for ui)

**Files:**
- Modify: `verification-loop/tick.mjs` — `observeCriterion` (line 306)
- Test: covered by the assert-dom unit tests (the integration is exercised by the live A1 proof, Task 9); add a guard test for fail-closed.

- [ ] **Step 1: Write the failing guard test**

Append to `verification-loop/test/loadcriteria.test.mjs`:

```javascript
import { verdictForSchemaError } from '../lib/cardschema.mjs';

test('a ui criterion with a schema_error resolves to REJECTED, never CONFIRMED', () => {
  assert.equal(verdictForSchemaError('ui criterion c2 requires a dom_assertion'), 'REJECTED');
  assert.equal(verdictForSchemaError(null), null);  // no error -> normal flow
});
```

- [ ] **Step 2: Run — expect failure**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — `verdictForSchemaError` not exported.

- [ ] **Step 3: Implement the helper + wire observeCriterion**

Append to `verification-loop/lib/cardschema.mjs`:

```javascript
/** A criterion that failed schema validation cannot be confirmed — it is REJECTED
 *  (fail closed). Returns 'REJECTED' for a non-empty error, else null. */
export function verdictForSchemaError(err) {
  return err ? 'REJECTED' : null;
}
```

In `tick.mjs` `observeCriterion`, at the very top (after computing `layer`), add the assertion-first path for `ui` criteria carrying a `dom_assertion`:

```javascript
async function observeCriterion(criterion, cfg, statePath, dir, periodLabel) {
  // Fail-closed: a ui criterion whose card schema was invalid is REJECTED outright.
  if (criterion.schema_error) {
    return { layer: 'DOM_ASSERT', evidenceRef: null,
      judgeResult: { token: 'NOT_SATISFIED', reason: criterion.schema_error, judge: 'schema', unclear: false } };
  }

  // Executable assertion BEFORE any LLM for ui criteria with a dom_assertion.
  if (criterion.criterion_type === 'ui' && criterion.dom_assertion) {
    const a = criterion.dom_assertion;
    const pagePath = cfg.page_map[a.page] || a.page;
    const url = cfg.prod_base + pagePath;
    const { chromium } = await import('playwright');
    const browser = await chromium.launch({ headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const ctx = await browser.newContext({ storageState: statePath });
    const page = await ctx.newPage();
    const consoleErrors = [];
    page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
    let res;
    try {
      await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.locator('main, [role="main"], #__next').first().waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
      res = await runDomAssertion(page, a, consoleErrors);
    } catch (e) {
      res = { pass: false, reason: `assertion error: ${e.message}`, observed: {} };
    } finally {
      await browser.close();
    }
    const evidenceFile = path.join(dir, 'evidence', `${criterion.id}-${periodLabel}.json`);
    fs.mkdirSync(path.join(dir, 'evidence'), { recursive: true });
    fs.writeFileSync(evidenceFile, JSON.stringify({ criterion: criterion.text, layer: 'DOM_ASSERT', url, assertion: a, result: res, at: new Date().toISOString() }, null, 2));
    return {
      layer: 'DOM_ASSERT',
      evidenceRef: evidenceFile,
      judgeResult: { token: res.pass ? 'SATISFIED' : 'NOT_SATISFIED', reason: res.reason, judge: 'dom-assert', unclear: false },
    };
  }

  // ... existing DOM / VISION layers below, unchanged ...
```

Add the import at the top of tick.mjs:

```javascript
import { runDomAssertion } from './lib/assert-dom.mjs';
```

(`verdictForSchemaError` is exercised by the unit test; `observeCriterion` uses the `schema_error` short-circuit directly, which is the same contract.)

- [ ] **Step 4: Run — expect pass + syntax check**

Run: `cd /home/albert/do-it/verification-loop && npm test && node --check tick.mjs`
Expected: PASS, no syntax errors.

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/cardschema.mjs verification-loop/test/loadcriteria.test.mjs verification-loop/tick.mjs
git commit -m "feat(verifier): assertion-first observation for ui criteria; fail closed on schema error"
```

---

## Task 6: Write per-criterion verdicts (REJECTED reaches the ledger)

**Files:**
- Modify: `verification-loop/tick.mjs` — `callSpecLedgerVerify` (line 274) and the resolve step (line 700-751)

- [ ] **Step 1: Change `callSpecLedgerVerify` to take a criterion id and use `--criterion`**

Replace the `spawn` args in `callSpecLedgerVerify` so it passes a per-criterion verdict (the v3.4.0 ledger derives the spec-level verdict from the map). New signature: `callSpecLedgerVerify(specId, criterionId, verdict, judge, evidenceRef)`:

```javascript
async function callSpecLedgerVerify(specId, criterionId, verdict, judge, evidenceRef) {
  if (DRY_RUN) {
    log(`[DRY-RUN] spec_ledger.py verify ${specId} --criterion ${criterionId}=${verdict} --judge ${judge}`);
    return;
  }
  if (!fs.existsSync(LEDGER_PY)) { log(`WARNING: spec_ledger.py not found at ${LEDGER_PY}`); return; }
  return new Promise((resolve) => {
    const child = spawn(PYTHON_BIN, [
      LEDGER_PY, 'verify', specId,
      '--judge', judge, '--evidence', evidenceRef,
      '--criterion', `${criterionId}=${verdict}`,
    ], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '', err = '';
    child.stdout.on('data', d => { out += d; });
    child.stderr.on('data', d => { err += d; });
    child.on('close', (code) => {
      if (code !== 0) log(`WARNING: spec_ledger.py verify exited ${code}: ${err.slice(0, 200)}`);
      else log(`spec_ledger verify: ${out.trim()}`);
      resolve();
    });
  });
}
```

- [ ] **Step 2: Update the resolve step to write BOTH confirmed and rejected per criterion**

In the resolve block (line 700-751), the `CONFIRMED` branch becomes a per-criterion CONFIRMED, and the `HOLLOW`/`MISSING`/`REGRESSION` branch now ALSO writes a per-criterion REJECTED (in addition to the existing escalate):

```javascript
      // ── Step 6: resolve ──────────────────────────────────────────────────────
      if (verdict === 'CONFIRMED') {
        log(`  CONFIRMED`);
        await callSpecLedgerVerify(spec.spec_id, criterion.id, 'CONFIRMED', finalJudgeResult.judge, evidenceRef || 'none');
      } else if (verdict === 'HOLLOW' || verdict === 'MISSING' || verdict === 'REGRESSION') {
        log(`  ${verdict} — writing REJECTED + filing corrective (attempt ${attempts + 1}/${TRIAL_BUDGET})`);
        await callSpecLedgerVerify(spec.spec_id, criterion.id, 'REJECTED', finalJudgeResult.judge, evidenceRef || 'none');
        escalate(dir, {
          // ... existing corrective escalate payload unchanged ...
```

Leave the `DATA-GAP`/`NOT-RUN`, `SUSPECTED-GAMING`, and `TASTE` branches as-is (they escalate; they do not write a spec-ledger verdict — `not-applicable`/incomplete is represented by the absence of a CONFIRMED, which the v3.4.0 aggregation already treats as `awaiting-prod`). For `DATA-GAP`, optionally write `--criterion <id>=not-applicable` so the aggregation can still confirm the rest of the spec — add:

```javascript
      } else if (verdict === 'DATA-GAP') {
        await callSpecLedgerVerify(spec.spec_id, criterion.id, 'not-applicable', finalJudgeResult.judge, evidenceRef || 'none');
        escalate(dir, { /* existing DATA-GAP escalate payload */ });
```

(Find every existing `callSpecLedgerVerify(spec.spec_id, 'CONFIRMED', ...)` call — there is one around line 703 — and update it to the new 5-arg form with `criterion.id`.)

- [ ] **Step 3: Syntax check**

Run: `cd /home/albert/do-it/verification-loop && node --check tick.mjs && npm test`
Expected: no syntax errors; existing unit tests still pass.

- [ ] **Step 4: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/tick.mjs
git commit -m "feat(verifier): write per-criterion CONFIRMED/REJECTED/not-applicable into the ledger"
```

---

## Task 7: 10-minute post-ship delay (interim for the deferred SHA gate)

**Files:**
- Modify: `verification-loop/tick.mjs` — `loadShippedSpecs` (capture `shipped_at`) + the per-spec loop (skip if too fresh)
- Test: `verification-loop/test/freshness.test.mjs` (create) — tests a pure helper

- [ ] **Step 1: Write the failing test**

Create `verification-loop/test/freshness.test.mjs`:

```javascript
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { tooFreshToVerify } from '../lib/freshness.mjs';

test('a spec shipped 2 minutes ago is too fresh (10-min floor)', () => {
  const twoMinAgo = new Date(Date.now() - 2 * 60 * 1000).toISOString();
  assert.equal(tooFreshToVerify(twoMinAgo, 10), true);
});
test('a spec shipped 20 minutes ago is ready', () => {
  const twentyMinAgo = new Date(Date.now() - 20 * 60 * 1000).toISOString();
  assert.equal(tooFreshToVerify(twentyMinAgo, 10), false);
});
test('missing timestamp is treated as ready (do not block)', () => {
  assert.equal(tooFreshToVerify(null, 10), false);
});
```

- [ ] **Step 2: Run — expect failure**

Run: `cd /home/albert/do-it/verification-loop && npm test`
Expected: FAIL — cannot find `../lib/freshness.mjs`.

- [ ] **Step 3: Implement `lib/freshness.mjs` + wire tick.mjs**

Create `verification-loop/lib/freshness.mjs`:

```javascript
/** True if a spec shipped fewer than `floorMinutes` ago (give the deploy time to go
 *  live before asserting — the interim for the deferred deployed_sha gate). A missing
 *  timestamp does not block. */
export function tooFreshToVerify(shippedAtIso, floorMinutes = 10) {
  if (!shippedAtIso) return false;
  const t = Date.parse(shippedAtIso);
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) < floorMinutes * 60 * 1000;
}
```

In `tick.mjs`: import it (`import { tooFreshToVerify } from './lib/freshness.mjs';`), capture `shipped_at` in `loadShippedSpecs` (parse the last `shipped` history entry's `at`, or a top-level `shipped_at:` if present — use a regex like the others: `const shipAtM = text.match(/^\s*-\s*at:\s*'?([^'\n]+)'?\n\s*status:\s*shipped/m);` and store `shipped_at`), and in the per-spec loop, near the top (before the criteria loop), skip:

```javascript
    if (tooFreshToVerify(spec.shipped_at, 10)) {
      log(`  skip ${spec.spec_id} — shipped <10min ago, letting the deploy go live`);
      continue;
    }
```

- [ ] **Step 4: Run — expect pass + syntax**

Run: `cd /home/albert/do-it/verification-loop && npm test && node --check tick.mjs`
Expected: PASS, no syntax errors.

- [ ] **Step 5: Commit**

```bash
cd /home/albert/do-it
git add verification-loop/lib/freshness.mjs verification-loop/test/freshness.test.mjs verification-loop/tick.mjs
git commit -m "feat(verifier): 10-min post-ship delay (interim for deferred deployed_sha gate)"
```

---

## Task 8: Full suite green, version bump, CHANGELOG

**Files:**
- Modify: `DO-IT.md` (version line), `CHANGELOG.md`

- [ ] **Step 1: Full Node suite + syntax**

Run: `cd /home/albert/do-it/verification-loop && npm test 2>&1 | tail -20 && node --check tick.mjs`
Expected: all `node --test` files pass; no syntax error.

- [ ] **Step 2: Confirm the Python ledger suite is untouched/green**

Run: `cd /home/albert/do-it && python3 -m pytest tests/ -q`
Expected: 46 passed (Plan 2 does not touch the Python side).

- [ ] **Step 3: Bump version to 3.5.0**

In `DO-IT.md` change `**Version:** 3.4.0` → `**Version:** 3.5.0`. Add a `CHANGELOG.md` entry under the top:

```markdown
## [3.5.0] — 2026-06-08

**Review Loop v2 — Part 2 of 3: the executable verifier.** The verifier now
produces verdicts from an executable rendered-page observation, feeding the v3.4.0
derived join.

### Added
- `lib/predicate.mjs`, `lib/assert-dom.mjs`, `lib/cardschema.mjs`, `lib/freshness.mjs`
  (+ `node --test` fixtures). The `dom_assertion` runner catches the A1 class —
  a blank-but-present container (`min_rows`/`count_gte`/`text_matches`, never
  `present`) and a render-throw via `forbid_console`.
- Machine-readable `verifier:criteria` block in the review card; the verifier parses
  it and **fails closed** on a ui criterion with no/invalid `dom_assertion`.

### Changed
- The verifier writes **per-criterion** `CONFIRMED`/`REJECTED`/`not-applicable` via
  `spec_ledger.py verify --criterion`; failures now reach `verified/` (not just
  `NEEDS-HUMAN.jsonl`), so `needs-rework` is reachable.
- A spec is skipped until 10 min after it shipped (interim for the deferred
  `deployed_sha` gate). Part 3 (ops crons + the standing `rev` session) follows.
```

- [ ] **Step 4: Commit**

```bash
cd /home/albert/do-it
git add DO-IT.md CHANGELOG.md
git commit -m "chore(release): v3.5.0 — review-loop v2 part 2 (executable verifier)"
```

---

## Task 9: Live A1 proof (acceptance — run at SYNC, against the AS instance)

**This task does NOT run in the public repo.** It is the acceptance gate, executed during the AS-instance sync (Plan 3 / rollout), against the real deployed page, by the orc. Documented here so it is not lost.

- [ ] Author the `verifier:criteria` block on spec 106's review card with the real A1 selector (`data-testid` orc adds to the region-flow table) and `predicate: min_rows:1`, `forbid_console: ["ZodError"]`.
- [ ] Run one forced tick against the live config: `node verification-loop/tick.mjs --config <as-project> --spec 106-... --force`.
- [ ] Confirm `~/.claude/ledger/verified/106-*.yml` gets `criteria: {c<n>: REJECTED}` and a derived `verdict: REJECTED`, and `spec_ledger.py --render` shows 106 under the top `❌ NEEDS-REWORK` section — **A1, which passed every old gate, now fails on the rendered page.**

---

## Self-Review (completed by plan author)

- **Spec coverage:** Plan 2 covers design item 2 (executable dom_assertion, LLM-secondary, card-schema parsing, fail-closed, `present` forbidden), the verifier half of item 3 (writing per-criterion REJECTED/CONFIRMED/not-applicable), and the 10-min interim from the deferred `deployed_sha` section. The vision-judge-with-image-bytes weakness stays deferred (design out-of-scope). Item 1 (cron/watchdog), item 5's durable NEEDS-HUMAN store, and item 6 (`rev`/`rev-watch`) are Plan 3.
- **Placeholder scan:** none in the buildable tasks (1-8). Task 9 is explicitly an acceptance gate run elsewhere, with concrete steps.
- **Type consistency:** `parsePredicate→{kind,...}`, `evalPredicate(pred,{count,text})→{pass,reason}`, `extractCriteriaBlock→[criteria]|null`, `validateCriterion(c)→string|null`, `criteriaFromCard→{criteria,errors}`, `runDomAssertion(page,assertion,consoleErrors)→{pass,reason,observed}`, `callSpecLedgerVerify(specId,criterionId,verdict,judge,evidenceRef)`, `tooFreshToVerify(iso,floor)→bool` are used consistently across tasks.
- **Testability honesty:** Tasks 1-7 are unit-tested (pure modules + the assertion runner against `file://` fixtures that reproduce A1's exact shape). The live end-to-end A1 proof is Task 9 at sync — it needs the real deployment and cannot run in this repo.
