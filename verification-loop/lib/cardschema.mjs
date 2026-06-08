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
