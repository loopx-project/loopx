/** Presentation of immutable quota, Turn and work-context captures. No admission or state writes.
 * Confirmed final input and explicit native-hook continuity assumptions remain
 * distinct; successful capture handoff alone is never retained-input proof. */
import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {canonicalAuthorityJson, canonicalAuthoritySha256} from "../coordination/authority_store_codec.ts";
import {reusableTurnContextPaths} from "../capability_hooks.ts";
import {COORDINATION_STATE_CONTRACT} from "../coordination/coordination_state_contract.generated.ts";

const VERSION = "context_delivery_v1";
const MAX_ANCESTRY = 32;
type Path = (string | number)[];
type Split = {fresh: JsonObject; valid: boolean};
const clone = <T>(value: T): T => structuredClone(value);
const equal = (a: unknown, b: unknown): boolean => {
  // Identical requirement strings need no canonical JSON allocation. Keep the
  // existing type-sensitive comparison for every other value.
  if (typeof a === "string" && a === b) return true;
  return a === undefined || b === undefined ? a === b
    : JSON.stringify(canonicalAuthorityJson(a)) === JSON.stringify(canonicalAuthorityJson(b));
};
const digest = (value: unknown): string => `sha256:${canonicalAuthoritySha256(value)}`;
const bytes = (value: unknown): number => Buffer.byteLength(JSON.stringify(value), "utf8");

/** Context keeps complete records and provenance, rather than repeating the
 * canonical reader's field dictionary. Unknown read schemas remain verbatim.
 * A title equal to the complete body carries no second instruction. */
export function compactTodoContextSource(content: JsonObject): JsonObject {
  let result = content;
  const todo = jsonObject(content.todo);
  if (todo && typeof todo.text === "string" && todo.title === todo.text) {
    const copy = {...todo};
    delete copy.title;
    result = {...result, todo: copy};
  }
  const authority = jsonObject(content.authority_read);
  const model = jsonObject(authority?.todo_read_model);
  if (model?.schema_version === COORDINATION_STATE_CONTRACT.todo_read_record.schema_version
      && Array.isArray(model.contract_fields)
      && model.contract_fields.every(field => typeof field === "string")) {
    const {contract_fields: _dictionary, ...metadata} = model;
    result = {...result, authority_read: {...authority, todo_read_model: metadata}};
  }
  return result;
}

function parentAt(root: JsonObject, path: Path): Record<string | number, unknown> {
  let value: unknown = root;
  for (const key of path.slice(0, -1)) value = (value as Record<string | number, unknown>)[key];
  return value as Record<string | number, unknown>;
}

/** Registered requirement fields and capability evidence subtrees are reusable. Revisions, source
 * failures, claims, reviews, grants, current gates and unknown fields stay fresh.
 * Source-array order is transport layout, never the identity of a requirement. */
function splitContext(context: JsonObject): Split {
  const fresh = clone(context), identities = new Set<string>();
  let valid = context.complete === true;
  const add = (identity: string, path: Path, structured = false) => {
    if (identities.has(identity)) { valid = false; return; }
    identities.add(identity);
    const parent = parentAt(fresh, path), key = path[path.length - 1];
    if (!Object.hasOwn(parent, key)) return;
    // These adapters own literal text, not arbitrary nested control records.
    if (!structured && parent[key] !== null && typeof parent[key] !== "string") { valid = false; return; }
    delete parent[key];
  };
  add("work_context_instruction", ["instruction"]);
  const userRead = jsonObject(jsonObject(jsonObject(fresh.user_todos)?.authority_read)?.todo_read_model);
  if (Array.isArray(userRead?.contract_fields) && userRead.contract_fields.every(field => typeof field === "string"))
    add("user_todo_contract_fields", ["user_todos", "authority_read", "todo_read_model", "contract_fields"], true);
  const sources = Array.isArray(fresh.sources) ? fresh.sources : [];
  const sourceIds = new Set<string>();
  for (let i = 0; i < sources.length; i++) {
    const source = jsonObject(sources[i]), content = jsonObject(source?.content);
    if (!source || !content || content.ok === false || content.read_complete === false) continue;
    const prefix: Path = ["sources", i, "content"];
    const kind = source.source;
    const known = ["goal_state", "selected_todo", "goal_acceptance"].includes(String(kind)) || reusableTurnContextPaths(source).length > 0;
    if (known && typeof source.reason === "string") add(`source_reason:${i}`, ["sources", i, "reason"]);
    const readModel = jsonObject(jsonObject(content.authority_read)?.todo_read_model);
    if (kind === "selected_todo" && Array.isArray(readModel?.contract_fields)
        && readModel.contract_fields.every(field => typeof field === "string"))
      add(`todo_contract_fields:${i}`, [...prefix, "authority_read", "todo_read_model", "contract_fields"], true);
    if (kind === "goal_state" && typeof content.state_file === "string"
        && typeof content.text === "string" && Array.isArray(content.excluded_todo_regions)) {
      const id = JSON.stringify([kind, content.state_file]);
      if (sourceIds.has(id)) { valid = false; continue; }
      sourceIds.add(id);
      add(id, [...prefix, "text"]);
    } else if (kind === "selected_todo") {
      const todo = jsonObject(content.todo);
      // An already referenced selected body lacks an independent source id.
      // Keep such residual metadata full rather than guessing its identity.
      if (!todo || typeof todo.todo_id !== "string" || !todo.todo_id) continue;
      const id = JSON.stringify([kind, todo.todo_id]);
      if (sourceIds.has(id)) { valid = false; continue; }
      sourceIds.add(id);
      for (const key of ["text", "note", "title"]) add(JSON.stringify([kind, todo.todo_id, key]), [...prefix, "todo", key]);
    } else if (kind === "goal_acceptance") {
      if (sourceIds.has(kind)) { valid = false; continue; }
      sourceIds.add(kind);
      const contract = jsonObject(content.goal_acceptance_contract);
      const outcomes = jsonObject(contract?.outcome_requirements)?.outcomes;
      if (!Array.isArray(outcomes)) continue;
      const seen = new Set<string>();
      for (let j = 0; j < outcomes.length; j++) {
        const outcome = jsonObject(outcomes[j]);
        if (!outcome || typeof outcome.id !== "string" || !outcome.id || seen.has(outcome.id)) {
          valid = false; continue;
        }
        seen.add(outcome.id);
        const path = [...prefix, "goal_acceptance_contract", "outcome_requirements", "outcomes", j];
        add(JSON.stringify([kind, outcome.id, "objective"]), [...path, "objective"]);
        const requirements = outcome.requirements;
        if (!Array.isArray(requirements)) continue;
        const requirementIds = new Set<string>();
        for (let k = 0; k < requirements.length; k++) {
          const requirement = jsonObject(requirements[k]);
          if (!requirement || typeof requirement.id !== "string" || !requirement.id || requirementIds.has(requirement.id)) {
            valid = false; continue;
          }
          requirementIds.add(requirement.id);
          add(JSON.stringify([kind, outcome.id, requirement.id, "description"]), [...path, "requirements", k, "description"]);
        }
      }
    } else {
      const paths = reusableTurnContextPaths(source);
      if (!paths.length) continue;
      if (content.ok !== true || typeof content.goal_id !== "string" || typeof content.agent_id !== "string") continue;
      const identity = [kind, source.hook_id, source.capability_id, content.goal_id, content.agent_id, content.todo_id ?? null];
      const id = JSON.stringify(identity);
      if (sourceIds.has(id)) { valid = false; continue; }
      sourceIds.add(id);
      for (const path of paths) {
        let value: unknown = content;
        for (const key of path.slice(0, -1)) value = Array.isArray(value)
          ? value[Number(key)] : jsonObject(value)?.[key];
        if (jsonObject(value) || Array.isArray(value))
          add(JSON.stringify([...identity, ...path]), [...prefix, ...path], true);
      }
    }
  }
  return {fresh, valid};
}

/** The projection surface is a transport binding, never a new admission mode.
 * Keep one exact codec for quota and Turn; source-only callers use the same codec. */
const SURFACES = ["work_context", "quota_packet", "turn_envelope"] as const;
type Surface = typeof SURFACES[number];
type Delta = {value: unknown} | {changed?: Record<string, Delta>; values?: Record<string, unknown>; removed?: string[]}
  | {items: Record<string, Delta>; values?: Record<string, unknown>; length?: number};
const pathKey = (path: Path): string => JSON.stringify(path);

// These are existing quota projections. Unknown root fields stay fresh. Within
// these complete, capture-bound projections, exact reuse does not change any
// obligation or make a historical source authoritative.
const REUSABLE_PACKET_FIELDS = new Set([
  "registry", "runtime_root", "source", "project_asset_source", "long_task_cadence_hint",
  "plan_summary", "reason", "recommended_action",
  "goal_frontier_projection", "todo_write_hint", "vision_continuation_audit", "agent_lane_next_action",
  "user_todo_summary", "agent_todo_summary", "blocked_priority_fallback",
  "autonomous_backlog_candidates", "autonomous_monitor_candidates", "next_action_projection_warning",
  "completed_todo_archive_warning", "promotion_readiness_warning", "next_handoff_condition", "agent_command",
  "automation_cadence", "automation_cadence_readback", "open_count",
  "turn_start_capability_hook_dispatch", "rollout_event", "reward_memory_recall", "todo_summary_projection",
  "planning_horizon", "autonomous_replan_obligation", "replan_action_packet", "bounded_research_frontier",
  "action_portfolio", "autonomous_replan_decision", "active_state_next_action",
]);

/** Current controls are forced into the change tree even when equal. This avoids
 * duplicating them in both a fresh frame and a patch. Static authoring guidance
 * remains binding via exact reuse; commands, conditions and identity stay fresh. */
function freshPaths(context: JsonObject, surface: Surface, legacyQuotaFields: readonly string[]): {paths: Path[]; valid: boolean} {
  const paths: Path[] = [];
  let valid = true;
  const leaves = (value: unknown, path: Path) => {
    if (Array.isArray(value) && value.length) value.forEach((v, i) => leaves(v, [...path, i]));
    else if (jsonObject(value) && Object.keys(value as JsonObject).length)
      Object.entries(value as JsonObject).forEach(([k, v]) => leaves(v, [...path, k]));
    else paths.push(path);
  };
  const work = (value: unknown, path: Path) => {
    const source = jsonObject(value);
    if (!source) { paths.push(path); return; }
    const split = splitContext(source); valid &&= split.valid;
    leaves(split.fresh, path);
  };
  const vision = (value: unknown, path: Path) => {
    const audit = jsonObject(value);
    if (!audit) { paths.push(path); return; }
    for (const [key, item] of Object.entries(audit)) {
      if (["required_before_closeout", "authoritative_evidence_kinds", "not_satisfied_by"].includes(key)) continue;
      if (key !== "vision_gap_judge" || !jsonObject(item)) { paths.push([...path, key]); continue; }
      for (const field of Object.keys(item as JsonObject))
        if (!["agent_judge_instruction", "evidence_read_instruction", "registry_read_instruction",
          "external_research_instruction", "research_writeback_required_when_used", "done_only_when", "continue_when"].includes(field))
          paths.push([...path, key, field]);
    }
  };
  const settlement = (value: unknown, path: Path) => {
    const plan = jsonObject(value);
    if (!plan) { paths.push(path); return; }
    for (const [key, item] of Object.entries(plan)) {
      if (key !== "ordered_steps" || !Array.isArray(item)) { paths.push([...path, key]); continue; }
      item.forEach((step, i) => {
        if (!jsonObject(step)) { paths.push([...path, key, i]); return; }
        for (const [field, detail] of Object.entries(step as JsonObject)) {
          if (["precondition", "vision_authoring", "guidance"].includes(field)) continue;
          if (field === "optional_attachments" && Array.isArray(detail)) {
            detail.forEach((attachment, j) => {
              const item = jsonObject(attachment), prefix = [...path, key, i, field, j];
              if (!item) { paths.push(prefix); return; }
              for (const property of Object.keys(item)) if (property !== "guidance") paths.push([...prefix, property]);
            });
          } else paths.push([...path, key, i, field]);
        }
      });
    }
  };
  const horizon = (value: unknown, path: Path) => {
    const object = jsonObject(value);
    if (!object) { paths.push(path); return; }
    for (const [field, item] of Object.entries(object)) {
      if (field === "core_goal" && jsonObject(item)) {
        // Repeat the original intent, provenance and availability. The complete
        // cumulative acceptance tree may be large; it is not the Goal sentence.
        for (const key of Object.keys(item as JsonObject))
          if (key !== "acceptance_contract") paths.push([...path, field, key]);
      } else if (!["work_items", "relations", "acceptance_gaps", "attention_todo_ids",
        "completeness", "detail_refs", "detail_refs_ref", "selection_contract"].includes(field)) paths.push([...path, field]);
    }
  };
  if (surface === "work_context") work(context, []);
  else if (surface === "turn_envelope") {
    for (const [key, value] of Object.entries(context)) {
      if (key === "work_context") work(value, [key]);
      else if ((key === "action" || key === "contract_capsule") && jsonObject(value)) {
        for (const [field, item] of Object.entries(value as JsonObject)) {
          if (key === "action" && field === "planning_horizon") horizon(item, [key, field]);
          else if (key === "contract_capsule" && field === "vision_continuation_audit") vision(item, [key, field]);
          else paths.push([key, field]);
        }
      }
      else if (key === "writeback") {
        const writeback = jsonObject(value);
        if (!writeback) { paths.push([key]); continue; }
        for (const [field, item] of Object.entries(writeback)) {
          if (field === "settlement_plan") settlement(item, [key, field]);
          else paths.push([key, field]);
        }
      } else paths.push([key]);
    }
  } else {
    for (const [key, value] of Object.entries(context)) {
      if (key === "planning_horizon") { horizon(value, [key]); continue; }
      if (key !== "interaction_contract") {
        if (!REUSABLE_PACKET_FIELDS.has(key) && !legacyQuotaFields.includes(key)) paths.push([key]);
        continue;
      }
      const contract = jsonObject(value);
      if (!contract) { paths.push([key]); continue; }
      for (const [channel, fields] of Object.entries(contract)) {
        if (!["agent_channel", "cli_channel"].includes(channel) || !jsonObject(fields)) {
          paths.push([key, channel]); continue;
        }
        for (const [field, item] of Object.entries(fields as JsonObject)) {
          const path = [key, channel, field];
          if (channel === "agent_channel" && field === "work_context") work(item, path);
          else if (channel === "cli_channel" && field === "settlement_plan") settlement(item, path);
          else if (field === "vision_continuation_audit") vision(item, path);
          else paths.push(path);
        }
      }
    }
  }
  return {paths, valid};
}

/** Group complete CURRENT literals, not baseline references. Keep the cheaper
 * ordinary edit when grouping would grow metadata; both containers share this rule. */
function groupCurrentValues(delta: Exclude<Delta, {value: unknown}>): Delta {
  const children = Object.entries("items" in delta ? delta.items : delta.changed ?? {});
  const values = children.filter(([, child]) => "value" in child);
  if (values.length < 2) return delta;
  const nested = Object.fromEntries(children.filter(([, child]) => !("value" in child)));
  const grouped: Exclude<Delta, {value: unknown}> = { ...delta,
    ...("items" in delta ? {items: nested} : {changed: nested}),
    values: Object.fromEntries(values.map(([key, child]) => [key, (child as {value: unknown}).value])),
  };
  if (!("items" in grouped) && !Object.keys(nested).length) delete grouped.changed;
  return bytes(grouped) < bytes(delta) ? grouped : delta;
}

function changeTree(before: unknown, after: unknown, forced: Path[]): Delta | null {
  const exact = new Set(forced.map(pathKey)), ancestors = new Set<string>();
  for (const path of forced) for (let i = 0; i < path.length; i++) ancestors.add(pathKey(path.slice(0, i)));
  const visit = (a: unknown, b: unknown, path: Path): Delta | null => {
    const key = pathKey(path);
    if (exact.has(key)) return {value: clone(b)};
    if (!ancestors.has(key) && equal(a, b)) return null;
    let delta: Delta;
    if (jsonObject(a) && jsonObject(b)) {
      const old = a as JsonObject, next = b as JsonObject;
      const changed: [string, Delta][] = [];
      for (const [field, value] of Object.entries(next)) {
        const child = Object.hasOwn(old, field) ? visit(old[field], value, [...path, field]) : {value: clone(value)};
        if (child) changed.push([field, child]);
      }
      const removed = Object.keys(old).filter(field => !Object.hasOwn(next, field));
      delta = groupCurrentValues({changed: Object.fromEntries(changed), ...(removed.length ? {removed} : {})});
    } else if (Array.isArray(a) && Array.isArray(b)) {
      const items: [string, Delta][] = [];
      b.forEach((value, i) => {
        const child = i < a.length ? visit(a[i], value, [...path, i]) : {value: clone(value)};
        if (child) items.push([String(i), child]);
      });
      delta = groupCurrentValues({items: Object.fromEntries(items), ...(a.length !== b.length ? {length: b.length} : {})});
    } else return {value: clone(b)};
    // Sizing does not publish a value. Copy only a replacement that is chosen,
    // rather than every unchanged subtree and every discarded ancestor value.
    return bytes(delta) < bytes({value: b}) ? delta : {value: clone(b)};
  };
  return visit(before, after, []);
}

/** Array indices address the exact hashed baseline, not inferred entity ids.
 * Reorders, duplicate values and length changes are lossless structural edits.
 * Full strings are replaced; no timestamp/revision normalization or prose diff. */
function applyTree(before: unknown, delta: Delta | null): unknown {
  if (delta === null) return clone(before);
  if ("value" in delta) return clone(delta.value);
  if ("items" in delta) {
    if (!Array.isArray(before)) throw new Error("array baseline missing");
    const result = clone(before);
    if (delta.length !== undefined) result.length = delta.length;
    for (const [key, value] of Object.entries(delta.values ?? {})) result[Number(key)] = clone(value);
    for (const [key, item] of Object.entries(delta.items)) result[Number(key)] = applyTree(before[Number(key)], item);
    return result;
  }
  const source = requireJsonObject(before, "object baseline"), result = clone(source);
  for (const key of delta.removed ?? []) delete result[key];
  for (const [key, value] of Object.entries(delta.values ?? {}))
    Object.defineProperty(result, key, {value: clone(value), enumerable: true, writable: true, configurable: true});
  for (const [key, item] of Object.entries(delta.changed ?? {})) {
    // Avoid prototype setters for JSON property names such as __proto__.
    Object.defineProperty(result, key, {value: applyTree(Object.hasOwn(source, key) ? source[key] : undefined, item),
      enumerable: true, writable: true, configurable: true});
  }
  return result;
}

function targetBinding(request: JsonObject, context: JsonObject, surface: Surface): JsonObject {
  const scope = requireJsonObject(request.scope, "context delivery scope");
  const binding: JsonObject = {projection_version: VERSION, surface};
  for (const key of ["goal_id", "agent_id", "session_id", "context_epoch"]) {
    binding[key] = requireNonEmptyString(scope[key], `scope.${key}`);
  }
  if (scope.continuity_generation !== undefined)
    binding.continuity_generation = requireNonEmptyString(scope.continuity_generation, "scope.continuity_generation");
  binding.capture_id = requireNonEmptyString(request.capture_id, "capture_id");
  binding.context_digest = digest(context);
  return binding;
}

function compatible(base: JsonObject, target: JsonObject): boolean {
  return ["goal_id", "agent_id", "session_id", "context_epoch", "projection_version", "surface", "continuity_generation"]
    .every(key => base[key] === target[key]);
}
function surfaceOf(value: unknown): Surface {
  if (!SURFACES.includes(value as Surface)) throw new Error("unsupported context delivery surface");
  return value as Surface;
}

/** Hosts may confirm final-input retention, or explicitly select the native
 * hook delivery assumption. These bases are distinct; neither grants authority. */
export function prepareContextDelivery(request: JsonObject, legacyQuotaFields: readonly string[] = []): JsonObject {
  const context = requireJsonObject(request.context, "current projection");
  const surface = surfaceOf(request.surface ?? "work_context"), target = targetBinding(request, context, surface);
  const full = (reason: string): JsonObject => ({schema_version: VERSION, mode: "full", target,
    ancestry: 0, reason, context: clone(context)});
  const baseline = jsonObject(request.baseline), retained = jsonObject(request.retained);
  const continuity = jsonObject(request.continuity);
  const binding = jsonObject(baseline?.target), prior = jsonObject(baseline?.context);
  const confirmed = !continuity && retained?.complete === true && equal(retained.target, binding);
  const assumed = !retained && continuity?.basis === "native_hook_assumption"
    && continuity.capture_bytes_verified === true
    && equal(continuity.target, binding);
  if (!binding || !prior || (!confirmed && !assumed) || !compatible(binding, target)
      || binding.context_digest !== digest(prior)) return full("baseline_unavailable");
  const depth = baseline?.ancestry;
  if (!Number.isSafeInteger(depth) || Number(depth) < 0 || Number(depth) >= MAX_ANCESTRY) return full("full_refresh_due");
  const before = freshPaths(prior, surface, legacyQuotaFields), after = freshPaths(context, surface, legacyQuotaFields);
  if (!before.valid || !after.valid) return full("source_not_reusable");
  const changes = changeTree(prior, context, after.paths);
  const delta: JsonObject = {schema_version: VERSION, mode: "delta", target, base: binding,
    ancestry: Number(depth) + 1, changes,
    ...(assumed ? {continuity_basis: "native_hook_assumption"} : {}),
    instruction: "Current controls are inline even when unchanged. Other fields inherit the "
      + (assumed ? "exact baseline under the native-hook continuity assumption" : "confirmed baseline")
      + "; all requirements remain binding. A value replaces its subtree; values assigns complete fields (exact indices with items); changed/items recurse, removed deletes object keys, length sets array length. Lost context triggers automatic full recovery from this capture, never an owner question."};
  if (bytes(delta) >= bytes(full("delta_not_smaller"))) return full("delta_not_smaller");
  if (!equal(applyTree(prior, changes), context)) return full("reconstruction_failed");
  // Native consumers page the wire before they can use its baseline. Deliver
  // the existing basis and interpretation before the potentially large tree;
  // do not duplicate them in a second host instruction or change strict IO.
  return assumed ? {schema_version: VERSION, mode: "delta",
    continuity_basis: delta.continuity_basis, instruction: delta.instruction, ...delta} : delta;
}

/** Validate the exact change tree against the immutable CURRENT capture before
 * applying it. Wire keys never choose freshness, scope or arbitrary write paths.
 * Recovery neither reruns admission nor mutates Goal/quota state. */
export function restoreContextDelivery(request: JsonObject, legacyQuotaFields: readonly string[] = []): JsonObject {
  const current = requireJsonObject(request.current, "current full capture");
  const currentContext = requireJsonObject(current.context, "current full context");
  const target = requireJsonObject(current.target, "current capture binding");
  if (current.mode !== "full" || current.schema_version !== VERSION || target.projection_version !== VERSION || target.context_digest !== digest(currentContext)) {
    throw new Error("current full capture is invalid");
  }
  const fallback = (): JsonObject => ({...clone(current), reason: "automatic_full_recovery", ancestry: 0});
  try {
    const surface = surfaceOf(target.surface);
    const delivery = requireJsonObject(request.delivery, "delivery"), baseline = requireJsonObject(request.baseline, "baseline");
    const base = requireJsonObject(baseline.target, "baseline target"), context = requireJsonObject(baseline.context, "baseline context");
    if (delivery.schema_version !== VERSION || delivery.mode !== "delta" || !equal(delivery.target, target)
        || !equal(delivery.base, base) || !compatible(base, target) || base.context_digest !== digest(context)
        || !Number.isSafeInteger(baseline.ancestry) || Number(baseline.ancestry) < 0
        || delivery.ancestry !== Number(baseline.ancestry) + 1 || Number(delivery.ancestry) > MAX_ANCESTRY) return fallback();
    const before = freshPaths(context, surface, legacyQuotaFields), after = freshPaths(currentContext, surface, legacyQuotaFields);
    if (!before.valid || !after.valid) return fallback();
    const expected = changeTree(context, currentContext, after.paths);
    // Also rejects omitted unchanged controls, redundant/conflicting edits,
    // malformed array indices/lengths and unsupported operations before applying.
    if (!equal(delivery.changes, expected)) return fallback();
    const result = applyTree(context, expected);
    if (!equal(result, currentContext)) return fallback();
    return {...clone(delivery), context: result};
  } catch { return fallback(); }
}
