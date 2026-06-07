import fs from 'node:fs';
import path from 'node:path';

const appendJsonl = (file, obj) =>
  fs.appendFileSync(file, JSON.stringify({ at: new Date().toISOString(), ...obj }) + '\n');
const readJsonl = (file) =>
  fs.existsSync(file) ? fs.readFileSync(file, 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [];

export const appendProgress = (dir, e) => appendJsonl(path.join(dir, 'PROGRESS.jsonl'), e);
export const escalate = (dir, item) => appendJsonl(path.join(dir, 'NEEDS-HUMAN.jsonl'), item);
export const recordVerdict = (dir, spec, criterionId, verdict, evidenceRef) =>
  appendJsonl(path.join(dir, 'VERIFICATION-LEDGER.jsonl'), { spec, criterionId, verdict, evidenceRef });

export function pinSpecSha(dir, spec, sha, criteria) {
  const p = path.join(dir, 'SPEC-PINS.json');
  const pins = fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : {};
  pins[spec] = { sha, criteria, at: new Date().toISOString() };
  fs.writeFileSync(p, JSON.stringify(pins, null, 2));
}

export function detectScopeReduction(dir, spec, currentCriteria) {
  const p = path.join(dir, 'SPEC-PINS.json');
  if (!fs.existsSync(p)) return [];
  const pinned = (JSON.parse(fs.readFileSync(p, 'utf8'))[spec] || {}).criteria || [];
  const verdicts = new Set(
    readJsonl(path.join(dir, 'VERIFICATION-LEDGER.jsonl'))
      .filter(v => v.spec === spec).map(v => v.criterionId));
  const cur = new Set(currentCriteria);
  return pinned.filter(c => !cur.has(c) && !verdicts.has(c));
}
