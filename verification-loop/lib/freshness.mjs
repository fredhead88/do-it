/** True if a spec shipped fewer than `floorMinutes` ago (give the deploy time to go
 *  live before asserting — the interim for the deferred deployed_sha gate). A missing
 *  timestamp does not block. */
export function tooFreshToVerify(shippedAtIso, floorMinutes = 10) {
  if (!shippedAtIso) return false;
  const t = Date.parse(shippedAtIso);
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) < floorMinutes * 60 * 1000;
}
