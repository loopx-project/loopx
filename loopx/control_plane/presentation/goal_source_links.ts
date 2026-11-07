/** Local organization of existing source Goals, never execution registration. */
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {requireJsonObject, requireNonEmptyString, type JsonObject} from "../runtime_decode.ts";

export function projectGoalSourceLinks(input: JsonObject): JsonObject {
  if (!Array.isArray(input.local_goal_ids) || !Array.isArray(input.links) || input.links.length > 32) {
    throw new Error("Use a bounded Goal source catalog");
  }
  const localIds = new Set(input.local_goal_ids.map(id => requireNonEmptyString(id, "local Goal")));
  const seen = new Set<string>();
  const sources = new Set<string>();
  const goals = input.links.flatMap(value => {
    const link = requireJsonObject(value, "source link");
    const goalId = requireNonEmptyString(link.goal_id, "source Goal");
    const parent = requireNonEmptyString(link.parent_goal_id, "parent Goal");
    const namespace = requireNonEmptyString(link.namespace, "source namespace");
    const label = requireNonEmptyString(link.label, "source label");
    const workspaceId = link.workspace_goal_id === undefined ? goalId
      : requireNonEmptyString(link.workspace_goal_id, "workspace source Goal");
    if (!/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,159}$/.test(workspaceId)) {
      throw new Error("Invalid workspace source Goal identity");
    }
    if (!BARE_SHA256_PATTERN.test(namespace)) {
      throw new Error("Invalid source identity");
    }
    // Parent lifecycle is owned elsewhere. A retained reference must not
    // make unrelated local/source Goals unreadable after its parent leaves.
    if (!localIds.has(parent)) return [];
    if (localIds.has(workspaceId)) {
      if (link.workspace_goal_id === undefined) return [];
      throw new Error("A source view cannot replace a local Goal");
    }
    if (seen.has(workspaceId) || sources.has(namespace)) {
      throw new Error("Source Goals need an existing local parent and an unambiguous original identity");
    }
    seen.add(workspaceId);
    sources.add(namespace);
    return [{id: workspaceId, parent_goal_id: parent, display_name: label,
      registry_member: true, activation_state: "active",
      source_access: {namespace, label, read_only: true,
        ...(workspaceId === goalId ? {} : {original_goal_id: goalId})}}];
  });
  return {goals};
}

/** Route a single source's status through a workspace view. Canonical records,
 * prose, evidence references and original Task/run ids are never rewritten. */
export function projectGoalSourceStatus(input: JsonObject): JsonObject {
  const status = requireJsonObject(input.status, "native status");
  const row = requireJsonObject(input.row, "source view");
  const access = requireJsonObject(row.source_access, "source access");
  const workspaceId = requireNonEmptyString(row.id, "workspace Goal");
  const originalId = requireNonEmptyString(access.original_goal_id ?? row.id, "original Goal");
  const history = requireJsonObject(status.run_history, "native history");
  if (!Array.isArray(history.goals) || history.goals.length !== 1
      || requireJsonObject(history.goals[0], "native Goal").id !== originalId) {
    throw new Error("The source status must contain exactly its original Goal");
  }
  // These are status projection routing fields, not source document contents.
  // All children of this status were read from the same qualified namespace.
  const route = (value: unknown, key = ""): unknown => {
    if ((key === "goal_id" || key === "goal_filter") && value === originalId) return workspaceId;
    if (Array.isArray(value)) return value.map(item => key === "goal_ids" && item === originalId
      ? workspaceId : route(item));
    if (value && typeof value === "object") return Object.fromEntries(
      Object.entries(value).map(([name, item]) => [name, route(item, name)]));
    return value;
  };
  const projected = (workspaceId === originalId ? status : route(status)) as JsonObject;
  const projectedHistory = requireJsonObject(projected.run_history, "native history");
  const goal = requireJsonObject((projectedHistory.goals as unknown[])[0], "native Goal");
  goal.native_parent_goal_id = goal.parent_goal_id ?? null;
  goal.id = workspaceId;
  goal.parent_goal_id = row.parent_goal_id;
  goal.source_access = access;
  return projected;
}
