// Which stored draft is newest is a fact about the record, not about the array
// it arrived in. `ChatActionStore.list` returns proposals ordered by
// (`updated_at`, `proposal_id`) newest first, and the workspace restores exactly
// that order; a draft created in this session is appended to the end of the map
// instead. Comparing the stored time, then the stable id, answers the same way
// for a restored list, a refreshed list, a list with a new draft at either end,
// and a list served out of contract order.

export type ProposalRecency = {
  createdAt?: string;
  previewId: string;
  updatedAt?: string;
};

export function proposalRecencyKey(proposal: ProposalRecency): [string, string] {
  // Mirror the store: it sorts the raw stored strings, never a parsed date, and
  // `created_at` only covers a preview that a host callback returned directly.
  return [proposal.updatedAt ?? proposal.createdAt ?? "", proposal.previewId];
}

/** Newest first: `[...drafts].sort(compareProposalRecency)[0]` is the newest. */
export function compareProposalRecency(a: ProposalRecency, b: ProposalRecency): number {
  const [aTime, aId] = proposalRecencyKey(a);
  const [bTime, bId] = proposalRecencyKey(b);
  if (aTime !== bTime) return aTime < bTime ? 1 : -1;
  if (aId === bId) return 0;
  return aId < bId ? 1 : -1;
}

/** Every draft but the newest, still newest first so the fold reads in order. */
export function olderProposals<T extends ProposalRecency>(proposals: T[]): T[] {
  return [...proposals].sort(compareProposalRecency).slice(1);
}
