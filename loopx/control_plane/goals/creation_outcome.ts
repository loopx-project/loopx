/** Adapt an owner's approved creation fields to the existing outcome owner.
 * No prose interpretation, check activation, completion or scheduling policy. */
export type GoalCreationOutcome = {
  operationId: string;
  refinement: {
    outcome_id: string; owner_agent_id: string; objective: string;
    requirements: Array<{id: string; description: string}>;
    todo_ids: string[]; source: {ref: string; summary: string};
  };
};

export function goalCreationOutcome(proposalId: string, parameters: Record<string, unknown>,
  todoIds: string[]): GoalCreationOutcome | undefined {
  const criteria = parameters.completion_criteria, agent = parameters.agent_id;
  if (typeof criteria !== "string" || !criteria.trim() || typeof agent !== "string" || !agent.trim()) return undefined;
  const operationId = `goal-create:${proposalId}:completion`;
  return {operationId, refinement: {
    outcome_id: "goal-creation", owner_agent_id: agent, objective: String(parameters.title),
    requirements: [{id: "completion", description: criteria}], todo_ids: todoIds,
    source: {ref: operationId, summary: "Owner-approved Goal creation completion criteria"},
  }};
}
