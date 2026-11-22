import type {ManagedConversationTarget, TypedActionProposal} from "../../data/chat";

/** Display address only. The existing managed Session reader verifies it again. */
export function goalCreatedManagedSession(proposal: TypedActionProposal): {target?: ManagedConversationTarget; invalid?: true} | undefined {
  if (proposal.action_kind !== "goal.create" || proposal.status !== "applied") return undefined;
  const ids = proposal.receipt?.resource_ids;
  if (!ids || typeof ids !== "object" || Array.isArray(ids) || !Object.hasOwn(ids, "managed_agent_id")) return undefined;
  const resources = ids as Record<string, unknown>;
  const goalId = resources.goal_id, agentId = resources.managed_agent_id, sessionId = resources.session_id;
  if (proposal.receipt?.outcome !== "goal_created" || proposal.receipt.projection_verified !== true || resources.agent_id !== "codex"
    || typeof goalId !== "string" || !/^[A-Za-z0-9._-]{1,160}$/.test(goalId)
    || goalId !== proposal.normalized_parameters.goal_id
    || typeof agentId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(agentId)
    || agentId !== proposal.normalized_parameters.agent_id
    || typeof sessionId !== "string" || !/^[A-Za-z0-9._-]{1,160}$/.test(sessionId)
    || resources.channel_id !== `agent.${agentId}`
    || !Object.hasOwn(resources, "goal_instance_id") || !(resources.goal_instance_id === null
      || typeof resources.goal_instance_id === "string" && resources.goal_instance_id.length > 0)) return {invalid: true};
  return {target: {goalId, agentId, sessionId}};
}
