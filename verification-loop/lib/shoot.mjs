import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const ANALYTICS_BLOCK = /\/(analytics|gtag|segment|posthog|hotjar|heap|mixpanel|clarity|fullstory)/;

/**
 * shoot({ url, out, statePath }) -> { shotFile, textFile, status }
 * Single-page screenshot + first 2000 chars innerText saved to disk.
 * Uses storageState for auth. Blocks analytics routes.
 * Returns file refs { shotFile, textFile, status, url }.
 */
export async function shoot({ url, out, statePath }) {
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  const ctxOpts = { viewport: { width: 1440, height: 2200 } };
  if (statePath && fs.existsSync(statePath)) {
    ctxOpts.storageState = statePath;
  }

  const ctx = await browser.newContext(ctxOpts);
  const page = await ctx.newPage();

  // Block analytics noise
  await page.route('**/*', (route) => {
    if (ANALYTICS_BLOCK.test(route.request().url())) {
      route.abort();
    } else {
      route.continue();
    }
  });

  let status = null;
  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    status = resp ? resp.status() : null;
  } catch (e) {
    await browser.close();
    throw new Error(`shoot goto failed: ${e.message}`);
  }

  // Wait for an API response to confirm data loaded (same pattern as probe.mjs)
  try {
    await page.waitForResponse(
      (r) => r.url().includes('/api/') && r.status() < 500,
      { timeout: 25000 }
    );
  } catch {
    // proceed anyway — may be a non-dashboard page
  }

  // Give JS time to render after API response
  try {
    await page.locator('main, [role="main"], #__next').first().waitFor({
      state: 'visible',
      timeout: 10000,
    });
  } catch {
    // proceed anyway
  }

  // Determine output paths
  const dir = out ? path.dirname(out) : '.';
  const base = out ? path.basename(out, path.extname(out)) : 'shot';
  const shotFile = out || path.join(dir, `${base}.png`);
  const textFile = path.join(dir, `${base}-text.txt`);

  await page.screenshot({ path: shotFile, fullPage: true });

  let innerText = '';
  try {
    innerText = await page.locator('body').innerText();
  } catch { /* ignore */ }
  fs.writeFileSync(textFile, innerText.slice(0, 2000));

  await browser.close();

  return { shotFile, textFile, status, url };
}
