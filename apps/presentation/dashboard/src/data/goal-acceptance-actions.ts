import {z} from "zod";
import {goalAcceptanceContractSchema} from "./goal-acceptance-observation";
import type {TypedActionProposal} from "./chat";
const responseSchema = z.object({ok: z.literal(true), goal_id: z.string(), provider_revision: z.string(),
  goal_acceptance_contract: goalAcceptanceContractSchema, checks_passed: z.boolean().optional()});
export type AcceptanceReadback = z.infer<typeof responseSchema>;
export async function acceptanceRequest(goalId: string, operation?: Record<string, unknown>, signal?: AbortSignal): Promise<AcceptanceReadback> {
  const url = "/api/chat/goal-acceptance";
  const response = await fetch(operation ? url : `${url}?goal_id=${encodeURIComponent(goalId)}`, operation
    ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({...operation, goal_id: goalId}), signal} : {signal});
  if (!response.ok) throw new Error("Acceptance unavailable");
  const result = responseSchema.parse(await response.json());
  if (result.goal_id !== goalId) throw new Error("Acceptance source changed");
  return result;
}

// Read model only. The existing typed acceptance owner validates the complete
// document and its command/file bindings again at apply time.
const preparedDocument = z.object({objective: z.string().min(1),
  scope: z.discriminatedUnion("kind", [z.object({kind: z.literal("all_advancement")}),
    z.object({kind: z.literal("selected_work"), todo_ids: z.array(z.string()).min(1)})]),
  criteria: z.array(z.object({id: z.string(), description: z.string(),
    validation_argv: z.array(z.string()).min(1),
    oracle: z.object({output_refs: z.array(z.string())}).passthrough().optional(),
  }).passthrough()).min(1),
}).passthrough();
export function preparedAcceptancePlan(proposals: TypedActionProposal[], goalId: string) {
  // Never resurrect an older draft after the newest plan was applied, cancelled
  // or invalidated. Ambiguous creation order requires a fresh Agent proposal.
  const matching = proposals.filter(proposal => proposal.action_kind === "goal.acceptance.check"
      && proposal.context.kind === "goal" && proposal.context.goal_id === goalId
      && proposal.normalized_parameters.goal_id === goalId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const proposal = matching[0];
  if (!proposal || matching[1]?.created_at === proposal.created_at) return null;
  if (proposal.permission_classification !== "local_command_execution"
      || !["preview_ready", "deferred"].includes(proposal.status) || proposal.gate || proposal.error
      || !proposal.available_transitions.includes("apply")) return null;
  const parsed = preparedDocument.safeParse(proposal.normalized_parameters.document);
  return parsed.success ? {proposal, document: parsed.data} : null;
}
