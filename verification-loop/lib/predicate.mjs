// Predicate grammar for dom_assertion. `present` is deliberately NOT supported:
// a blank-but-mounted container satisfies "present", which is the exact A1 failure.
export function parsePredicate(str) {
  const s = String(str || '').trim();
  if (s === 'present' || s.startsWith('present')) {
    throw new Error("predicate 'present' is not allowed for a ui criterion — use min_rows:N, count_gte:N, or text_matches:<re>");
  }
  const i = s.indexOf(':');
  const kind = i === -1 ? s : s.slice(0, i);
  const arg = i === -1 ? '' : s.slice(i + 1);
  if (kind === 'min_rows' || kind === 'count_gte') {
    const n = parseInt(arg, 10);
    if (Number.isNaN(n)) throw new Error(`${kind} requires a number, got ${JSON.stringify(arg)}`);
    return { kind, n };
  }
  if (kind === 'text_matches') {
    try { new RegExp(arg); } catch (e) { throw new Error(`invalid regex in text_matches: ${e.message}`); }
    return { kind, re: arg };
  }
  throw new Error(`unknown predicate: ${str}`);
}

export function evalPredicate(predicate, observed) {
  const { count = 0, text = '' } = observed || {};
  switch (predicate.kind) {
    case 'min_rows':
    case 'count_gte':
      return { pass: count >= predicate.n, reason: `count=${count} vs ${predicate.kind} ${predicate.n}` };
    case 'text_matches': {
      const ok = new RegExp(predicate.re).test(text);
      return { pass: ok, reason: ok ? `text matched /${predicate.re}/` : `text did not match /${predicate.re}/` };
    }
    default:
      return { pass: false, reason: `uncheckable predicate ${predicate.kind}` };
  }
}
