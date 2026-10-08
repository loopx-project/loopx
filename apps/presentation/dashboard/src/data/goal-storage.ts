import {z} from "zod";
import {ChatApiError, requestJson} from "./chat";
// The local migration owner's existing built-in subset. Do not pull the Node
// provider graph into the browser; this schema validates its HTTP projection.
const provider = z.enum(["file", "sqlite"]);
export type MigrationProvider = z.infer<typeof provider>;
export const storageCarrierSchema = z.object({
  goal_id: z.string().min(1), preview_id: z.string().regex(/^[a-f0-9]{32}$/),
  plan_sha256: z.string().regex(/^[a-f0-9]{64}$/),
});
export type StorageCarrier = z.infer<typeof storageCarrierSchema>;
export const storageSourceSchema = z.object({
  goal_id: z.string(), canonical: z.boolean(), provider: provider.nullable(),
  store_identity: z.string().optional(), provider_revision: z.string().optional(), cursor: z.string().optional(),
  todo_count: z.number().int().nonnegative().optional(), unsettled_lease_count: z.number().int().nonnegative().optional(),
});
export type StorageSource = z.infer<typeof storageSourceSchema>;
export const storageResultSchema = z.object({
  ok: z.boolean(), goal_id: z.string(), status: z.string(),
  execution_authority_granted: z.literal(false), authority_changed: z.boolean().nullable(),
  preview_id: z.string().regex(/^[a-f0-9]{32}$/).optional(),
  plan_sha256: z.string().regex(/^[a-f0-9]{64}$/).optional(),
  target_provider: provider.optional(), selected_provider: provider.optional(),
  reviewed_source: z.object({provider, cursor: z.string(), provider_revision: z.string(), store_identity: z.string()}).optional(),
  current: storageSourceSchema.nullable().optional(),
  cold_source: z.object({
    active_todo_count: z.number().int().nonnegative(), archived_todo_count: z.number().int().nonnegative(),
    lease_file_count: z.number().int().nonnegative(), unsettled_lease_count: z.number().int().nonnegative(),
    capture_artifacts_present: z.boolean(), outbox_files_present: z.boolean(),
    import_ready: z.literal(false), writer_stop_verified: z.literal(false), outbox_reconciliation_verified: z.literal(false),
  }).optional(),
  recovery: z.object({phase: z.enum(["prepared", "completed"]), target_store_identity: z.string(), archive_sha256: z.string()}).nullable().optional(),
  reason_code: z.string().optional(),
});
export type StorageResult = z.infer<typeof storageResultSchema>;

async function read(url: string, init?: RequestInit): Promise<StorageResult> {
  try { return storageResultSchema.parse(await requestJson<unknown>(url, init)); }
  catch (error) {
    if (error instanceof ChatApiError && error.payload.http_status === 409) return storageResultSchema.parse(error.payload);
    throw error;
  }
}
export function fetchGoalStorage(goalId: string) {
  return read(`/api/chat/goal-storage?${new URLSearchParams({goal_id: goalId})}`);
}
export function previewGoalStorage(goalId: string, target: MigrationProvider) {
  return read("/api/chat/goal-storage/preview", {method: "POST", body: JSON.stringify({goal_id: goalId, provider: target})});
}
export function recoverGoalStorage(carrier: StorageCarrier, apply = false) {
  return read(`/api/chat/goal-storage/${apply ? "apply" : "recover"}`, {method: "POST", body: JSON.stringify(storageCarrierSchema.parse(carrier))});
}
