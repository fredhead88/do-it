/**
 * rejectgate.mjs — blind second-opinion gate on REJECT verdicts (rev P5).
 *
 * False-reject insurance. rev nearly rejected spec 198 on a self-inflicted
 * period_end off-by-one. Before a REJECT lands, route it through a FRESH BLIND
 * refuter (codex primary, claude fallback — cross-vendor where possible) that
 * sees ONLY the criterion, rev's evidence, and rev's stated reason, and tries to
 * OVERTURN the rejection. A REJECT lands only if the blind refuter independently
 * UPHOLDS it; any disagreement or ambiguity HOLDS it for arbitration. This is a
 * two-key rule for the destructive direction (a wrong REJECT dings the build and
 * burns the orc's time), not for SATISFIED verdicts.
 *
 * The refuter is told the known false-reject classes so it checks them actively:
 *   - period_end off-by-one (full month ends on first-of-NEXT-month, exclusive)
 *   - scope selected by ASIN string instead of product TITLE → empty surface
 *   - scoped KPI cards blank for ~5-10s headless (too-short wait = false negative)
 *   - stale hardcoded $/unit literals in the check that have since drifted
 *   - a hand-picked non-FE-natural URL param producing a boundary artifact
 */

const OVERTURN_HINT =
  /\b(false[- ]?reject|actually (satisfied|present|correct|renders?)|off[- ]?by[- ]?one|exclusive|first of (the )?next month|selector|by title|too short|headless|stale (literal|value)|drift\w*)\b/i;
const UPHOLD_HINT =
  /\b(genuinely (missing|absent|broken)|truly (not|missing)|correctly rejected|reject (is|was) (right|correct|justified)|no data (is )?present)\b/i;

/** Build the blind-refutation prompt. The refuter sees only the criterion, the
 *  evidence, and rev's reason — never the code, author, or commit. */
export function buildRefutePrompt(criterion, evidenceText, rejectReason) {
  return [
    'You are an adversarial verification auditor. A reviewer wants to REJECT a',
    'criterion as NOT satisfied. Your job is to try to REFUTE that rejection —',
    'to find whether it is a FALSE reject. You see ONLY the criterion, the same',
    'evidence artifact the reviewer saw, and the reviewer\'s stated reason. No',
    'code, author, or commit context is provided or needed.',
    '',
    'Actively check the common false-reject causes before agreeing:',
    '  - period_end off-by-one: a full month ends on the FIRST of the NEXT month',
    '    (period_end is EXCLUSIVE); an inclusive month-end under-counts.',
    '  - scope selected by ASIN string instead of product TITLE → empty surface.',
    '  - scoped KPI cards are blank for ~5-10s headless; a too-short wait reads',
    '    as a false "no data".',
    '  - the check asserts a stale hardcoded $/unit literal that has since drifted',
    '    (data drift is not a regression).',
    '  - a hand-picked, non-FE-natural URL parameter producing a boundary artifact.',
    '',
    'Output rules: first line is EXACTLY one token — REJECT_UPHELD (the rejection',
    'is justified by the evidence) or REJECT_OVERTURNED (the rejection is not',
    'justified / looks like a false reject). Second line: one sentence why.',
    '',
    `CRITERION: ${criterion}`,
    '',
    `REVIEWER'S REASON FOR REJECTING: ${rejectReason}`,
    '',
    `EVIDENCE ARTIFACT: ${evidenceText}`,
  ].join('\n');
}

/** Parse a refutation. Returns { token, reason, unclear }. Mirrors judge.mjs's
 *  polarity-trap guard: a token whose justification argues the opposite is
 *  treated as unclear (which fails safe to HOLD in gateReject). */
export function parseRefutation(raw) {
  const text = String(raw || '').trim();
  const m = text.match(/\b(REJECT_UPHELD|REJECT_OVERTURNED)\b/);
  const token = m ? m[1] : null;
  const reason = text.split('\n').slice(1).join(' ').trim() || text;
  let unclear = !token;
  // polarity traps: token says one thing, prose argues the other
  if (token === 'REJECT_UPHELD' && OVERTURN_HINT.test(reason) && !UPHOLD_HINT.test(reason)) unclear = true;
  if (token === 'REJECT_OVERTURNED' && UPHOLD_HINT.test(reason) && !OVERTURN_HINT.test(reason)) unclear = true;
  return { token, reason, unclear };
}

/** Decide whether a REJECT may land, given a blind refutation.
 *  land  -> blind refuter UPHELD the rejection (two independent keys agree).
 *  hold  -> refuter OVERTURNED or was unclear (possible false reject; arbitrate).
 *  Fails safe to HOLD on any ambiguity. */
export function gateReject(refutation) {
  if (refutation.token === 'REJECT_UPHELD' && !refutation.unclear) {
    return { decision: 'land', reason: 'blind refuter independently upheld the rejection' };
  }
  if (refutation.token === 'REJECT_OVERTURNED' && !refutation.unclear) {
    return { decision: 'hold', reason: 'blind refuter OVERTURNED the rejection — possible false reject; arbitrate before it lands' };
  }
  return { decision: 'hold', reason: 'blind refutation unclear — holding for arbitration (fail-safe)' };
}

/** Run the blind refuter (codex primary, claude fallback). Returns
 *  { token, reason, unclear, refuter }. */
export async function refuteRejection(criterion, evidenceText, rejectReason, { runCodex, runClaude }) {
  const prompt = buildRefutePrompt(criterion, evidenceText, rejectReason);
  let raw = null, who = 'codex';
  try {
    raw = await runCodex(prompt);
    if (!String(raw || '').trim()) throw new Error('empty');
  } catch {
    who = 'claude-fallback';
    raw = await runClaude(prompt);
  }
  return { ...parseRefutation(raw), refuter: who };
}

/** End-to-end gate: refute, then decide. Returns
 *  { decision, reason, refutation }. A caller should only let a REJECT verdict
 *  land when decision === 'land'; otherwise record a HOLD for arbitration. */
export async function gateRejectVerdict(criterion, evidenceText, rejectReason, runners) {
  const refutation = await refuteRejection(criterion, evidenceText, rejectReason, runners);
  const { decision, reason } = gateReject(refutation);
  return { decision, reason, refutation };
}
