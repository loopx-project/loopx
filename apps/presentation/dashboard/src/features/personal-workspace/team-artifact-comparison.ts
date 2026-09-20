import type {DelegationDependency, DelegationReadback} from "../../data/chat";

type Artifact = NonNullable<DelegationReadback["artifacts"]>[number];

/** Compare only the exact source version bound to the request, never a newer file. */
export function comparisonSource(link: DelegationDependency, source: DelegationReadback): Artifact | null {
  if (link.state !== "current" || source.operation_id !== link.operation_id ||
      source.status !== "accepted" || source.recovery_required || source.error) return null;
  const matches = (source.artifacts ?? []).filter(row => row.ref === link.ref && row.sha256 === link.sha256);
  return matches.length === 1 ? matches[0] : null;
}

/** A changed range, not a semantic diff: unchanged lines inside the range may remain. */
export function changedRange(before: string, after: string) {
  const left = before.split("\n"), right = after.split("\n");
  let start = 0, suffix = 0;
  while (start < Math.min(left.length, right.length) && left[start] === right[start]) start++;
  while (suffix < Math.min(left.length, right.length) - start &&
         left[left.length - suffix - 1] === right[right.length - suffix - 1]) suffix++;
  return {left, right, start, leftEnd: left.length - suffix, rightEnd: right.length - suffix};
}
