import { ChatApiError } from "../../data/chat";
import type { WorkspaceTranslate } from "./i18n";
import type { WorkspaceActionPreviewRequest } from "./personal-workspace-model";

/** Present the terminal owner's refusal; neither inspect nor acquire a lease. */
export function todoCompletionPreviewFeedback(
  error: unknown,
  request: WorkspaceActionPreviewRequest,
  t: WorkspaceTranslate,
): string | null {
  if (request.actionKind !== "todo.update" || request.normalizedParameters.operation !== "complete"
    || !(error instanceof ChatApiError)) return null;
  if (error.payload.error_code !== "handoff_mode_requires_lease"
    && error.payload.error_code !== "lease_fence_required") return null;
  return t("feedback.taskCompletionUnavailable");
}
