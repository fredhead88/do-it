export function gamingTriggerFires(criterion, evidence = {}) {
  return Boolean(
    evidence.roundPerfectPass || evidence.allOrNothing ||
    evidence.priorVerdict === 'HOLLOW' || evidence.regressionLedgerHit ||
    evidence.entityTargetedCommit);
}

export function metamorphicRelation(klass, original = {}, alt = {}) {
  switch (klass) {
    case 'non_null_numeric':
      return alt.value !== null && alt.value !== undefined && alt.value !== '' && alt.value !== '—';
    case 'visible_rendered':
      return alt.rendered === true;
    case 'state_changes':
      return alt.before !== alt.after;
    case 'reconciles_to_db':
      return Math.abs((alt.ui ?? 0) - (alt.db ?? 0)) <= (alt.tolerance ?? 0.01);
    default:
      return false;
  }
}

export async function runIpt(criterion, evidence, deps) {
  if (!gamingTriggerFires(criterion, evidence)) return { verdict: 'SKIP', detail: 'no trigger' };
  if (!(await deps.altMemberExists(criterion, evidence)))
    return { verdict: 'DATA-GAP', detail: 'no alternate member to perturb' };
  const alt = await deps.observeAlt(criterion, evidence);
  const holds = metamorphicRelation(criterion.type, evidence.original, alt);
  return holds
    ? { verdict: 'CONFIRMED', detail: 'metamorphic relation holds' }
    : { verdict: 'SUSPECTED-GAMING', detail: 'relation failed with alt data present' };
}
