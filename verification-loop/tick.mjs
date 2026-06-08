#!/usr/bin/env node
/**
 * tick.mjs — the 8-step verification loop body
 *
 * Usage:
 *   node tick.mjs [options]
 *
 * Options:
 *   --config <name>       config name (default: example)
 *   --spec <id>           limit to one spec id (can repeat); default = all shipped
 *   --criterion <text>    limit to a single criterion text (smoke-test mode)
 *   --dry-run             observe + judge but do NOT write verdicts or call spec_ledger
 *   --force               ignore idle-sha check (always run, even if no new ship)
 *
 * Config keys used (read from <config>.json + env overrides):
 *   repo_root       Path to the git repo (used for sha detection). Env: VLOOP_REPO_ROOT
 *   spec_ledger_py  Path to spec_ledger.py. Env: SPEC_LEDGER_PY
 *   ledger_base     Path to the bus ledger dir (~/.claude/ledger). Env: LEDGER_BASE
 *   python_bin      Python interpreter path. Env: VLOOP_PYTHON_BIN
 *   version_file    Path to .version.json written by deploy.sh. Env: VLOOP_VERSION_FILE
 *
 * Credentials (set in your project's .env and sourced before running):
 *   VERIFIER_USER / VERIFIER_PASS   test-user login creds (names overridden by config's
 *                                   auth.user_env / auth.pass_env)
 *   API_KEY                         backend Bearer token (name overridden by config's
 *                                   api_key_env)
 *
 * The tick is designed to be called by cron every 30 minutes.
 * See SETUP.md for the exact cron line.
 */

import { execSync, spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { parseArgs } from 'node:util';

import yaml from 'js-yaml';

import { loadConfig, runDir, ROOT } from './lib/config.mjs';
import { launchBrowser } from './lib/browser.mjs';
import { criteriaFromCard, validateCriterion } from './lib/cardschema.mjs';
import { runDomAssertion } from './lib/assert-dom.mjs';
import { tooFreshToVerify, shippedAtFromRecord } from './lib/freshness.mjs';
import { selfcheck } from './lib/selfcheck.mjs';
import { acquire } from './lib/auth.mjs';
import { probe } from './lib/probe.mjs';
import { shoot } from './lib/shoot.mjs';
import { callApi } from './lib/api.mjs';
import { selectObservationLayer } from './lib/routing.mjs';
import { judge } from './lib/judge.mjs';
import { runCodex, runClaude } from './lib/judge-runners.mjs';
import { runIpt } from './lib/ipt.mjs';
import {
  appendProgress,
  escalate,
  recordVerdict,
  pinSpecSha,
  detectScopeReduction,
} from './lib/state.mjs';

// ── CLI ────────────────────────────────────────────────────────────────────────

const { values: cli } = parseArgs({
  options: {
    config:    { type: 'string',  default: 'example' },
    spec:      { type: 'string',  multiple: true, default: [] },
    criterion: { type: 'string',  default: '' },
    'dry-run': { type: 'boolean', default: false },
    force:     { type: 'boolean', default: false },
  },
  strict: false,
});

const DRY_RUN  = cli['dry-run'];
const FORCE    = cli['force'];
const specFilter   = cli.spec;           // [] = all
const critFilter   = cli.criterion;      // '' = all

// ── constants (resolved from config + env overrides after config load) ────────
// These are set in the tick() function after config is loaded.
let LEDGER_PY;
let LEDGER_BASE;
let REPO_ROOT_CFG;
let PYTHON_BIN;
let VERSION_FILE;

const TRIAL_BUDGET = 3;   // max corrective attempts per criterion

// ── helpers ───────────────────────────────────────────────────────────────────

function log(msg) {
  console.log(`[tick ${new Date().toISOString()}] ${msg}`);
}

/** Read PROGRESS.jsonl and return the last deployed sha recorded. */
function lastRecordedSha(dir) {
  const f = path.join(dir, 'PROGRESS.jsonl');
  if (!fs.existsSync(f)) return null;
  const lines = fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean);
  for (let i = lines.length - 1; i >= 0; i--) {
    try {
      const obj = JSON.parse(lines[i]);
      if (obj.deployed_sha) return obj.deployed_sha;
    } catch { /* skip malformed lines */ }
  }
  return null;
}

/** Get the current deployed sha.
 *  Order: local API /version endpoint → .version.json on disk → git HEAD.
 */
function currentDeployedSha(cfg) {
  // 1. Try the API /version endpoint (if api_base is configured)
  if (cfg.api_base) {
    try {
      const body = execSync(`curl -s ${cfg.api_base.replace(/\/$/, '')}/version`, { timeout: 5000 }).toString();
      const j = JSON.parse(body);
      if (j.sha && j.sha !== 'unknown') return j.sha;
    } catch { /* fallthrough */ }
  }

  // 2. Try .version.json on disk (written by deploy.sh)
  if (VERSION_FILE && fs.existsSync(VERSION_FILE)) {
    try {
      const j = JSON.parse(fs.readFileSync(VERSION_FILE, 'utf8'));
      if (j.sha && j.sha !== 'unknown') return j.sha;
    } catch { /* fallthrough */ }
  }

  // 3. Fall back to git HEAD (good enough for idle-detection)
  if (REPO_ROOT_CFG) {
    try {
      return execSync(`git -C ${REPO_ROOT_CFG} rev-parse --short HEAD`, { timeout: 3000 })
        .toString().trim();
    } catch { /* fallthrough */ }
  }

  return 'unknown';
}

/** Parse shipped specs from the ledger directory. Returns array of records. */
function loadShippedSpecs(filterIds = []) {
  if (!fs.existsSync(LEDGER_BASE)) return [];
  const files = fs.readdirSync(LEDGER_BASE)
    .filter(f => f.endsWith('.yml') && !f.startsWith('.'));

  const records = [];
  for (const f of files) {
    try {
      const text = fs.readFileSync(path.join(LEDGER_BASE, f), 'utf8');
      // Minimal YAML parse: just extract status, spec_id, spec_file
      const statusM = text.match(/^status:\s*(.+)$/m);
      const idM     = text.match(/^spec_id:\s*(.+)$/m);
      const fileM   = text.match(/^spec_file:\s*(.+)$/m);
      const shaM    = text.match(/^shipped_sha:\s*(.+)$/m);
      const cardM   = text.match(/^review_card:\s*(.+)$/m);
      if (!statusM || !idM) continue;
      const status  = statusM[1].trim();
      if (!['shipped', 'accepted'].includes(status)) continue;
      const spec_id   = idM[1].trim();
      if (filterIds.length && !filterIds.includes(spec_id)) continue;
      let shipped_at = null;
      try {
        const doc = yaml.load(text);
        const hist = (doc && Array.isArray(doc.history)) ? doc.history : [];
        for (let i = hist.length - 1; i >= 0; i--) {
          if (hist[i] && hist[i].status === 'shipped') { shipped_at = hist[i].at || null; break; }
        }
      } catch { /* leave null — do not block verification */ }
      records.push({
        spec_id,
        spec_file: fileM ? fileM[1].trim() : null,
        shipped_sha: shaM ? shaM[1].trim() : null,
        review_card: cardM ? cardM[1].trim() : null,
        shipped_at,
        status,
      });
    } catch { /* skip */ }
  }
  return records;
}

/** Load acceptance criteria from a spec file.
 *  Looks for lines starting with "- Acceptance" or "## R" blocks.
 *  Returns array of { id, text, type }.
 */
function loadCriteria(specFile, specId, reviewCard) {
  // Prefer the machine-readable criteria block in the review card (authored by orc).
  if (reviewCard) {
    const cardPath = path.isAbsolute(reviewCard)
      ? reviewCard
      : path.join(process.env.HOME, '.claude', 'brief-inbox', reviewCard);
    if (fs.existsSync(cardPath)) {
      try {
        const { criteria, errors } = criteriaFromCard(fs.readFileSync(cardPath, 'utf8'));
        if (criteria) {
          if (errors.length) log(`  card schema errors for ${specId}: ${errors.join('; ')}`);
          return criteria.map((c) => ({ ...c, schema_error: validateCriterion(c) || null }));
        }
      } catch (e) {
        log(`  card YAML parse error for ${specId}: ${e.message} — falling back to prose`);
      }
    }
  }
  // specFile may be relative to repo root
  let filePath = specFile;
  if (specFile && !path.isAbsolute(specFile) && REPO_ROOT_CFG) {
    filePath = path.join(REPO_ROOT_CFG, specFile);
  }
  if (!filePath || !fs.existsSync(filePath)) {
    // Synthesise a single presence-check criterion from the spec_id title
    return [{
      id: 'c1',
      text: `The overview page loads without errors and shows data for this spec (${specId})`,
      type: 'non_null_numeric',
    }];
  }

  const text = fs.readFileSync(filePath, 'utf8');
  const criteria = [];
  let idx = 0;

  // Pattern 1: "- Acceptance (observable): ..." lines
  const acceptRe = /^-\s+Acceptance\s*(?:\([^)]*\))?:\s*(.+?)(?=\n|$)/gm;
  let m;
  while ((m = acceptRe.exec(text)) !== null) {
    idx++;
    const ctext = m[1].trim();
    criteria.push({
      id: `c${idx}`,
      text: ctext,
      type: guessType(ctext),
    });
  }

  // Pattern 2: "Acceptance criteria:" / "## Acceptance" / "**Acceptance criteria:**" sections
  if (criteria.length === 0) {
    // Match both plain "Acceptance criteria:" and bold "**Acceptance criteria:**" (with optional trailing spaces)
    const sectionRe = /(?:##\s*Acceptance[^\n]*\n|\*{0,2}Acceptance criteria?:\*{0,2}\s*\n)((?:[ \t]*[-*]\s*.+\n?)+)/gi;
    while ((m = sectionRe.exec(text)) !== null) {
      const block = m[1];
      const lineRe = /^[ \t]*[-*]\s+(.+?)(?:\s*\*{0,2})?$/gm;
      let lm;
      while ((lm = lineRe.exec(block)) !== null) {
        idx++;
        const ctext = lm[1].trim().replace(/^\*{1,2}|\*{1,2}$/g, '');
        criteria.push({ id: `c${idx}`, text: ctext, type: guessType(ctext) });
      }
    }
  }

  if (criteria.length === 0) {
    // Fallback: single generic criterion
    criteria.push({
      id: 'c1',
      text: `The overview page loads without errors (spec ${specId})`,
      type: 'non_null_numeric',
    });
  }

  return criteria;
}

function guessType(text) {
  const t = text.toLowerCase();
  if (/\$([\d,]+)|\d+%|\d+ unit|\bcount\b|\bvalue\b|\bnumber\b/.test(t)) return 'non_null_numeric';
  if (/renders|visible|chart|bar|graph|display/.test(t)) return 'visible_rendered';
  if (/click|hover|select|toggle|changes|navigat/.test(t)) return 'state_changes';
  if (/api|backend|endpoint|200|returns/.test(t)) return 'reconciles_to_db';
  return 'visible_rendered';
}

/** Count prior corrective attempts for a criterion ACROSS ALL runs (lifetime). */
function priorAttemptCount(_dir, specId, criterionId) {
  const runsDir = path.join(ROOT, 'runs');
  if (!fs.existsSync(runsDir)) return 0;
  const runDates = fs.readdirSync(runsDir).filter(d => /^\d{4}-\d{2}-\d{2}$/.test(d));
  let total = 0;
  for (const date of runDates) {
    const f = path.join(runsDir, date, 'VERIFICATION-LEDGER.jsonl');
    if (!fs.existsSync(f)) continue;
    const lines = fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean);
    total += lines.filter(l => {
      try {
        const o = JSON.parse(l);
        return o.spec === specId && o.criterionId === criterionId &&
               ['HOLLOW', 'MISSING', 'REGRESSION'].includes(o.verdict);
      } catch { return false; }
    }).length;
  }
  return total;
}

/** Check if a criterion already has a CONFIRMED verdict in this run. */
function alreadyConfirmed(dir, specId, criterionId) {
  const f = path.join(dir, 'VERIFICATION-LEDGER.jsonl');
  if (!fs.existsSync(f)) return false;
  const lines = fs.readFileSync(f, 'utf8').trim().split('\n').filter(Boolean);
  return lines.some(l => {
    try {
      const o = JSON.parse(l);
      return o.spec === specId && o.criterionId === criterionId && o.verdict === 'CONFIRMED';
    } catch { return false; }
  });
}

/** Call spec_ledger.py verify subcommand. No-op in dry-run mode. */
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

/** Observe a single criterion and return { verdict, evidenceRef, judgeResult }. */
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
    const browser = await launchBrowser();
    let res;
    try {
      const ctx = await browser.newContext({ storageState: statePath });
      const page = await ctx.newPage();
      const consoleErrors = [];
      page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
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

  const layer = selectObservationLayer(criterion.text);
  const evidenceName = `${criterion.id}-${periodLabel}`;

  // ── DOM layer ────────────────────────────────────────────────────────────────
  if (layer === 'DOM_INTERACTION') {
    // No real interaction driver exists yet — fall through to DOM-only observation.
    const noteFile = path.join(dir, 'PROGRESS.jsonl');
    const note = JSON.stringify({
      at: new Date().toISOString(),
      event: 'DOM_INTERACTION_DOWNGRADED',
      criterion: criterion.text,
      note: 'DOM_INTERACTION criteria observed as DOM-only (interaction driver not yet implemented)',
    }) + '\n';
    fs.appendFileSync(noteFile, note);
  }
  if (layer === 'DOM' || layer === 'DOM_INTERACTION') {
    const snapFile = path.join(dir, `snap-overview.txt`);
    const textFile = path.join(dir, `text-overview.txt`);

    let evidenceText = '';
    if (fs.existsSync(snapFile)) {
      evidenceText = fs.readFileSync(snapFile, 'utf8').slice(0, 6000);
    } else if (fs.existsSync(textFile)) {
      evidenceText = fs.readFileSync(textFile, 'utf8').slice(0, 4000);
    } else {
      try {
        const url = cfg.prod_base + cfg.page_map.overview;
        const res = await shoot({ url, out: path.join(dir, `shot-crit-${evidenceName}.png`), statePath });
        evidenceText = fs.existsSync(res.textFile)
          ? fs.readFileSync(res.textFile, 'utf8').slice(0, 4000)
          : `[shoot returned status ${res.status}]`;
      } catch (e) {
        evidenceText = `[shoot error: ${e.message}]`;
      }
    }

    const evidenceFile = path.join(dir, 'evidence', `${evidenceName}.json`);
    fs.mkdirSync(path.join(dir, 'evidence'), { recursive: true });
    const record = {
      criterion: criterion.text,
      layer,
      at: new Date().toISOString(),
      source: snapFile,
      excerpt: evidenceText.slice(0, 1500),
    };
    fs.writeFileSync(evidenceFile, JSON.stringify(record, null, 2));

    const result = await judge(criterion.text, evidenceText, { runCodex, runClaude });
    return { layer, evidenceRef: evidenceFile, judgeResult: result };
  }

  // ── VISION layer ────────────────────────────────────────────────────────────
  if (layer === 'VISION') {
    const url = cfg.prod_base + cfg.page_map.overview;
    const shotOut = path.join(dir, `shot-crit-${evidenceName}.png`);
    let screenshotB64 = '';
    let status = null;
    try {
      const res = await shoot({ url, out: shotOut, statePath });
      status = res.status;
      if (fs.existsSync(shotOut)) {
        screenshotB64 = `[screenshot at ${shotOut}]`;
      }
    } catch (e) {
      screenshotB64 = `[screenshot error: ${e.message}]`;
    }

    const evidenceText = `Screenshot of ${url} captured at ${new Date().toISOString()}.
HTTP status: ${status}.
Screenshot saved to: ${shotOut}
Is the chart/visual element present and visible? Answer based on ONLY this screenshot.`;

    const evidenceFile = path.join(dir, 'evidence', `${evidenceName}.json`);
    fs.mkdirSync(path.join(dir, 'evidence'), { recursive: true });
    fs.writeFileSync(evidenceFile, JSON.stringify({
      criterion: criterion.text,
      layer: 'VISION',
      at: new Date().toISOString(),
      screenshot: shotOut,
      status,
      evidence_text: evidenceText,
    }, null, 2));

    const result = await judge(criterion.text, evidenceText, { runCodex, runClaude });
    return { layer, evidenceRef: evidenceFile, judgeResult: result };
  }

  // Fallback
  return {
    layer: 'UNKNOWN',
    evidenceRef: null,
    judgeResult: { token: null, reason: 'unknown layer', judge: 'none', unclear: true },
  };
}

/** Map a judge result + IPT result to a ledger verdict. */
function assignVerdict(judgeResult, iptResult) {
  if (judgeResult.unclear) return 'UNCLEAR';
  if (judgeResult.token === 'SATISFIED') {
    if (iptResult && iptResult.verdict === 'SUSPECTED-GAMING') return 'SUSPECTED-GAMING';
    if (iptResult && iptResult.verdict === 'CONFIRMED') return 'CONFIRMED';
    return 'CONFIRMED';
  }
  if (judgeResult.token === 'NOT_SATISFIED') {
    return 'HOLLOW';
  }
  return 'NOT-RUN';
}

// ── MAIN TICK ─────────────────────────────────────────────────────────────────

async function tick() {
  const cfg  = loadConfig(cli.config);
  const dir  = runDir();   // runs/<today>/

  // Resolve config-driven constants (with env overrides)
  REPO_ROOT_CFG = process.env.VLOOP_REPO_ROOT || cfg.repo_root || null;
  LEDGER_PY     = process.env.SPEC_LEDGER_PY  || cfg.spec_ledger_py || 'scripts/spec_ledger.py';
  LEDGER_BASE   = process.env.LEDGER_BASE      || cfg.ledger_base   || (process.env.HOME + '/.claude/ledger');
  PYTHON_BIN    = process.env.VLOOP_PYTHON_BIN || cfg.python_bin    || 'python3';
  VERSION_FILE  = process.env.VLOOP_VERSION_FILE || cfg.version_file || null;

  log(`tick start — config: ${cli.config}, dir: ${dir}, dry-run: ${DRY_RUN}`);

  // ── Step 1: Detect new ship ──────────────────────────────────────────────────
  const currentSha = currentDeployedSha(cfg);
  const prevSha    = lastRecordedSha(dir);

  if (!FORCE && currentSha !== 'unknown' && currentSha === prevSha) {
    log(`idle — sha ${currentSha} unchanged since last tick; nothing to do`);
    appendProgress(dir, { event: 'idle', deployed_sha: currentSha, reason: 'sha_unchanged' });
    return;
  }

  log(`new ship detected: prev=${prevSha || 'none'} → current=${currentSha}`);

  // ── Step 2: selfcheck ────────────────────────────────────────────────────────
  const check = selfcheck(cfg, process.env, fs);
  if (!check.ok) {
    const msg = `selfcheck failed: ${check.failures.join('; ')}`;
    log(`HALT: ${msg}`);
    escalate(dir, { event: 'SELFCHECK_FAIL', failures: check.failures, deployed_sha: currentSha });
    appendProgress(dir, { event: 'halted', reason: 'selfcheck_fail', deployed_sha: currentSha });
    process.exit(1);
  }
  log('selfcheck passed');

  // ── Step 3: Auth + load specs + pin sha ──────────────────────────────────────
  const statePath = await acquire(cfg, process.env, dir);
  log(`auth: state at ${statePath}`);

  const specs = loadShippedSpecs(specFilter);
  log(`specs to verify: ${specs.length} (filter=${specFilter.join(',') || 'all'})`);

  if (specs.length === 0) {
    log('no shipped specs found — idle');
    appendProgress(dir, { event: 'idle', deployed_sha: currentSha, reason: 'no_shipped_specs' });
    return;
  }

  // ── Step 4: Probe the prod surfaces ─────────────────────────────────────────
  log('probing prod pages...');
  let probeResults = {};
  try {
    probeResults = await probe(cfg, statePath, dir);
    for (const [name, r] of Object.entries(probeResults)) {
      if (r.deployInProgress) {
        log(`DEPLOY_IN_PROGRESS on ${name} — will not cry P0; page artifacts may be stale`);
      }
    }
  } catch (e) {
    log(`probe error: ${e.message} — continuing with existing artifacts`);
  }

  // ── I1: Escalation expiry ────────────────────────────────────────────────────
  // Read NEEDS-HUMAN.jsonl; for any unresolved escalation >2 ticks old, emit a
  // human-notification entry instead of re-filing correctives for that criterion.
  const escalationFile = path.join(dir, 'NEEDS-HUMAN.jsonl');
  const progressFile   = path.join(dir, 'PROGRESS.jsonl');
  const escalatedSet   = new Set();  // "specId:criterionId" keys currently in escalated state

  {
    const ticksDone = fs.existsSync(progressFile)
      ? fs.readFileSync(progressFile, 'utf8').trim().split('\n').filter(Boolean).filter(l => {
          try { return JSON.parse(l).event === 'tick_complete'; } catch { return false; }
        }).length
      : 0;

    if (fs.existsSync(escalationFile)) {
      const escLines = fs.readFileSync(escalationFile, 'utf8').trim().split('\n').filter(Boolean);
      for (const line of escLines) {
        let e;
        try { e = JSON.parse(line); } catch { continue; }
        if (!e.spec || !e.criterionId) continue;
        const key = `${e.spec}:${e.criterionId}`;
        const escalatedAtTick = typeof e.tick === 'number' ? e.tick : 0;
        const ticksElapsed = ticksDone - escalatedAtTick;
        if (ticksElapsed > 2) {
          if (!escalatedSet.has(key)) {
            escalatedSet.add(key);
            escalate(dir, {
              event: 'UNRESOLVED_ESCALATION',
              reason: 'unresolved_escalation',
              spec: e.spec,
              criterionId: e.criterionId,
              criterion: e.criterion,
              ticks_unresolved: ticksElapsed,
              deployed_sha: currentSha,
              note: 'This item has been escalated for >2 ticks with no resolution. Human review required.',
            });
          }
        }
      }
    }
  }

  // ── Per-spec, per-criterion loop ──────────────────────────────────────────────
  for (const spec of specs) {
    if (tooFreshToVerify(spec.shipped_at, 10)) {
      log(`  skip ${spec.spec_id} — shipped <10min ago, letting the deploy go live`);
      continue;
    }

    const criteria = loadCriteria(spec.spec_file, spec.spec_id, spec.review_card);

    const pinnedSha = spec.shipped_sha || currentSha;
    pinSpecSha(dir, spec.spec_id, pinnedSha, criteria.map(c => c.id));

    let activeCriteria = criteria;
    if (critFilter) {
      activeCriteria = criteria.filter(c =>
        c.text.toLowerCase().includes(critFilter.toLowerCase())
      );
      if (activeCriteria.length === 0) {
        log(`no criteria matched filter "${critFilter}" for ${spec.spec_id}`);
        continue;
      }
    }

    log(`verifying spec ${spec.spec_id} — ${activeCriteria.length} criterion(a)`);

    for (const criterion of activeCriteria) {
      if (alreadyConfirmed(dir, spec.spec_id, criterion.id)) {
        log(`  skip ${criterion.id} — already CONFIRMED this run`);
        continue;
      }

      if (escalatedSet.has(`${spec.spec_id}:${criterion.id}`)) {
        log(`  skip ${criterion.id} — unresolved escalation; human review required`);
        continue;
      }

      const attempts = priorAttemptCount(dir, spec.spec_id, criterion.id);
      if (attempts >= TRIAL_BUDGET) {
        log(`  BOUNCE ${criterion.id} — trial budget exhausted (${attempts} attempts)`);
        escalate(dir, {
          event: 'TRIAL_BUDGET_EXHAUSTED',
          spec: spec.spec_id,
          criterionId: criterion.id,
          criterion: criterion.text,
          attempts,
          deployed_sha: currentSha,
        });
        if (!DRY_RUN) {
          recordVerdict(dir, spec.spec_id, criterion.id, 'BOUNCED', 'trial-budget-exhausted');
        }
        continue;
      }

      log(`  checking ${criterion.id}: "${criterion.text.slice(0, 80)}..."`);

      // ── Steps 4–5: observe + judge — loop over all verify_periods ─────────
      const periods = (cfg.verify_periods && cfg.verify_periods.length)
        ? cfg.verify_periods
        : ['primary'];

      let overallVerdict = 'CONFIRMED';
      let lastFinalJudgeResult = null;
      let lastEvidenceRef = null;
      let fatalError = false;

      for (const periodLabel of periods) {
        let observeResult;
        try {
          observeResult = await observeCriterion(criterion, cfg, statePath, dir, periodLabel);
        } catch (e) {
          log(`  observation error [${periodLabel}]: ${e.message}`);
          escalate(dir, {
            event: 'OBSERVATION_ERROR',
            spec: spec.spec_id,
            criterionId: criterion.id,
            period: periodLabel,
            error: e.message,
            deployed_sha: currentSha,
          });
          if (!DRY_RUN) {
            recordVerdict(dir, spec.spec_id, criterion.id, 'NOT-RUN', `error: ${e.message}`);
          }
          fatalError = true;
          break;
        }

        const { judgeResult, evidenceRef } = observeResult;
        lastEvidenceRef = evidenceRef;

        // Handle UNCLEAR — re-judge once
        let finalJudgeResult = judgeResult;
        if (judgeResult.unclear) {
          log(`  UNCLEAR token/reason [${periodLabel}] — re-judging once`);
          try {
            finalJudgeResult = await judge(criterion.text,
              fs.existsSync(evidenceRef) ? (() => { const rec = JSON.parse(fs.readFileSync(evidenceRef, 'utf8')); return rec.excerpt || rec.evidence_text || ''; })() : '',
              { runCodex, runClaude }
            );
          } catch (e) {
            log(`  re-judge error: ${e.message}`);
          }
          if (finalJudgeResult.unclear) {
            log(`  still UNCLEAR after re-judge [${periodLabel}] — escalating`);
            escalate(dir, {
              event: 'UNCLEAR_VERDICT',
              spec: spec.spec_id,
              criterionId: criterion.id,
              period: periodLabel,
              criterion: criterion.text,
              judgeResult: finalJudgeResult,
              deployed_sha: currentSha,
            });
            if (!DRY_RUN) {
              recordVerdict(dir, spec.spec_id, criterion.id, 'NOT-RUN', evidenceRef || 'unclear');
            }
            appendProgress(dir, {
              event: 'criterion_checked',
              spec: spec.spec_id,
              criterionId: criterion.id,
              period: periodLabel,
              verdict: 'NOT-RUN',
              reason: 'unclear',
              deployed_sha: currentSha,
            });
            fatalError = true;
            break;
          }
        }

        lastFinalJudgeResult = finalJudgeResult;

        // ── IPT ─────────────────────────────────────────────────────────────
        let iptResult = null;
        const priorVerdict = attempts > 0 ? 'HOLLOW' : null;
        const evidence = {
          priorVerdict,
          original: { value: finalJudgeResult.token === 'SATISFIED' ? 1 : null },
        };
        try {
          iptResult = await runIpt(
            { text: criterion.text, type: criterion.type },
            evidence,
            {
              altMemberExists: async () => false,
              observeAlt: async () => ({}),
            }
          );
        } catch (e) {
          log(`  IPT error (non-fatal): ${e.message}`);
        }

        // ── per-period verdict ───────────────────────────────────────────────
        const periodVerdict = assignVerdict(finalJudgeResult, iptResult);
        log(`  [${periodLabel}] verdict: ${periodVerdict} (judge: ${finalJudgeResult.judge})`);

        appendProgress(dir, {
          event: 'criterion_checked',
          spec: spec.spec_id,
          criterionId: criterion.id,
          period: periodLabel,
          verdict: periodVerdict,
          judge: finalJudgeResult.judge,
          reason: finalJudgeResult.reason.slice(0, 200),
          evidenceRef,
          deployed_sha: currentSha,
        });

        if (periodVerdict !== 'CONFIRMED') {
          overallVerdict = periodVerdict;
        }
      }  // end period loop

      if (fatalError) continue;

      const finalJudgeResult = lastFinalJudgeResult;
      const evidenceRef = lastEvidenceRef;
      const verdict = overallVerdict;

      if (!DRY_RUN) {
        recordVerdict(dir, spec.spec_id, criterion.id, verdict, evidenceRef || 'none');
      }

      // ── Step 6: resolve ──────────────────────────────────────────────────────
      if (verdict === 'CONFIRMED') {
        log(`  CONFIRMED`);
        await callSpecLedgerVerify(spec.spec_id, criterion.id, 'CONFIRMED', finalJudgeResult.judge, evidenceRef || 'none');

      } else if (verdict === 'HOLLOW' || verdict === 'MISSING' || verdict === 'REGRESSION') {
        log(`  ${verdict} — writing REJECTED + filing corrective (attempt ${attempts + 1}/${TRIAL_BUDGET})`);
        await callSpecLedgerVerify(spec.spec_id, criterion.id, 'REJECTED', finalJudgeResult.judge, evidenceRef || 'none');
        escalate(dir, {
          event: 'CORRECTIVE_NEEDED',
          spec: spec.spec_id,
          criterionId: criterion.id,
          criterion: criterion.text,
          verdict,
          judge: finalJudgeResult.judge,
          reason: finalJudgeResult.reason,
          attempts: attempts + 1,
          deployed_sha: currentSha,
          note: `File a corrective spec with observable criteria targeting: ${criterion.text}`,
        });

      } else if (verdict === 'DATA-GAP') {
        await callSpecLedgerVerify(spec.spec_id, criterion.id, 'not-applicable', finalJudgeResult.judge, evidenceRef || 'none');
        escalate(dir, {
          event: 'OPS_NOTE',
          spec: spec.spec_id,
          criterionId: criterion.id,
          criterion: criterion.text,
          verdict,
          deployed_sha: currentSha,
        });

      } else if (verdict === 'NOT-RUN') {
        escalate(dir, {
          event: 'OPS_NOTE',
          spec: spec.spec_id,
          criterionId: criterion.id,
          criterion: criterion.text,
          verdict,
          deployed_sha: currentSha,
        });

      } else if (verdict === 'SUSPECTED-GAMING') {
        // intentional: no spec_ledger verdict — a human must adjudicate a gaming claim
        escalate(dir, {
          event: 'SUSPECTED_GAMING',
          spec: spec.spec_id,
          criterionId: criterion.id,
          criterion: criterion.text,
          deployed_sha: currentSha,
          note: 'Metamorphic relation failed — possible builder gaming. Human review required.',
        });

      } else if (verdict === 'TASTE') {
        escalate(dir, {
          event: 'TASTE_ESCALATION',
          spec: spec.spec_id,
          criterionId: criterion.id,
          criterion: criterion.text,
          deployed_sha: currentSha,
        });
      } else {
        log(`  unhandled verdict ${verdict} for ${spec.spec_id}:${criterion.id} — escalating`);
        escalate(dir, { event: 'UNHANDLED_VERDICT', spec: spec.spec_id, criterionId: criterion.id, verdict });
      }
    }  // end criterion loop

    // ── Step 8: detect scope reduction ────────────────────────────────────────
    const reduced = detectScopeReduction(dir, spec.spec_id, criteria.map(c => c.id));
    if (reduced.length > 0) {
      log(`  SCOPE_REDUCTION in ${spec.spec_id}: ${reduced.join(', ')}`);
      escalate(dir, {
        event: 'SCOPE_REDUCTION',
        spec: spec.spec_id,
        missing_criteria: reduced,
        deployed_sha: currentSha,
        note: 'Criteria pinned at handover are now absent with no evidence. Possible silent descope.',
      });
    }
  }  // end spec loop

  // ── Step 7: re-probe transient check ──────────────────────────────────────
  for (const [name, r] of Object.entries(probeResults)) {
    if (r.deployInProgress) {
      appendProgress(dir, {
        event: 'DEPLOY_IN_PROGRESS',
        page: name,
        note: 'Got 502/503 on initial probe; retried once. Not a P0.',
        deployed_sha: currentSha,
      });
    }
  }

  // ── Step 8: appendProgress tick-end ────────────────────────────────────────
  appendProgress(dir, {
    event: 'tick_complete',
    deployed_sha: currentSha,
    specs_checked: specs.map(s => s.spec_id),
    dry_run: DRY_RUN,
  });

  log('tick complete');
}

// ── Run ────────────────────────────────────────────────────────────────────────
tick().catch(e => {
  console.error(`[tick FATAL] ${e.stack || e.message}`);
  process.exit(1);
});
