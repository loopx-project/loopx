import {z} from "zod";
import {requestJson} from "./chat";
import {requestBriefSchema} from "./request-control";

const rowSchema = z.object({request_id: z.string(), goal_id: z.string(), agent_id: z.string(),
  unfinished: z.boolean(), agenda: z.object({step: z.string()}).passthrough().nullable(),
  control_state: z.string().nullable(),
  brief: requestBriefSchema.nullable().optional(), source_question: z.string(), request_revision: z.number().int().positive().nullable(),
  read_status: z.string(), decision: z.string(), decision_reason: z.string().nullable().optional(),
  returns: z.array(z.object({phase: z.string(), status: z.string(), request_revision: z.number().nullable().optional()})),
  linked_todos: z.array(z.object({todo_id: z.string(), title: z.string().nullable().optional(), status: z.string().optional()})),
  evidence_refs: z.array(z.string()), warnings: z.array(z.string()),
  source_session_id: z.string(), source_turn_id: z.string(), last_event_at: z.string().nullable()});
const inventorySchema = z.object({ok: z.literal(true), session_id: z.string(), rows: z.array(rowSchema),
  snapshot_digest: z.string().regex(/^[a-f0-9]{64}$/),
  pending_count: z.number().int().nonnegative(), matched: z.number().int().nonnegative(),
  next_offset: z.number().int().nonnegative().nullable(), observed_at: z.string(),
  coverage: z.object({scan_complete: z.boolean(), unreadable: z.number().int().nonnegative()})});
export type ConversationRequest = z.infer<typeof rowSchema>;
export type ConversationRequestInventory = z.infer<typeof inventorySchema>;

export async function fetchConversationRequests(sessionId: string, history: boolean, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams({session_id: sessionId, view: history ? "history" : "unfinished", offset: String(offset)});
  const result = inventorySchema.parse(await requestJson<unknown>(`/api/chat/request-inventory?${query}`, {signal}));
  if (result.session_id !== sessionId) throw new Error("Conversation request scope changed.");
  return result;
}
