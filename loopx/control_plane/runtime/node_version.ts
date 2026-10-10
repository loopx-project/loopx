const MINIMUM_NODE_VERSION = [22, 22, 3] as const;
const NODE_VERSION_PATTERN = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;

export function isSupportedNodeVersion(version: string): boolean {
  const match = NODE_VERSION_PATTERN.exec(version);
  if (!match) return false;

  const actual = [Number(match[1]), Number(match[2]), Number(match[3])];
  for (let index = 0; index < MINIMUM_NODE_VERSION.length; index += 1) {
    const difference = actual[index] - MINIMUM_NODE_VERSION[index];
    if (difference !== 0) return difference > 0;
  }
  return match[4] === undefined;
}
