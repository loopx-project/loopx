import { ChatApiError } from "../../data/chat";
import type { WorkspaceTranslate } from "./i18n";
import type { WorkspaceActionPreviewRequest } from "./personal-workspace-model";

/** Input shape only; current execution authority is always checked by the backend. */
export function parseTodoCompletionProof(text: string): {
  agent_id: string; task_lease_idempotency_key: string; task_lease_expected_version: number;
} | null {
  if (!text.trim()) return null;
  try {
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    const row = value as Record<string, unknown>;
    if (Object.keys(row).sort().join(",") !== "agent_id,task_lease_expected_version,task_lease_idempotency_key"
      || typeof row.agent_id !== "string" || !row.agent_id.trim()
      || typeof row.task_lease_idempotency_key !== "string" || !row.task_lease_idempotency_key.trim()
      || typeof row.task_lease_expected_version !== "number" || !Number.isSafeInteger(row.task_lease_expected_version)
      || row.task_lease_expected_version < 0) return null;
    return {agent_id: row.agent_id, task_lease_idempotency_key: row.task_lease_idempotency_key,
      task_lease_expected_version: row.task_lease_expected_version};
  } catch { return null; }
}

/** Present the terminal owner's refusal; neither inspect nor acquire a lease. */
export function todoCompletionProofRequired(error: unknown): boolean {
  return error instanceof ChatApiError && (error.payload.error_code === "handoff_mode_requires_lease"
    || error.payload.error_code === "lease_fence_required");
}

export function todoCompletionPreviewFeedback(
  error: unknown,
  request: WorkspaceActionPreviewRequest,
  t: WorkspaceTranslate,
): string | null {
  if (request.actionKind !== "todo.update" || request.normalizedParameters.operation !== "complete"
    || !todoCompletionProofRequired(error)) return null;
  return t("feedback.taskCompletionUnavailable");
}
