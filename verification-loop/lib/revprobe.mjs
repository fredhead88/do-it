/**
 * revprobe.mjs — hardened reviewer-probe helpers (rev prod-reachability P4).
 *
 * Every one-off `rev-*.mjs` probe re-implements the same handful of things, and
 * each one is a place a reviewer has historically produced a FALSE REJECT or a
 * hang. This module codifies those gotchas ONCE so they stop being re-learned
 * from relay batons every session. It builds on the existing lib (config/auth/
 * browser) — it does not duplicate login or browser launch.
 *
 * The four false-reject / hang classes this kills:
 *   1. period_end off-by-one (the 198 class). A full month is [first, first-of-
 *      NEXT-month) — period_end is EXCLUSIVE. Hand-typing "2026-05-31" excludes
 *      the 31st and under-counts the month, reading as a regression that isn't.
 *      Build ranges with fullMonth()/fullMonthRange(); periodQuery() throws if a
 *      full-month start is paired with a non-first-of-month end.
 *   2. client scope selected by ASIN string instead of product TITLE — selects
 *      nothing, surface looks empty, false "not ingested". applyClientScope()
 *      selects by visible title.
 *   3. scoped overview KPI cards render blank for ~5–10s headless; a 4.5s wait is
 *      a false negative, ~11s works. waitForCardsRendered() polls for populated
 *      cards with a 15s default ceiling instead of a fixed short sleep.
 *   4. buffered stdout hanging after a few dozen probe lines. flushLine() writes
 *      one synchronously-flushed line per result.
 *
 * Plus: readOnlyApiContext() uses rev's P2 read-only API key to GET any endpoint
 * directly — no user login — which is the cleanest staff-equivalent read path.
 */
import { request as pwRequest } from 'playwright';
import fs from 'node:fs';
import { launchBrowser } from './browser.mjs';

// ── env ──────────────────────────────────────────────────────────────────────

/** Parse a dotenv file into a plain object. The one-off probes all reimplement
 *  this; here it is once. Strips surrounding single/double quotes. */
export function loadEnv(envPath = '<repo root>/.env') {
  const env = {};
  for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
    const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (m) env[m[1]] = m[2].replace(/^['"]|['"]$/g, '');
  }
  return env;
}

// ── period helpers (the 198 off-by-one killer) ───────────────────────────────

function _pad(n) { return String(n).padStart(2, '0'); }

/** '2026-05' (or '2026-05-anything') -> '2026-06-01' (first of NEXT month). */
export function firstOfNextMonth(ym) {
  const m = String(ym).match(/^(\d{4})-(\d{2})/);
  if (!m) throw new Error(`firstOfNextMonth: bad year-month ${ym}`);
  let year = Number(m[1]);
  let month = Number(m[2]);
  if (month < 1 || month > 12) throw new Error(`firstOfNextMonth: bad month ${ym}`);
  month += 1;
  if (month === 13) { month = 1; year += 1; }
  return `${year}-${_pad(month)}-01`;
}

/** Full-month FE-natural range: { period_start, period_end } with EXCLUSIVE end.
 *  fullMonth('2026-05') -> { period_start:'2026-05-01', period_end:'2026-06-01' } */
export function fullMonth(ym) {
  const m = String(ym).match(/^(\d{4})-(\d{2})/);
  if (!m) throw new Error(`fullMonth: bad year-month ${ym}`);
  return { period_start: `${m[1]}-${m[2]}-01`, period_end: firstOfNextMonth(ym) };
}

/** Span across whole months, inclusive of both endpoints' months, EXCLUSIVE end.
 *  fullMonthRange('2026-04','2026-05') -> {period_start:'2026-04-01', period_end:'2026-06-01'} */
export function fullMonthRange(startYm, endYm) {
  const a = String(startYm).match(/^(\d{4})-(\d{2})/);
  if (!a) throw new Error(`fullMonthRange: bad start ${startYm}`);
  return { period_start: `${a[1]}-${a[2]}-01`, period_end: firstOfNextMonth(endYm) };
}

function _isFirstOfMonth(d) { return /^\d{4}-\d{2}-01$/.test(d); }

/** Guard the 198 off-by-one: if the start is a first-of-month (i.e. the caller
 *  clearly intends a full month) but the end is NOT a first-of-month, that is
 *  the inclusive-vs-exclusive mistake. Throw loudly at construction time rather
 *  than let it become a false-reject downstream. Partial-period probes (mid-month
 *  start) are left alone. */
export function assertExclusiveEnd(period_start, period_end) {
  if (_isFirstOfMonth(period_start) && !_isFirstOfMonth(period_end)) {
    throw new Error(
      `period_end "${period_end}" is not first-of-month but period_start "${period_start}" is — ` +
      `this is the inclusive off-by-one (198 class). period_end is EXCLUSIVE: a full month ends on ` +
      `the FIRST of the next month. Use fullMonth()/fullMonthRange() to build the range.`
    );
  }
}

/** Build a FE-natural profit query string. Runs assertExclusiveEnd. Any extra
 *  keys are appended verbatim. */
export function periodQuery({ agency_id, client_id, period_start, period_end, ...extra }) {
  if (period_start && period_end) assertExclusiveEnd(period_start, period_end);
  const params = new URLSearchParams();
  if (agency_id) params.set('agency_id', agency_id);
  if (client_id) params.set('client_id', client_id);
  if (period_start) params.set('period_start', period_start);
  if (period_end) params.set('period_end', period_end);
  for (const [k, v] of Object.entries(extra)) if (v != null) params.set(k, String(v));
  return params.toString();
}

// ── read-only API probing (P2 read-only key) ─────────────────────────────────

/** A Playwright request context that authenticates EVERY call with rev's
 *  read-only Bearer key (P2). Prefers API_KEY_READONLY; falls back to API_KEY
 *  with a stderr warning so a missing read-only key is visible, not silent.
 *  Use this to GET any endpoint directly — no user login, no storageState. */
export async function readOnlyApiContext(cfg, env) {
  const key = env.API_KEY_READONLY || env[cfg.api_key_env] || env.API_KEY;
  if (!env.API_KEY_READONLY) {
    flushLine('[revprobe] WARN: API_KEY_READONLY not set — falling back to master API_KEY for reads');
  }
  if (!key) throw new Error('readOnlyApiContext: no API_KEY_READONLY or API_KEY in env');
  return pwRequest.newContext({
    baseURL: cfg.api_base || cfg.prod_base,
    extraHTTPHeaders: { Authorization: `Bearer ${key}`, Accept: 'application/json' },
  });
}

/** GET apiPath on a context and parse JSON, surfacing non-2xx loudly. */
export async function getJson(ctx, apiPath) {
  const resp = await ctx.get(apiPath);
  const status = resp.status();
  let body;
  try { body = await resp.json(); } catch { body = await resp.text(); }
  if (status >= 400) {
    return { ok: false, status, body };
  }
  return { ok: true, status, body };
}

// ── browser helpers ──────────────────────────────────────────────────────────

/** Launch a browser + context (optionally with a logged-in storageState) and
 *  return { browser, context, page }. Caller closes browser. */
export async function browserPage(cfg, { statePath, viewport = { width: 1440, height: 2200 } } = {}) {
  const browser = await launchBrowser();
  const ctxOpts = { viewport };
  if (statePath && fs.existsSync(statePath)) ctxOpts.storageState = statePath;
  const context = await browser.newContext(ctxOpts);
  const page = await context.newPage();
  return { browser, context, page };
}

/** wait-for-cards-rendered. Scoped overview KPI cards are blank for ~5–10s in
 *  headless; a fixed short sleep (4.5s) is a false negative. Poll until at least
 *  `minPopulated` matching cards carry non-skeleton content, up to `timeout` ms
 *  (default 15s — comfortably past the ~11s real render). Returns
 *  { rendered, count, waitedMs }. Never throws on timeout — returns rendered:false
 *  so the caller can record an honest "still blank after Ns" instead of a guess. */
export async function waitForCardsRendered(page, {
  // The profit-v2 app renders every KPI value in a `.tabular-nums` element (the
  // hero + scoped overview cards); testids are nav/header/hero, not "kpi"/"card"
  // (measured against the live qur_life overview, 2026-06-21). Default to the
  // number primitive plus the common testid hints so this works across surfaces.
  selector = '[class*="tabular-nums"], [data-testid*="hero"], [data-testid*="kpi"], [data-testid*="card"], [data-testid*="metric"]',
  minPopulated = 1,
  timeout = 15000,
  pollMs = 500,
} = {}) {
  const start = Date.now();
  // value/skeleton heuristic: a populated card has a digit or a currency/percent
  // glyph in its text and is NOT showing a loading/skeleton marker.
  const countPopulated = (sel) => {
    const nodes = Array.from(document.querySelectorAll(sel));
    let n = 0;
    for (const el of nodes) {
      const t = (el.innerText || '').trim();
      const loading = el.querySelector('[data-loading], .animate-pulse, [aria-busy="true"]');
      if (!loading && /[\d$%]/.test(t)) n += 1;
    }
    return n;
  };
  let count = 0;
  while (Date.now() - start < timeout) {
    count = await page.evaluate(countPopulated, selector);
    if (count >= minPopulated) return { rendered: true, count, waitedMs: Date.now() - start };
    await page.waitForTimeout(pollMs);
  }
  return { rendered: false, count, waitedMs: Date.now() - start };
}

/** apply-client-scope by product TITLE, not ASIN. The scope picker lists product
 *  titles; selecting by the ASIN string matches nothing and the surface reads as
 *  empty ("not ingested" false gate). Tries a <select>, then a combobox/listbox,
 *  then any clickable option whose visible text contains the title. Returns
 *  { applied, via } or { applied:false }. */
export async function applyClientScope(page, { productTitle, selector } = {}) {
  if (!productTitle) throw new Error('applyClientScope: productTitle required (select by TITLE, never ASIN)');
  // 1. native <select> whose options include the title
  const selects = selector ? [selector] : ['select[name*="scope"]', 'select[name*="product"]', 'select'];
  for (const s of selects) {
    const el = page.locator(s).first();
    if (await el.count().catch(() => 0)) {
      try {
        await el.selectOption({ label: new RegExp(escapeRe(productTitle), 'i') });
        return { applied: true, via: `select:${s}` };
      } catch { /* try label-contains via options */ }
      try {
        const opt = await el.locator('option', { hasText: productTitle }).first().getAttribute('value');
        if (opt != null) { await el.selectOption(opt); return { applied: true, via: `select-opt:${s}` }; }
      } catch { /* fall through */ }
    }
  }
  // 2. combobox / listbox pattern: open then click the matching option by text
  try {
    const trigger = page.locator('[role="combobox"], [aria-haspopup="listbox"], button[aria-expanded]').first();
    if (await trigger.count()) {
      await trigger.click();
      const option = page.getByRole('option', { name: new RegExp(escapeRe(productTitle), 'i') }).first();
      await option.click({ timeout: 4000 });
      return { applied: true, via: 'listbox' };
    }
  } catch { /* fall through */ }
  // 3. any clickable element whose visible text contains the title
  try {
    const cand = page.getByText(new RegExp(escapeRe(productTitle), 'i')).first();
    if (await cand.count()) { await cand.click({ timeout: 4000 }); return { applied: true, via: 'text-click' }; }
  } catch { /* fall through */ }
  return { applied: false };
}

function escapeRe(s) { return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&'); }

// ── output ───────────────────────────────────────────────────────────────────

/** Write one synchronously-flushed line. Many probes printing to a buffered
 *  stdout can hang the run (the 31-call buffered-hang); per-line flush avoids it.
 *  Objects are JSON-stringified. */
export function flushLine(obj) {
  process.stdout.write((typeof obj === 'string' ? obj : JSON.stringify(obj)) + '\n');
}

// Re-export the existing login helper so probes have one import surface.
export { acquire } from './auth.mjs';
export { loadConfig } from './config.mjs';
