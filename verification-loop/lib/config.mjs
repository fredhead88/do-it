import fs from 'node:fs';
import path from 'node:path';
const ROOT = new URL('..', import.meta.url).pathname;
export function loadConfig(name = 'albert-scott') {
  const cfg = JSON.parse(fs.readFileSync(path.join(ROOT, 'config', `${name}.json`), 'utf8'));
  for (const k of ['prod_base', 'api_base', 'auth', 'page_map']) {
    if (!cfg[k]) throw new Error(`config ${name}: missing ${k}`);
  }
  return cfg;
}
export function runDir(date = new Date().toISOString().slice(0, 10)) {
  const d = path.join(ROOT, 'runs', date);
  fs.mkdirSync(d, { recursive: true });
  return d;
}
export { ROOT };
