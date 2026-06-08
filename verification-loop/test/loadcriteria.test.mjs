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
