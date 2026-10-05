/** Pure identity contract shared with presentation. Host grant observation and
 * filesystem IO remain outside this classifier. */
export function normalizeProjectContext(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid project conversation context");
  const context = value as Record<string, unknown>;
  const ref = context.project_ref, workspace = context.workspace_path;
  if (typeof ref !== "string" || !/^[a-f0-9]{24}$/.test(ref)
      || typeof workspace !== "string" || !workspace || context.kind !== "project_workspace"
      || !["local_owner", "bound_owner"].includes(String(context.audience))
      || !["workspace_read", "workspace_write"].includes(String(context.grant))
      || (workspace[0] !== "/" && !/^[A-Za-z]:[\\/]/.test(workspace))) {
    throw new Error("invalid project conversation context");
  }
  const normalized: Record<string, string> = {kind: "project_workspace", project_ref: ref, workspace_path: workspace,
    audience: String(context.audience), grant: String(context.grant)};
  if (context.audience === "bound_owner") {
    for (const field of ["binding_id", "source_ref", "provider_ref", "operator_ref"]) {
      const value = context[field];
      if (typeof value !== "string" || !/^[a-f0-9]{24}$/.test(value)) throw new Error("incomplete bound project identity");
      normalized[field] = value;
    }
  }
  return normalized;
}

export function projectConversationIdentity(input: Record<string, unknown>): Record<string, unknown> {
  const context = normalizeProjectContext(input.context);
  return {context, sandbox: context.grant === "workspace_write" ? "workspace-write" : "read-only",
    channel_id: context.audience === "local_owner"
    ? `project.${context.project_ref}` : `project.external.${context.binding_id}.${context.source_ref}`};
}

export function normalizeStewardGoalScope(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 128
      || value.some(g => typeof g !== "string" || !/^[A-Za-z0-9._-]{1,160}$/.test(g)
        || [".", "..", "loopx-manager"].includes(g))) throw new Error("invalid steward Goal scope");
  return [...new Set(value as string[])].sort();
}

/** An explicitly selected steward has a bounded portfolio, including an honest
 * empty one. Identity is persisted by Core; message text cannot select it.
 */
export function normalizeStewardContext(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("steward context unavailable");
  const row = value as Record<string, unknown>;
  const workspace = normalizeProjectContext({...row, kind: "project_workspace", grant: "workspace_read"});
  if (row.kind !== "bound_steward" || row.audience !== "bound_owner" || row.grant !== "portfolio_read"
      ) throw new Error("invalid bounded steward context");
  return {...workspace, kind: "bound_steward", grant: "portfolio_read", goal_ids: normalizeStewardGoalScope(row.goal_ids)};
}

export function stewardConversationIdentity(input: Record<string, unknown>): Record<string, unknown> {
  const context = normalizeStewardContext(input.context);
  return {context, channel_id: `manager.external.native.${context.binding_id}.${context.source_ref}`};
}

type ConversationScope = Record<string, unknown> & (
  | {kind: "owner_portfolio"; goal_ids: null; private_conversation: true}
  | {kind: "owner_goal"; goal_ids: [string]; private_conversation: true}
  | {kind: "project_workspace"; goal_ids: []; private_conversation: boolean}
  | {kind: "external_audience" | "unavailable"; goal_ids: []; private_conversation: false}
);

/** Classify a host-owned conversation, never model-supplied role or scope.
 * This selects existing read/delivery boundaries; it grants no execution rights.
 */
export function resolveConversationScope(input: Record<string, unknown>): ConversationScope {
  const channel = input.channel_id;
  const goal = input.goal_id;
  if (goal === null) {
    try {
      const context = normalizeProjectContext(input.project_context);
      if (context.audience === "local_owner" && (input.origin === undefined || input.origin === "web")
          && channel === `project.${context.project_ref}`) {
        return {kind: "project_workspace", goal_ids: [], private_conversation: true};
      }
      if (context.audience === "bound_owner" && (input.origin === undefined || input.origin === "lark")
          && channel === `project.external.${context.binding_id}.${context.source_ref}`) {
        return {kind: "project_workspace", goal_ids: [], private_conversation: false};
      }
    } catch { /* Incomplete host identity grants no context. */ }
  }
  if (goal === "loopx-manager" && (input.origin === undefined || input.origin === "lark")) {
    try {
      const selected = stewardConversationIdentity({context: input.steward_context});
      if (channel === selected.channel_id) {
        return {kind: "external_audience", goal_ids: [], private_conversation: false, bound_steward: true};
      }
    } catch { /* An incomplete steward identity cannot authorize a portfolio. */ }
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
