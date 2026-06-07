import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const ANALYTICS_BLOCK = /\/(analytics|gtag|segment|posthog|hotjar|heap|mixpanel|clarity|fullstory)/;
const DEFAULT_TIMEOUT = 30000;
const DEPLOY_SLEEP_MS = 30000;

/**
 * probe(cfg, statePath, runDir) -> summary object
 * Loads each page in cfg.page_map using storageState. Collects 4xx/5xx + console errors.
 * Blocks analytics routes. Waits for an /api/ response then a sentinel toBeVisible.
 * Writes snap-<name>.txt, shot-<name>.png, text-<name>.txt to runDir.
 * Returns compact summary (counts + file refs). On 502/503 waits 30s and retries once,
 * tagging result as DEPLOY_IN_PROGRESS.
 */
export async function probe(cfg, statePath, runDir) {
  const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  const results = {};
  try {
    for (const [name, pagePath] of Object.entries(cfg.page_map)) {
      results[name] = await probePage(browser, cfg, statePath, runDir, name, pagePath, false);
    }
  } finally {
    await browser.close();
  }
  return results;
}

async function probePage(browser, cfg, statePath, runDir, name, pagePath, isRetry) {
  const ctx = await browser.newContext({
    storageState: statePath,
    viewport: { width: 1440, height: 1600 },
  });
  const page = await ctx.newPage();
  const fails = [];
  const cerr = [];
  let deployWindow = false;

  // Block analytics noise
  await page.route('**/*', (route) => {
    if (ANALYTICS_BLOCK.test(route.request().url())) {
      route.abort();
    } else {
      route.continue();
    }
  });

  page.on('response', (r) => {
    if (r.status() >= 400) {
      fails.push(`${r.status()} ${r.url().replace(cfg.prod_base, '').slice(0, 90)}`);
    }
  });
  page.on('console', (m) => {
    if (m.type() === 'error') cerr.push(m.text().slice(0, 120));
  });

  const url = cfg.prod_base + pagePath;
  let gotoStatus = null;
  try {
    const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 60000 });
    gotoStatus = resp ? resp.status() : null;
  } catch (e) {
    await ctx.close();
    return { name, error: `GOTO-ERR: ${e.message.slice(0, 100)}`, files: {} };
  }

  // Check for 502/503 deploy window
  if (gotoStatus === 502 || gotoStatus === 503) {
    if (!isRetry) {
      await ctx.close();
      console.log(`[probe] ${name} got ${gotoStatus} — sleeping ${DEPLOY_SLEEP_MS}ms, retrying once`);
      await new Promise(r => setTimeout(r, DEPLOY_SLEEP_MS));
      const retryResult = await probePage(browser, cfg, statePath, runDir, name, pagePath, true);
      retryResult.deployWindowOnFirst = true;
      return retryResult;
    } else {
      deployWindow = true;
    }
  }

  // Wait for an /api/ response to complete (indicating data loaded), with 502/503 deploy check
  let apiWaitFailed = false;
  try {
    const apiResp = await page.waitForResponse(
      (r) => r.url().includes('/api/') && r.status() >= 200 && r.status() < 300,
      { timeout: DEFAULT_TIMEOUT }
    );
    if (apiResp.status() === 502 || apiResp.status() === 503) {
      if (!isRetry) {
        await ctx.close();
        console.log(`[probe] ${name} API got ${apiResp.status()} — sleeping ${DEPLOY_SLEEP_MS}ms, retrying once`);
        await new Promise(r => setTimeout(r, DEPLOY_SLEEP_MS));
        const retryResult = await probePage(browser, cfg, statePath, runDir, name, pagePath, true);
        retryResult.deployWindowOnFirst = true;
        return retryResult;
      }
      deployWindow = true;
    }
  } catch {
    apiWaitFailed = true;
  }

  // Wait for a sentinel visible element (main content area)
  let sentinelVisible = false;
  try {
    await page.locator('main, [role="main"], #__next, .dashboard-content').first().waitFor({
      state: 'visible',
      timeout: 15000,
    });
    sentinelVisible = true;
  } catch {
    // page may have loaded but not match sentinel — still capture
  }

  const snapFile = path.join(runDir, `snap-${name}.txt`);
  const shotFile = path.join(runDir, `shot-${name}.png`);
  const textFile = path.join(runDir, `text-${name}.txt`);

  // Aria snapshot (U1)
  try {
    const snap = await page.locator('body').ariaSnapshot();
    fs.writeFileSync(snapFile, snap);
  } catch (e) {
    fs.writeFileSync(snapFile, `[aria snapshot failed: ${e.message}]`);
  }

  // Screenshot
  await page.screenshot({ path: shotFile, fullPage: false });

  // Inner text (whole page, truncated at 10k chars for disk)
  try {
    const txt = await page.locator('body').innerText();
    fs.writeFileSync(textFile, txt.slice(0, 10000));
  } catch (e) {
    fs.writeFileSync(textFile, `[innerText failed: ${e.message}]`);
  }

  await ctx.close();

  return {
    name,
    url,
    gotoStatus,
    failedRequests: fails.length ? fails : 'NONE',
    consoleErrors: cerr.length ? cerr : 'none',
    sentinelVisible,
    apiWaitFailed,
    deployInProgress: deployWindow,
    files: { snap: snapFile, shot: shotFile, text: textFile },
  };
}
