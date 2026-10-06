import type {DelegationDependency, DelegationReadback} from "../../data/chat";

type Artifact = NonNullable<DelegationReadback["artifacts"]>[number];

export type ComparisonSelection = {
  sessionId: string;
  target: Pick<DelegationReadback, "operation_id" | "request_id" | "agent_id" | "todo_id">;
  link: DelegationDependency;
  source: DelegationReadback;
  output: Artifact;
};

/** Preserve reading context only while both exact version bindings remain current. */
export function currentComparisonSelection(selection: ComparisonSelection | null,
  result: DelegationReadback, sessionId: string): ComparisonSelection | null {
  if (!selection || selection.sessionId !== sessionId || result.status !== "accepted" ||
      result.error || result.recovery_required ||
      selection.target.operation_id !== result.operation_id || selection.target.request_id !== result.request_id ||
      selection.target.agent_id !== result.agent_id || selection.target.todo_id !== result.todo_id) return null;
  const links = (result.dependencies ?? []).filter(row => row.operation_id === selection.link.operation_id &&
    row.ref === selection.link.ref && row.sha256 === selection.link.sha256 &&
    row.input_ref === selection.link.input_ref && row.relation === selection.link.relation);
  const outputs = (result.artifacts ?? []).filter(row => row.ref === selection.output.ref && row.sha256 === selection.output.sha256);
  if (links.length !== 1 || outputs.length !== 1 || !comparisonSource(links[0], selection.source)) return null;
  return {...selection, target: result, link: links[0], output: outputs[0]};
}

/** A reading convenience only; the user can compare any returned artifact. */
export function preferredComparisonIndex(ref: string, artifacts: Artifact[]): number {
  const exact = artifacts.findIndex(row => row.ref === ref);
  if (exact >= 0) return exact;
  const extension = ref.match(/\.[^./]+$/)?.[0].toLowerCase();
  const sameFormat = extension ? artifacts.findIndex(row => row.ref.toLowerCase().endsWith(extension)) : -1;
  return Math.max(0, sameFormat);
}

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
