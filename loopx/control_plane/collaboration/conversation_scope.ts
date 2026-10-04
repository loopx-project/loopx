/** Pure identity contract shared with presentation. Host grant observation and
 * filesystem IO remain outside this classifier. */
export function normalizeProjectContext(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid project conversation context");
  const context = value as Record<string, unknown>;
  const ref = context.project_ref, workspace = context.workspace_path;
  if (typeof ref !== "string" || !/^[a-f0-9]{24}$/.test(ref)
      || typeof workspace !== "string" || !workspace || context.kind !== "project_workspace"
      || context.audience !== "local_owner" || context.grant !== "workspace_read"
      || (workspace[0] !== "/" && !/^[A-Za-z]:[\\/]/.test(workspace))) {
    throw new Error("invalid project conversation context");
  }
  return {kind: "project_workspace", project_ref: ref, workspace_path: workspace,
    audience: "local_owner", grant: "workspace_read"};
}

type ConversationScope = Record<string, unknown> & (
  | {kind: "owner_portfolio"; goal_ids: null; private_conversation: true}
  | {kind: "owner_goal"; goal_ids: [string]; private_conversation: true}
  | {kind: "project_workspace"; goal_ids: []; private_conversation: true}
  | {kind: "external_audience" | "unavailable"; goal_ids: []; private_conversation: false}
);

/** Classify a host-owned conversation, never model-supplied role or scope.
 * This selects existing read/delivery boundaries; it grants no execution rights.
 */
export function resolveConversationScope(input: Record<string, unknown>): ConversationScope {
  const channel = input.channel_id;
  const goal = input.goal_id;
  if (goal === null && (input.origin === undefined || input.origin === "web")) {
    try {
      const context = normalizeProjectContext(input.project_context);
      if (channel === `project.${context.project_ref}`) {
        return {kind: "project_workspace", goal_ids: [], private_conversation: true};
      }
    } catch { /* Incomplete host identity grants no context. */ }
  }
  if (channel === "manager") {
    return {kind: "owner_portfolio", goal_ids: null, private_conversation: true};
  }
  if (typeof goal === "string" && /^[A-Za-z0-9._-]{1,160}$/.test(goal)
      && goal !== "." && goal !== ".." && goal !== "loopx-manager" && channel === `goal.${goal}`
      && (input.origin === undefined || input.origin === "web")) {
    return {kind: "owner_goal", goal_ids: [goal], private_conversation: true};
  }
  if (typeof channel === "string" && channel.startsWith("manager.external.")
      && channel.length > "manager.external.".length) {
    return {kind: "external_audience", goal_ids: [], private_conversation: false};
  }
  return {kind: "unavailable", goal_ids: [], private_conversation: false};
}
