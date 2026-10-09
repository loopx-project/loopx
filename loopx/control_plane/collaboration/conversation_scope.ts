/** Pure identity contract shared with presentation. Host grant observation and
 * filesystem IO remain outside this classifier. */
export function normalizeProjectContext(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("invalid project conversation context");
  const context = value as Record<string, unknown>;
  const ref = context.project_ref, workspace = context.workspace_path;
  if (typeof ref !== "string" || !/^[a-f0-9]{24}$/.test(ref)
      || typeof workspace !== "string" || !workspace || context.kind !== "project_workspace"
      || !["local_owner", "bound_owner", "bound_group"].includes(String(context.audience))
      || !["workspace_read", "workspace_write"].includes(String(context.grant))
      || (workspace[0] !== "/" && !/^[A-Za-z]:[\\/]/.test(workspace))) {
    throw new Error("invalid project conversation context");
  }
  const normalized: Record<string, string> = {kind: "project_workspace", project_ref: ref, workspace_path: workspace,
    audience: String(context.audience), grant: String(context.grant)};
  if (context.filesystem_scope !== undefined) {
    if (context.filesystem_scope !== "workspace_only") throw new Error("invalid project filesystem scope");
    normalized.filesystem_scope = "workspace_only";
  }
  if (context.audience === "bound_owner" || context.audience === "bound_group") {
    for (const field of ["binding_id", "source_ref", "provider_ref", "operator_ref"]) {
      const value = context[field];
      if (typeof value !== "string" || !/^[a-f0-9]{24}$/.test(value)) throw new Error("incomplete bound project identity");
      normalized[field] = value;
    }
  }
  if (context.audience === "bound_group") {
    if (context.filesystem_scope !== "workspace_only") throw new Error("group Chat requires workspace-only isolation");
    for (const field of ["group_ref", "topic_ref"]) {
      const value = context[field];
      if (typeof value !== "string" || !/^[a-f0-9]{24}$/.test(value)) throw new Error("incomplete group topic identity");
      normalized[field] = value;
    }
  }
  return normalized;
}

export function projectConversationIdentity(input: Record<string, unknown>): Record<string, unknown> {
  const context = normalizeProjectContext(input.context);
  const writable = context.grant === "workspace_write";
  const permissions = context.filesystem_scope === "workspace_only" ? {
    // Share authentication within this authorized workspace/owner binding,
    // never with another App or local-owner context. Source topics retain
    // separate threads without requiring a new login for every message.
    host_store_key: [context.project_ref, ...(context.audience !== "local_owner"
      ? [context.binding_id, context.provider_ref, context.operator_ref] : ["local"])].join("."),
    permissions_profile: `loopx_workspace_only_${writable ? "write" : "read"}`,
    host_config: {
      default_permissions: `loopx_workspace_only_${writable ? "write" : "read"}`,
      allow_login_shell: false,
      web_search: "disabled",
      shell_environment_policy: {
        inherit: "none", include_only: ["PATH"], experimental_use_profile: false,
        // A minimal POSIX search path keeps native file tools usable without
        // inheriting account-defined variables or user-installed executables.
        ...(context.workspace_path.startsWith("/")
          ? {set: {PATH: "/usr/bin:/bin:/usr/sbin:/sbin"}} : {}),
      },
      // The host may discover instructions outside the filesystem sandbox.
      // Read project instructions/skills through the bounded native tools.
      skills: {include_instructions: false},
      project_doc_max_bytes: 0,
      permissions: {
        [`loopx_workspace_only_${writable ? "write" : "read"}`]: {
          extends: ":workspace",
          filesystem: {
            ":root": "deny", ":minimal": "read", ":tmpdir": "deny", ":slash_tmp": "deny",
            ":workspace_roots": {".": writable ? "write" : "read"},
          },
          network: {enabled: false},
        },
      },
    },
  } : {};
  return {context, sandbox: writable ? "workspace-write" : "read-only", ...permissions,
    channel_id: context.audience === "local_owner"
    ? `project.${context.project_ref}` : `project.external.${context.binding_id}.${context.source_ref}`};
}

export function normalizeStewardGoalScope(value: unknown): string[] {
  if (!Array.isArray(value)
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
      if (context.audience !== "local_owner" && (input.origin === undefined || input.origin === "lark" || input.origin === "external")
          && channel === `project.external.${context.binding_id}.${context.source_ref}`) {
        return {kind: "project_workspace", goal_ids: [], private_conversation: false};
      }
    } catch { /* Incomplete host identity grants no context. */ }
  }
  if (goal === "loopx-manager" && (input.origin === undefined || input.origin === "lark" || input.origin === "external")) {
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
