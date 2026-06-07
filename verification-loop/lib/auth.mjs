import { request as pwRequest } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

/**
 * acquire(cfg, env, runDir) -> statePath
 * POST {email, password} to prod_base + auth.login_path via Playwright request context.
 * Saves storageState to runDir/state.json. Returns the state file path.
 * Re-acquires only if state.json is missing OR caller deletes it on 401.
 */
export async function acquire(cfg, env, runDir) {
  const statePath = path.join(runDir, 'state.json');
  if (fs.existsSync(statePath)) return statePath;

  const ctx = await pwRequest.newContext({ baseURL: cfg.prod_base });
  try {
    const res = await ctx.post(cfg.auth.login_path, {
      data: {
        email: env[cfg.auth.user_env],
        password: env[cfg.auth.pass_env],
      },
    });
    if (res.status() !== 200) {
      let body = '';
      try { body = await res.text(); } catch { /* ignore */ }
      throw new Error(`login failed: ${res.status()} ${body.slice(0, 200)}`);
    }
    await ctx.storageState({ path: statePath });
    fs.chmodSync(statePath, 0o600);
  } finally {
    await ctx.dispose();
  }
  return statePath;
}
