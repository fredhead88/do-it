import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
import { runDomAssertion } from '../lib/assert-dom.mjs';
import { launchBrowser } from '../lib/browser.mjs';

const fixture = (f) => pathToFileURL(path.resolve('test/fixtures', f)).href;
let browser;
before(async () => { browser = await launchBrowser(); });
after(async () => { await browser.close(); });

async function check(file, assertion) {
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  await page.goto(fixture(file), { waitUntil: 'domcontentloaded' });
  const res = await runDomAssertion(page, assertion, consoleErrors);
  await page.close();
  return res;
}

const A1 = { selector: "[data-testid='a1-region-table'] tbody tr", predicate: 'min_rows:1', forbid_console: ['ZodError'] };

test('populated page PASSES the A1 assertion', async () => {
  const res = await check('a1-populated.html', A1);
  assert.equal(res.pass, true, res.reason);
});

test('blank-but-present container FAILS (min_rows:1 sees 0 rows)', async () => {
  const res = await check('a1-blank.html', { ...A1, forbid_console: [] });
  assert.equal(res.pass, false);
  assert.match(res.reason, /count=0|0 rows|min_rows/i);
});

test('Zod console error FAILS via forbid_console', async () => {
  const res = await check('a1-blank.html', A1);
  assert.equal(res.pass, false);
  assert.match(res.reason, /ZodError|console/i);
});

test('selector matching 0 elements FAILS, never silently passes', async () => {
  const res = await check('a1-populated.html', { selector: "[data-testid='does-not-exist']", predicate: 'min_rows:1', forbid_console: [] });
  assert.equal(res.pass, false);
});
