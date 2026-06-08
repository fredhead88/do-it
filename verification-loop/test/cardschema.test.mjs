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
