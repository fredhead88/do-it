/** True if a spec shipped fewer than `floorMinutes` ago (give the deploy time to go
 *  live before asserting — the interim for the deferred deployed_sha gate). A missing
 *  timestamp does not block. */
export function tooFreshToVerify(shippedAtIso, floorMinutes = 10) {
  if (!shippedAtIso) return false;
  const t = Date.parse(shippedAtIso);
  if (Number.isNaN(t)) return false;
  return (Date.now() - t) < floorMinutes * 60 * 1000;
}

/** Extract the `at` timestamp from the last history entry whose status === 'shipped'.
 *  Returns the timestamp string, or null if not found.
 *  Order of keys within each history entry does not matter. */
export function shippedAtFromRecord(doc) {
  if (!doc || !Array.isArray(doc.history)) return null;
  for (let i = doc.history.length - 1; i >= 0; i--) {
    const entry = doc.history[i];
    if (entry && entry.status === 'shipped') return entry.at || null;
  }
  return null;
}
