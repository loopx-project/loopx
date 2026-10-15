import {z} from "zod";
import {requestJson} from "./chat";
import {REQUEST_CONTROL_SCHEMA, REQUEST_AMENDMENT_OUTCOMES, REQUEST_CONTROL_STATES,
  REQUEST_OBSERVATION_STATES} from "../../../../../loopx/control_plane/collaboration/request_control_contract.js";

export const requestControlRouteSchema = z.object({available: z.boolean().optional(), goal_id: z.string(), goal_instance_id: z.string(),
  agent_id: z.string(), request_id: z.string(), session_id: z.string(), turn_id: z.string()});
export type RequestControlRoute = z.infer<typeof requestControlRouteSchema>;
export function requestControlRouteKey(route: RequestControlRoute) {
  return JSON.stringify([route.goal_id, route.goal_instance_id, route.agent_id, route.request_id, route.session_id, route.turn_id]);
}
export const requestBriefSchema = z.object({schema_version: z.literal("collaboration_brief_v0"), purpose: z.string(), context: z.string(),
  constraints: z.array(z.string()), inputs: z.array(z.object({ref: z.string(), description: z.string(), sha256: z.string().optional(),
    delegation: z.object({operation_id: z.string(), ref: z.string(), relation: z.enum(["responds_to", "revises", "uses"])}).optional()})),
  acceptance: z.array(z.string()), return_requirement: z.string()});
export type RequestBrief = z.infer<typeof requestBriefSchema>;
const terminalSchema = z.object({operation_id: z.string(), reservation_digest: z.string(), outcome: z.enum(REQUEST_AMENDMENT_OUTCOMES),
  goal_changed: z.boolean(), goal_revision: z.number().nullable(), todo_id: z.string().nullable(), receipt_sha256: z.string(),
  goal_ref: z.object({goal_id: z.string(), goal_instance_id: z.string()})});
export const requestControlViewSchema = z.object({schema_version: z.literal(REQUEST_CONTROL_SCHEMA),
  request_id: z.string(), goal_ref: z.object({goal_id: z.string(), goal_instance_id: z.string()}), receiver_agent_id: z.string(),
  revision: z.number().int().positive(), control_version: z.number().int().nonnegative(), revision_digest: z.string(), brief: requestBriefSchema,
  reason: z.string(), read_at: z.string().nullable(), read_readback: z.enum(REQUEST_OBSERVATION_STATES),
  assessment: z.object({decision: z.enum(["adopt", "defer", "reject", "no_change"]), reason: z.string(), assessed_at: z.string()}).nullable(),
  assessment_readback: z.enum(REQUEST_OBSERVATION_STATES),
  control_state: z.enum(REQUEST_CONTROL_STATES),
  cancellation: z.object({operation_id: z.string(), reason: z.string(), requested_at: z.string()}).nullable(),
  active_reservation: z.object({digest: z.string(), binding: z.object({request_revision: z.number(), operation_id: z.string()})}).nullable(),
  terminal_effects: z.array(z.object({digest: z.string(), binding: z.object({request_revision: z.number(), operation_id: z.string()}), terminal: terminalSchema})),
  execution_authority_granted: z.literal(false)});
export type RequestControlView = z.infer<typeof requestControlViewSchema>;
export const pendingRequestControlSchema = z.object({route: requestControlRouteSchema, operation: z.enum(["revise", "cancel", "recover"]),
  parameters: z.record(z.string(), z.unknown()), execute: z.literal(true)});
export type PendingRequestControl = z.infer<typeof pendingRequestControlSchema>;
const responseSchema = z.object({ok: z.boolean(), view: requestControlViewSchema.nullable().optional(),
  request_control: requestControlViewSchema.nullable().optional(), effect: terminalSchema.nullable().optional(),
  readback_errors: z.array(z.string()).optional(), effect_recovery: z.object({readback_errors: z.array(z.string()).optional()}).optional()});
function routing(route: RequestControlRoute) {const {available: _available, ...fields} = route; return fields;}
export async function fetchRequestControl(route: RequestControlRoute) {
  const response = responseSchema.parse(await requestJson<unknown>(`/api/chat/request-control?${new URLSearchParams(routing(route))}`));
  if (!response.ok || !response.view) throw new Error("Current request readback is unavailable.");
  return response.view;
}
export async function applyRequestControl(request: PendingRequestControl, execute = true) {
  return responseSchema.parse(await requestJson<unknown>("/api/chat/request-control", {method: "POST",
    body: JSON.stringify({...routing(request.route), operation: request.operation, parameters: request.parameters, execute})}));
}
