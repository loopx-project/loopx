/** Local organization of existing source Goals, never execution registration. */
import {BARE_SHA256_PATTERN} from "../content_digest.ts";
import {canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {requireJsonObject, requireNonEmptyString, type JsonObject} from "../runtime_decode.ts";
import {createHash} from "node:crypto";

/** Locate a declared document, never reconstruct Goal intent from a title or plan. */
export function planGoalSourceDocument(input: JsonObject): JsonObject {
  const goalId = requireNonEmptyString(input.goal_id, "original Goal");
  const registration = requireJsonObject(input.registration, "source registration");
  if (!Array.isArray(registration.goals) || registration.goals.length !== 1) throw new Error("Choose one registered original Goal");
  const goal = requireJsonObject(registration.goals[0], "original Goal");
  if (goal.id !== goalId || !Array.isArray(goal.authority_sources)) throw new Error("Original Goal declaration is unavailable");
  const documents = goal.authority_sources.map(value => requireJsonObject(value, "declared source"))
    .filter(row => row.kind === "goal_doc" && row.role === "primary_goal_document");
  if (documents.length !== 1) throw new Error("Choose one declared primary Goal document");
  const declared = requireNonEmptyString(documents[0].path, "declared document path");
  const repo = typeof goal.repo === "string" ? goal.repo : "";
  let path = declared, expected: string | null = null;
  if (input.snapshot !== null && input.snapshot !== undefined) {
    const snapshot = requireJsonObject(input.snapshot, "captured document binding");
    if (Object.keys(snapshot).length !== 3 || snapshot.declared_path !== declared
        || typeof snapshot.sha256 !== "string" || !BARE_SHA256_PATTERN.test(snapshot.sha256)) {
      throw new Error("The captured document must match the original declaration");
    }
    path = requireNonEmptyString(snapshot.path, "captured document path"); expected = snapshot.sha256;
  }
  if ([declared, path, repo].some(value => /[\0\r\n]/.test(value))) throw new Error("Invalid document location");
  return {goal_id: goalId, path, repo, expected_sha256: expected,
    registration_digest: canonicalAuthoritySha256(registration)};
}

export function projectGoalSourceDocument(input: JsonObject): JsonObject {
  const plan = requireJsonObject(input.plan, "document plan");
  const registration = requireJsonObject(input.registration, "source registration");
  if (canonicalAuthoritySha256(registration) !== plan.registration_digest) throw new Error("The original declaration changed during reading");
  const document = requireJsonObject(input.document, "original document");
  const text = document.text;
  if (typeof text !== "string" || !text.length || text.includes("\0") || Buffer.byteLength(text, "utf8") > 128 * 1024) {
    throw new Error("The complete original document is unavailable");
  }
  const digest = createHash("sha256").update(text, "utf8").digest("hex");
  if (plan.expected_sha256 !== null && plan.expected_sha256 !== digest) throw new Error("The captured document changed");
  // Paths remain in the source adapter, not browser payloads or public sinks.
  return {document: {text, sha256: digest, captured: plan.expected_sha256 !== null}};
}

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

/** Read original Goal strategy versions without turning them into Explore
 * events, verified adoption, or a new history authority. */
export function projectGoalSourceStrategies(input: JsonObject): JsonObject {
  const goalId = requireNonEmptyString(input.goal_id, "original Goal");
  const namespace = requireNonEmptyString(input.namespace, "source namespace");
  const history = requireJsonObject(input.history, "native run history");
  if (!BARE_SHA256_PATTERN.test(namespace) || history.ok !== true
      || history.goal_filter !== goalId || !Array.isArray(history.goals) || history.goals.length !== 1) {
    throw new Error("Strategy history must belong to exactly its original source Goal");
  }
  const goal = requireJsonObject(history.goals[0], "native Goal");
  const runs = goal.latest_runs;
  if (goal.id !== goalId || goal.registry_member !== true || !Array.isArray(runs)
      || !Number.isSafeInteger(goal.unique_runs) || (goal.unique_runs as number) < 0
      || runs.length !== goal.unique_runs) {
    throw new Error("The complete original Goal history is unavailable");
  }
  const records = new Map<string, JsonObject>();
  for (const value of runs) {
    const run = requireJsonObject(value, "native run");
    if (run.goal_id !== goalId) throw new Error("A strategy record belongs to another Goal");
    if (run.agent_vision === undefined || run.agent_vision === null) continue;
    const vision = requireJsonObject(run.agent_vision, "recorded strategy");
    const path = requireNonEmptyString(run.json_path, "native record identity");
    const agentId = requireNonEmptyString(run.agent_id, "recorded Agent");
    if (vision.schema_version !== "goal_vision_replan_contract_v0"
        || (vision.goal_id !== undefined && vision.goal_id !== goalId)
        || (vision.agent_id !== undefined && vision.agent_id !== agentId)) {
      throw new Error("The recorded strategy scope is inconsistent");
    }
    const recordedAt = typeof run.generated_at === "string" ? run.generated_at : "";
    const classification = typeof run.classification === "string" ? run.classification : "";
    // The private native path contributes to identity, but never leaves the
    // source adapter. A changed original gets a different selectable version.
    const id = canonicalAuthoritySha256({namespace, goal_id: goalId, path,
      recorded_at: recordedAt, agent_id: agentId, classification, agent_vision: vision});
    records.set(id, {id, recorded_at: recordedAt, agent_id: agentId, classification,
      agent_vision: vision});
  }
  const stamp = (row: JsonObject) => {
    const value = Date.parse(String(row.recorded_at));
    return Number.isFinite(value) ? value : -Infinity;
  };
  const items = [...records.values()].sort((left, right) => {
    const a = stamp(left), b = stamp(right);
    return a === b ? 0 : b - a;
  });
  return {items, total: items.length, native_run_count: goal.unique_runs};
}
