/** Shared grammar for Explore finding and execution evidence references. */
export function isExploreEvidenceRef(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$/.test(value);
}
