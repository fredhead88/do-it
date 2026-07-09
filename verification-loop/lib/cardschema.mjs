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

/** A criterion that failed schema validation cannot be confirmed — it is REJECTED
 *  (fail closed). Returns 'REJECTED' for a non-empty error, else null. */
export function verdictForSchemaError(err) {
  return err ? 'REJECTED' : null;
}

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

// ── Capture Provenance (R-D2 — per-criterion surface binding) ─────────────────
//
// Each evidence artifact written by the verifier must carry a `captured_surface`
// (the page-map key used when capturing). The check below compares that recorded
// surface to the criterion's declared surface (dom_assertion.page for ui criteria,
// or criterion.surface for non-ui criteria).
//
// A mismatch means the artifact came from the WRONG page — the F4 scenario where
// criteria 124/125/128 were judged against a neighbor spec's snapshot. Any such
// mismatch fails the criterion and must never silently pass.

/**
 * Derive the declared surface for a criterion.
 * For ui criteria the authoritative source is dom_assertion.page.
 * For non-ui criteria, fall back to criterion.surface if present.
 * Returns null if the criterion makes no surface declaration.
 *
 * @param {object} criterion
 * @returns {string|null}
 */
export function declaredSurface(criterion) {
  if (criterion.criterion_type === 'ui' && criterion.dom_assertion && criterion.dom_assertion.page) {
    return criterion.dom_assertion.page;
  }
  return criterion.surface || null;
}

/**
 * Check capture provenance: verify that an evidence artifact was captured from
 * the criterion's own surface, not a neighbor's.
 *
 * @param {object} criterion   - the criterion being judged
 * @param {object} artifact    - the parsed evidence JSON (must carry `captured_surface`)
 * @returns {{ mismatch: boolean, declared: string|null, captured: string|null, reason: string }}
 *
 * A missing `captured_surface` in the artifact is treated as UNKNOWN (not a fail),
 * because legacy artifacts predate this field. Only an explicit mismatch between two
 * known values is flagged.
 */
export function checkCaptureProvenance(criterion, artifact) {
  const declared = declaredSurface(criterion);
  const captured = (artifact && typeof artifact.captured_surface === 'string')
    ? artifact.captured_surface
    : null;

  // If either side is unknown, we cannot assert a mismatch — pass through.
  if (!declared || !captured) {
    return { mismatch: false, declared, captured, reason: 'provenance unknown (legacy artifact or no declared surface)' };
  }

  if (declared !== captured) {
    return {
      mismatch: true,
      declared,
      captured,
      reason: `PROVENANCE MISMATCH: criterion declares surface "${declared}" but artifact was captured from "${captured}" — this is the F4 wrong-surface failure; verdict rejected`,
    };
  }

  return { mismatch: false, declared, captured, reason: `provenance ok: both surfaces are "${declared}"` };
}
