const NEG = /\b(not|fails?|missing|violat\w*|empty|dash(es)?|absent|broken|hollow|incorrect)\b|—/i;
const POS = /\b(satisfied|correct|present|renders?|displays?|works?|valid|matches)\b/i;

export function buildJudgePrompt(criterion, evidenceText) {
  return [
    'You are a strict UI/behaviour verification judge.',
    'You receive ONLY an evidence artifact captured from a running product.',
    'No context about the code, the author, or the commit is provided or needed.',
    'Decide whether the stated CRITERION is SATISFIED by the ARTIFACT.',
    'Output rules: first line is EXACTLY one token — SATISFIED or NOT_SATISFIED.',
    'Second line: one sentence why.',
    '', `CRITERION: ${criterion}`, '', `ARTIFACT: ${evidenceText}`,
  ].join('\n');
}

export function parseVerdict(raw) {
  const text = String(raw || '').trim();
  const m = text.match(/\b(SATISFIED|NOT_SATISFIED)\b/);
  const token = m ? m[1] : null;
  const reason = text.split('\n').slice(1).join(' ').trim() || text;
  let unclear = !token;
  if (token === 'SATISFIED' && NEG.test(reason)) unclear = true;       // the polarity trap
  if (token === 'NOT_SATISFIED' && !NEG.test(reason) && POS.test(reason)) unclear = true;  // reverse polarity trap
  return { token, reason, unclear };
}

export async function judge(criterion, evidenceText, { runCodex, runClaude }) {
  const prompt = buildJudgePrompt(criterion, evidenceText);
  let raw = null, who = 'codex';
  try { raw = await runCodex(prompt); if (!String(raw || '').trim()) throw new Error('empty'); }
  catch { who = 'claude-fallback'; raw = await runClaude(prompt); }
  const v = parseVerdict(raw);
  return { ...v, judge: who };
}
