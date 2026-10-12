import {z} from "zod";
import {requestJson} from "./chat";

const goalRef = z.object({goal_id: z.string(), goal_instance_id: z.string()});
const permissionScope = z.object({write_scopes: z.array(z.string()), capabilities: z.array(z.string())});
const commitments = z.object({objective: z.string(), non_goals: z.array(z.string()), acceptance: z.array(z.string()), stop_condition: z.string()});
const template = z.object({id: z.string(), digest: z.string(), todo: z.object({text: z.string(), action_kind: z.string(), target_key: z.string(),
  required_write_scopes: z.array(z.string()), required_capabilities: z.array(z.string())})});
export const amendmentViewSchema = z.object({
  ok: z.literal(true), schema_version: z.literal("goal_amendment_result_v0"), status: z.literal("loaded"),
  goal_ref: goalRef, provider_revision: z.string(), work_digest: z.string(), current_lifetime: z.boolean(),
  source_commitments: commitments.nullable(), execution_authority_granted: z.literal(false),
  mutation_readiness: z.object({kind: z.enum(["admit", "reject"]), code: z.string().optional()}),
  intent: z.object({goal_ref: goalRef, goal_revision: z.number().int().positive(), intent_digest: z.string(),
    root_intent: z.object({permission_scope: permissionScope, objective: z.string(), non_goals: z.array(z.string()), acceptance: z.array(z.string()), stop_conditions: z.array(z.string()), terminal_conditions: z.array(z.string())}),
    policy: z.object({eligible_agents: z.array(z.string()), templates: z.array(template), max_created_todos: z.number().int()}),
    created_todo_ids: z.array(z.string()), proposals: z.array(z.object({proposal_id: z.string(), proposer_agent_id: z.string(), template_id: z.string(), base_goal_revision: z.number(), replan_obligation_id: z.string()})),
  }).nullable(),
  peer_adoption: z.array(z.object({agent_id: z.string(), based_on_goal_revision: z.number().nullable(), current: z.boolean()})),
  replan_observations: z.array(z.object({obligation_id: z.string(), agent_id: z.string(), state: z.enum(["pending", "settled"])})),
});
export type AmendmentView = z.infer<typeof amendmentViewSchema>;
const rootParametersSchema = z.object({expected_provider_revision: z.string(), permission_scope: permissionScope,
  terminal_conditions: z.array(z.string()), policy: z.object({out_of_policy: z.literal("reject"), eligible_agents: z.array(z.string()),
    max_created_todos: z.number().int(), templates: z.array(template.omit({digest: true}))})});
export const rootRequestSchema = z.object({goal_id: z.string(), goal_instance_id: z.string(), operation_id: z.string(),
  parameters: rootParametersSchema, preview_digest: z.string().optional()});
export const savedRootRequestSchema = z.object({request: rootRequestSchema.extend({preview_digest: z.string().regex(/^[a-f0-9]{64}$/)}), attempted: z.boolean()});
export type RootRequest = z.infer<typeof rootRequestSchema>;
const resultSchema = z.object({ok: z.boolean(), schema_version: z.literal("goal_amendment_result_v0"), status: z.string(),
  intent_digest: z.string().optional(), reason: z.string().optional(), reason_code: z.string().optional(), execution_authority_granted: z.literal(false),
  current_projection: amendmentViewSchema.omit({ok: true}).optional()});
export async function fetchAmendment(goalId: string): Promise<AmendmentView> {
  const payload = await requestJson<unknown>(`/api/chat/goal-amendment?${new URLSearchParams({goal_id: goalId})}`);
  const outcome = resultSchema.parse(payload);
  if (!outcome.ok) throw new Error(outcome.reason ?? "Goal amendment state is unavailable.");
  return amendmentViewSchema.parse(payload);
}
export async function configureAmendment(request: RootRequest, execute: boolean) {
  return resultSchema.parse(await requestJson<unknown>("/api/chat/goal-amendment", {method: "POST", body: JSON.stringify({...request, execute})}));
}

export async function fetchAmendmentReceipt(request: RootRequest) {
  const query = new URLSearchParams({goal_id: request.goal_id, goal_instance_id: request.goal_instance_id, operation_id: request.operation_id});
  return z.object({ok: z.boolean(), status: z.string(), historical_only: z.literal(true), execution_authority_granted: z.literal(false),
    receipts: z.array(z.object({operation_id: z.string(), result: z.object({goal_ref: goalRef, intent_digest: z.string().optional()})})).optional()
  }).parse(await requestJson<unknown>(`/api/chat/goal-amendment?${query}`));
}
