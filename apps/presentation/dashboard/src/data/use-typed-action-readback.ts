import { useQuery, useQueryClient } from "@tanstack/react-query";
import { listTypedActions, loadTypedAction, type TypedActionProposal } from "./chat";

const proposalQueryKey = (proposalId: string | null) => ["typed-action-proposal-readback", proposalId];

/** An already-opened proposal is read by its original id, independently of
 * conversation discovery. This does not add it to another Goal's frontier. */
export function useTypedActionProposalReadback(readOnly: boolean, proposalId: string | null) {
  return useQuery({
    queryKey: proposalQueryKey(proposalId),
    queryFn: ({ signal }) => loadTypedAction(proposalId!,
      AbortSignal.any([signal, AbortSignal.timeout(10_000)])),
    enabled: !readOnly && proposalId !== null,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: false,
  });
}

/** Visible workspace readback only: no confirmation, dispatch or effect owner.
 * Query keys fence scope changes; React Query serializes same-key requests,
 * cancels superseded reads and suspends background interval polling. */
export function useTypedActionReadback(readOnly: boolean, goalId: string | null | undefined) {
  const client = useQueryClient();
  const queryKey = ["typed-action-readback", goalId ?? "manager"];
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => listTypedActions(goalId ? { goalId } : {},
      AbortSignal.any([signal, AbortSignal.timeout(10_000)])),
    enabled: !readOnly,
    refetchInterval: 5_000,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: "always",
    retry: false,
  });
  return {
    ...query,
    async acceptProposal(proposal: TypedActionProposal, replacedId?: string) {
      if (readOnly) return;
      // The validated mutation response is already canonical readback. Fence
      // older reads before publishing it, so a cached preview or delayed poll
      // cannot replace an acknowledged receipt during a Goal status refresh.
      const detailKey = proposalQueryKey(proposal.proposal_id);
      await Promise.all([
        client.cancelQueries({ queryKey, exact: true }),
        client.cancelQueries({ queryKey: detailKey, exact: true }),
      ]);
      if (proposal.action_kind === "goal.create") client.setQueryData(detailKey, proposal);
      if (goalId && (proposal.context.goal_id ?? proposal.normalized_parameters.goal_id) !== goalId) return;
      client.setQueryData<TypedActionProposal[]>(queryKey, current => current
        ? [proposal, ...current.filter(row => row.proposal_id !== proposal.proposal_id
          && row.proposal_id !== replacedId)]
        : current);
    },
  };
}
