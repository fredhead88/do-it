const CHROME = '/usr/bin/google-chrome-stable';
export function selfcheck(cfg, env, fsImpl) {
  const failures = [];
  const need = [cfg.auth?.user_env, cfg.auth?.pass_env, cfg.api_key_env].filter(Boolean);
  for (const name of need)
    if (!env[name] || !String(env[name]).trim()) failures.push(`credential ${name} missing or empty`);
  if (!fsImpl.existsSync(CHROME)) failures.push(`chrome not found at ${CHROME}`);
  for (const k of ['prod_base', 'api_base', 'page_map'])
    if (!cfg[k]) failures.push(`config missing ${k}`);
  return { ok: failures.length === 0, failures };
}
