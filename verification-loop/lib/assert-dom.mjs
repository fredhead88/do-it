import { parsePredicate, evalPredicate } from './predicate.mjs';

/**
 * Run a dom_assertion against an already-open Playwright `page`.
 * `consoleErrors` is an array the caller fills from page 'console' error events.
 * Returns { pass, reason, observed }. A 0-count selector or any forbidden console
 * pattern is a FAIL — never a silent pass (this is the A1 guard).
 */
export async function runDomAssertion(page, assertion, consoleErrors = []) {
  // 1. forbidden console patterns are an immediate fail (render-throw class)
  for (const pat of assertion.forbid_console || []) {
    const hit = consoleErrors.find((e) => e.includes(pat));
    if (hit) return { pass: false, reason: `forbidden console output: ${hit.slice(0, 120)}`, observed: { console: hit } };
  }

  let predicate;
  try { predicate = parsePredicate(assertion.predicate); }
  catch (e) { return { pass: false, reason: e.message, observed: {} }; }

  const loc = page.locator(assertion.selector);
  const count = await loc.count();
  let text = '';
  if (count > 0 && predicate.kind === 'text_matches') {
    try { text = (await loc.first().innerText()).slice(0, 2000); } catch { /* leave '' */ }
  }
  const { pass, reason } = evalPredicate(predicate, { count, text });
  return { pass, reason, observed: { count, text: text.slice(0, 200) } };
}
