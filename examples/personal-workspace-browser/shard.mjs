// Local CI partition only; ordinary acceptance still executes the whole catalog.
export function selectScenarioShard(catalog, shard) {
  if (shard === undefined) return catalog;
  const match = /^([1-9][0-9]*)\/([1-9][0-9]*)$/.exec(shard);
  if (!match) throw new Error("Workspace shard must be index/count with positive integers");
  const [index, count] = match.slice(1).map(Number);
  if (!Number.isSafeInteger(count) || index > count || count > catalog.length) {
    throw new Error("Workspace shard is outside the scenario catalog");
  }
  return catalog.filter((_, offset) => offset % count === index - 1);
}
