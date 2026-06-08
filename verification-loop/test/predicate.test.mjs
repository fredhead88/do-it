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
