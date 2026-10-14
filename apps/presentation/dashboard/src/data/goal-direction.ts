import {z} from "zod";
import {requestJson} from "./chat";

const ref = z.object({goal_id:z.string(),goal_instance_id:z.string()});
const item = z.object({material_id:z.string(),required:z.boolean(),required_revision:z.string().nullable(),observed_revision:z.string().nullable(),
  state:z.enum(["missing","inaccessible","required_unread","stale","current"]),reason_codes:z.array(z.string()),receipt_ref:z.string().nullable()});
const projection = z.object({baseline_digest:z.string().nullable(),direction_state:z.enum(["blocked","re_evaluation_required","current"]),
  reason_codes:z.array(z.string()),items:z.array(item)});
export const directionViewSchema=z.object({schema_version:z.literal("goal_direction_result_v0"),ok:z.literal(true),status:z.literal("loaded"),
  goal_ref:ref,provider_revision:z.string(),enabled:z.boolean(),authority_digest:z.string().nullable(),registered_agents:z.array(z.string()),projection:projection.nullable(),
  authority_registry:z.object({project_materials:z.record(z.string(),z.object({id:z.string(),revision:z.string(),boundary:z.string()})),
    goal_direction_requirements:z.array(z.object({requirement_id:z.string(),material_id:z.string(),purpose:z.string(),required:z.boolean()}))}).nullable(),
  vision_gap:z.object({kind:z.literal("goal_direction_basis"),agent_id:z.string(),state:z.string()}).nullable(),
  acceptance_basis:z.object({contract_revision:z.number().nullable(),contract_digest:z.string().nullable(),acceptance_assessed:z.literal(false),result:z.literal("not_assessed")}),
  execution_authority_granted:z.literal(false),acceptance_assessed:z.literal(false)});
export type DirectionView=z.infer<typeof directionViewSchema>;
export const directionMaterialSchema=z.object({material_id:z.string(),source_path:z.string(),purpose:z.string(),topics:z.array(z.string()),required:z.boolean(),
  boundary:z.enum(["public","public_safe","local_private"])});
export type DirectionMaterial=z.infer<typeof directionMaterialSchema>;
export const directionRequestSchema=z.object({goal_id:z.string(),goal_instance_id:z.string(),operation:z.enum(["configure","disable"]),operation_id:z.string(),
  parameters:z.object({expected_provider_revision:z.string(),materials:z.array(directionMaterialSchema).optional()}),preview_digest:z.string().optional(),reviewed_request_sha256:z.string().optional()});
export type DirectionRequest=z.infer<typeof directionRequestSchema>;
export const savedDirectionSchema=z.object({request:directionRequestSchema,attempted:z.boolean()});
const outcomeSchema=z.object({ok:z.boolean(),status:z.string(),reason:z.string().optional(),reason_code:z.string().optional(),authority_digest:z.string().nullable().optional(),
  reviewed_request_sha256:z.string().optional(),
  execution_authority_granted:z.literal(false),acceptance_assessed:z.literal(false)});
export async function fetchGoalDirection(goalId:string,agentId?:string){
  const query=new URLSearchParams({goal_id:goalId});if(agentId)query.set("agent_id",agentId);
  const value=await requestJson<unknown>(`/api/chat/goal-direction?${query}`);const outcome=outcomeSchema.parse(value);
  if(!outcome.ok)throw Error(outcome.reason??"Direction state unavailable");
  return directionViewSchema.parse(value);
}
export async function updateGoalDirection(request:DirectionRequest,execute:boolean){
  const {goal_id,goal_instance_id,operation,operation_id,parameters,preview_digest}=request;
  return outcomeSchema.parse(await requestJson<unknown>("/api/chat/goal-direction",{method:"POST",body:JSON.stringify({goal_id,goal_instance_id,operation,operation_id,parameters,preview_digest,execute})}));
}
export async function recoverGoalDirection(request:DirectionRequest){
  const query=new URLSearchParams({goal_id:request.goal_id,goal_instance_id:request.goal_instance_id,operation_id:request.operation_id});
  return z.object({ok:z.boolean(),status:z.string(),historical_only:z.literal(true),receipts:z.array(z.object({operation_id:z.string(),request_sha256:z.string(),result:z.object({goal_ref:ref,
    authority_digest:z.string(),agent_id:z.null()})})).optional()}).parse(await requestJson<unknown>(`/api/chat/goal-direction?${query}`));
}
