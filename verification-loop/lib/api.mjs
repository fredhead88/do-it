import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

/**
 * callApi(cfg, env, statePath, apiPath, runDir, name) -> evidenceFilePath
 *
 * Calls cfg.api_base + apiPath. Authenticates using:
 *   1. API_KEY header (env[cfg.api_key_env]) — preferred for backend-only routes
 *   2. Cookie from storageState, extracted as a raw header string
 *
 * Writes a SIGNED evidence record to runDir/evidence/<name>.json:
 *   { url, status, at, body_sha256, body_excerpt }
 *
 * Returns the evidence file path.
 */
export async function callApi(cfg, env, statePath, apiPath, runDir, name) {
  const url = cfg.api_base + apiPath;
  const evidenceDir = path.join(runDir, 'evidence');
  fs.mkdirSync(evidenceDir, { recursive: true });
  const evidenceFile = path.join(evidenceDir, `${name}.json`);

  // Build headers
  const headers = { 'Accept': 'application/json' };

  // Add API key as Bearer token
  const apiKey = env[cfg.api_key_env];
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`;
  }

  // Extract cookies from storageState if available
  if (statePath && fs.existsSync(statePath)) {
    try {
      const state = JSON.parse(fs.readFileSync(statePath, 'utf8'));
      const cookies = (state.cookies || [])
        .filter(c => {
          const prodHost = new URL(cfg.prod_base).hostname;
          return c.domain.includes(prodHost) || prodHost.includes(c.domain.replace(/^\./, ''));
        })
        .map(c => `${c.name}=${c.value}`)
        .join('; ');
      if (cookies) headers['Cookie'] = cookies;
    } catch { /* ignore parse errors */ }
  }

  let status = 0;
  let bodyText = '';
  try {
    const resp = await fetch(url, { headers });
    status = resp.status;
    bodyText = await resp.text();
  } catch (e) {
    bodyText = `[fetch error: ${e.message}]`;
    status = 0;
  }

  const bodySha256 = crypto.createHash('sha256').update(bodyText).digest('hex');
  const record = {
    url,
    status,
    at: new Date().toISOString(),
    body_sha256: bodySha256,
    body_excerpt: bodyText.slice(0, 2000),
  };

  fs.writeFileSync(evidenceFile, JSON.stringify(record, null, 2));
  return evidenceFile;
}
