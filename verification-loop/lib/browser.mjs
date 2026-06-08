import { chromium } from 'playwright';

/** Launch headless chromium, preferring Playwright's bundled build and falling
 *  back to the system Google Chrome (channel:'chrome') on hosts where the bundled
 *  browser can't be installed. */
export async function launchBrowser() {
  const args = ['--no-sandbox', '--disable-dev-shm-usage'];
  try {
    return await chromium.launch({ headless: true, args });
  } catch {
    return await chromium.launch({ channel: 'chrome', headless: true, args });
  }
}
