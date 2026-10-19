import {z} from "zod";
import {requestJson} from "./chat";

const ownerSchema = z.object({todo_id: z.string(), current_owner: z.string().nullable(), current_claim_matches_actor: z.boolean()});
const projectionEnvelopeSchema = z.object({
  schema_version: z.literal("loopx_projection_envelope_v0"),
  observed_at: z.string(), served_at: z.string(), fresh: z.boolean(), complete: z.boolean(), alert: z.boolean(),
  alert_reasons: z.array(z.string()),
}).passthrough();
const roomWorkSchema = z.object({
  ok: z.boolean(), status: z.string(), actor_id: z.string(), connection_id: z.string(), scope_revision: z.string(),
  canonical_claim_accepted: z.boolean(), readback_verified: z.boolean(), execution_authority_granted: z.literal(false), claim_available: z.boolean(),
  projection_envelope: projectionEnvelopeSchema.optional(),
  projection: z.object({source_revision: z.string(), generated_at: z.string(),
    selected_todo: z.object({todo_id: z.string()}).nullable(),
    counts: z.object({unclaimed: z.number(), user_gates: z.number()}),
    inspected_todo: ownerSchema.nullable().optional(),
  }).optional(),
  receipt: ownerSchema.extend({receipt_id: z.string(), acceptance: z.string(),
    current_owner: z.string().nullable().optional(), current_claim_matches_actor: z.boolean().optional(),
  }).optional(),
});
export type RoomWork = z.infer<typeof roomWorkSchema>;
export const roomClaimIntentSchema = z.object({
  goal_id: z.string(), connection_id: z.string(), scope_revision: z.string(),
  todo_id: z.string(), expected_revision: z.string(), idempotency_key: z.string(),
});
export type RoomClaimIntent = z.infer<typeof roomClaimIntentSchema>;

export async function fetchRoomWork(goalId: string, connectionId: string, todoId?: string) {
  const query = new URLSearchParams({goal_id: goalId, connection_id: connectionId});
  if (todoId) query.set("todo_id", todoId);
  return roomWorkSchema.parse(await requestJson(`/api/chat/lark/room-work?${query}`));
}
export async function claimRoomWork(intent: RoomClaimIntent) {
  return roomWorkSchema.parse(await requestJson("/api/chat/lark/room-work", {method: "POST", body: JSON.stringify(intent)}));
}
