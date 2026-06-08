import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shippedAtFromRecord } from '../lib/freshness.mjs';

test('shippedAtFromRecord returns null for missing history', () => {
  assert.equal(shippedAtFromRecord(null), null);
  assert.equal(shippedAtFromRecord({}), null);
  assert.equal(shippedAtFromRecord({ history: [] }), null);
});

test('shippedAtFromRecord finds shipped entry with at-first key order', () => {
  const doc = {
    history: [
      { at: '2026-06-01T10:00:00Z', status: 'registered', by: 'thinker' },
      { at: '2026-06-02T12:00:00Z', status: 'shipped', by: 'orc' },
    ],
  };
  assert.equal(shippedAtFromRecord(doc), '2026-06-02T12:00:00Z');
});

test('shippedAtFromRecord finds shipped entry with by-first key order (order-independent)', () => {
  const doc = {
    history: [
      { by: 'thinker', status: 'registered', at: '2026-06-01T10:00:00Z' },
      { by: 'orc', status: 'shipped', at: '2026-06-03T08:00:00Z' },
    ],
  };
  assert.equal(shippedAtFromRecord(doc), '2026-06-03T08:00:00Z');
});

test('shippedAtFromRecord returns the LAST shipped entry', () => {
  const doc = {
    history: [
      { at: '2026-06-01T10:00:00Z', status: 'shipped', by: 'orc' },
      { at: '2026-06-02T10:00:00Z', status: 'bounced', by: 'orc' },
      { at: '2026-06-03T10:00:00Z', status: 'shipped', by: 'orc' },
    ],
  };
  assert.equal(shippedAtFromRecord(doc), '2026-06-03T10:00:00Z');
});

test('shippedAtFromRecord returns null when no shipped entry present', () => {
  const doc = {
    history: [
      { at: '2026-06-01T10:00:00Z', status: 'registered', by: 'thinker' },
    ],
  };
  assert.equal(shippedAtFromRecord(doc), null);
});

test('shippedAtFromRecord returns null when shipped entry has no at field', () => {
  const doc = {
    history: [
      { status: 'shipped', by: 'orc' },
    ],
  };
  assert.equal(shippedAtFromRecord(doc), null);
});
