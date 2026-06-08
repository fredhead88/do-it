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
