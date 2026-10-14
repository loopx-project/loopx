import { createHash } from "node:crypto";
import { INTERNAL_STATE_PRIVATE_TEXT_PATTERNS } from "./vision_checkpoint.ts";

/** M1 is an opt-in pure read model. It cannot authenticate caller-supplied receipts. */
export const GOAL_DIRECTION_SCHEMA = "goal_direction_baseline_v0";
export const GOAL_DIRECTION_LIMITS = {
  requirements: 64, materials: 256, receipts: 4096,
  input_bytes: 2 * 1024 * 1024, output_bytes: 256 * 1024,
} as const;

// Local to this RFC's projection; item states reuse agent_material_frontier_v0.
export type DirectionState = "blocked" | "re_evaluation_required" | "current";
type MaterialState = "missing" | "inaccessible" | "required_unread" | "stale" | "current";
export type DirectionReason =
  | "direction_input_invalid" | "direction_capacity_exceeded"
  | "authority_registry_missing" | "authority_registry_invalid"
  | "direction_requirement_unresolved" | "authority_revision_missing"
  | "material_inaccessible" | "material_required_unread"
  | "material_revision_changed" | "authority_conflict" | "authority_freshness_stale"
  | "material_receipt_invalid" | "material_receipt_conflict";

type Row = Record<string, unknown>;
interface Requirement {
  requirement_id: string; material_id?: string; topic?: string;
  purpose: string; required: boolean;
}
interface Receipt {
  receipt_id: string; goal_id: string; agent_id: string; material_id: string;
  todo_id: string | null; observed_revision: string | null;
  outcome: "read" | "used" | "verified" | "unavailable"; recorded_at: string;
}
interface DirectionItem {
  material_id: string; bound_by: string[]; required: boolean; topics: string[];
  required_revision: string | null; observed_revision: string | null;
  state: MaterialState; reason_codes: DirectionReason[]; receipt_ref: string | null;
}
export interface DirectionBaseline {
  schema_version: typeof GOAL_DIRECTION_SCHEMA; goal_id: string; agent_id: string;
  generated_at: string; baseline_digest: string | null;
  direction_state: DirectionState; reason_codes: DirectionReason[];
  summary: { required_count: number; current_count: number; stale_count: number; blocked_count: number };
  items: DirectionItem[];
  advisory: { kind: "agent_vision_re_evaluation"; creates_work: false; rewrites_vision: false; changes_goal_route: false } | null;
  truth_contract: { authority_is_goal_owned: true; projection_is_read_only: true;
    receipt_is_agent_scoped: true; provider_is_not_write_authority: true; raw_source_body_recorded: false };
}

export class DirectionBaselineInputError extends Error {
  readonly reason: DirectionReason;
  constructor(reason: DirectionReason) { super(reason); this.name = "DirectionBaselineInputError"; this.reason = reason; }
}
function refuse(reason: DirectionReason): never { throw new DirectionBaselineInputError(reason); }
function row(value: unknown, reason: DirectionReason): Row {
  if (!value || typeof value !== "object" || Array.isArray(value)) refuse(reason);
  return value as Row;
}
function text(value: unknown, reason: DirectionReason, limit = 180): string {
  if (typeof value !== "string" || !value.trim() || value !== value.trim()
      || [...value].length > limit || value.startsWith("/") || /[\r\n\t]/.test(value)
      || /(?:[a-z][a-z0-9+.-]*:\/\/|www\.)/i.test(value)
      || INTERNAL_STATE_PRIVATE_TEXT_PATTERNS.some(pattern => pattern.test(value))) refuse(reason);
  return value;
}
function optionalText(value: unknown, reason: DirectionReason): string | null {
  return value === undefined || value === null || value === "" ? null : text(value, reason);
}
/** Shared identity boundary for the pure reader and its source-backed writer. */
export function validateDirectionIdentity(value: unknown): string {return text(value, "direction_input_invalid");}
// Stable JSON excludes object insertion order. Arrays are sorted by their owning rules.
function canonical(value: unknown): string {
  return JSON.stringify(value, (_key, current) => current && typeof current === "object" && !Array.isArray(current)
    ? Object.fromEntries(Object.keys(current).sort().map(key => [key, current[key]])) : current);
}
function bytes(value: unknown): number { return Buffer.byteLength(JSON.stringify(value), "utf8"); }
function capacity(count: number, limit: number): void {
  if (count > limit) refuse("direction_capacity_exceeded");
}
// Retain sub-millisecond ordering; Date.parse alone can turn contradictory observations into a tie.
function timestamp(value: unknown, reason: DirectionReason): bigint {
  if (typeof value !== "string") refuse(reason);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$/.exec(value);
  if (!match) refuse(reason);
  const [, year, month, day, hour, minute, second, fraction = "", zone] = match;
  const date = new Date(`${year}-${month}-${day}T00:00:00Z`);
  if (!Number.isFinite(date.getTime()) || date.toISOString().slice(0, 10) !== `${year}-${month}-${day}`
      || +hour > 23 || +minute > 59 || +second > 59
      || (zone !== "Z" && (+zone.slice(1, 3) > 23 || +zone.slice(4) > 59))) refuse(reason);
  const milliseconds = Date.parse(`${year}-${month}-${day}T${hour}:${minute}:${second}${zone}`);
  if (!Number.isFinite(milliseconds)) refuse(reason);
  return BigInt(milliseconds) * 1_000_000n + BigInt(fraction.padEnd(9, "0"));
}
function requirements(value: unknown): Requirement[] {
  if (!Array.isArray(value) || !value.length) refuse("authority_registry_invalid");
  capacity(value.length, GOAL_DIRECTION_LIMITS.requirements);
  const unique = new Map<string, Requirement>();
  for (const entry of value) {
    const r = row(entry, "authority_registry_invalid");
    const id = text(r.requirement_id, "authority_registry_invalid");
    if ((r.material_id !== undefined) === (r.topic !== undefined)
        || (r.required !== undefined && typeof r.required !== "boolean")) refuse("authority_registry_invalid");
    const normalized: Requirement = {
      requirement_id: id, purpose: text(r.purpose, "authority_registry_invalid", 220), required: r.required ?? true,
      ...(r.material_id !== undefined ? { material_id: text(r.material_id, "authority_registry_invalid") }
        : { topic: text(r.topic, "authority_registry_invalid", 120) }),
    };
    if (unique.has(id) && canonical(unique.get(id)) !== canonical(normalized)) refuse("authority_registry_invalid");
    unique.set(id, normalized);
  }
  return [...unique.values()].sort((a, b) => compare(a.requirement_id, b.requirement_id));
}
function compare(a: string, b: string): number { return a < b ? -1 : a > b ? 1 : 0; }
function materials(value: unknown): Map<string, Row> {
  if (value === undefined) refuse("authority_registry_missing");
  const entries = Array.isArray(value) ? value.map(item => [undefined, item] as const)
    : Object.entries(row(value, "authority_registry_invalid"));
  const result = new Map<string, Row>();
  for (const [key, value] of entries) {
    const item = row(value, "authority_registry_invalid");
    const id = text(item.id ?? item.source_id ?? key, "authority_registry_invalid");
    if ((key !== undefined && key !== id) || (item.id !== undefined && item.source_id !== undefined && item.id !== item.source_id)
        || result.has(id)) refuse("authority_registry_invalid");
    result.set(id, item);
  }
  return result;
}
function topics(rawTopics: unknown): Map<string, string[]> {
  const result = new Map<string, string[]>();
  for (const [key, value] of Object.entries(row(rawTopics, "authority_registry_missing"))) {
    const topic = text(key, "authority_registry_invalid", 120);
    const ids = (Array.isArray(value) ? value : [value]).map(item => {
      if (typeof item === "string") return text(item, "authority_registry_invalid");
      const ref = row(item, "authority_registry_invalid");
      const candidates = [ref.material_id, ref.id, ref.source_id].filter(x => x !== undefined);
      if (new Set(candidates).size !== 1) refuse("authority_registry_invalid");
      return text(candidates[0], "authority_registry_invalid");
    });
    if (!ids.length) refuse("authority_registry_invalid");
    result.set(topic, [...new Set(ids)].sort(compare));
  }
  return result;
}
function receipts(value: unknown): Receipt[] {
  if (!Array.isArray(value)) refuse("material_receipt_invalid");
  capacity(value.length, GOAL_DIRECTION_LIMITS.receipts);
  const unique = new Map<string, { receipt: Receipt; raw: string }>();
  for (const entry of value) {
    const r = row(entry, "material_receipt_invalid");
    if (r.schema_version !== "material_usage_receipt_v0") refuse("material_receipt_invalid");
    const outcome = r.outcome;
    if (outcome !== "read" && outcome !== "used" && outcome !== "verified" && outcome !== "unavailable") refuse("material_receipt_invalid");
    const receipt: Receipt = {
      receipt_id: text(r.receipt_id, "material_receipt_invalid"),
      goal_id: text(r.goal_id, "material_receipt_invalid"), agent_id: text(r.agent_id, "material_receipt_invalid"),
      material_id: text(r.material_id, "material_receipt_invalid"), todo_id: optionalText(r.todo_id, "material_receipt_invalid"),
      observed_revision: optionalText(r.observed_revision, "material_receipt_invalid"),
      outcome, recorded_at: text(r.recorded_at, "material_receipt_invalid"),
    };
    timestamp(receipt.recorded_at, "material_receipt_invalid");
    if (outcome !== "unavailable" && receipt.observed_revision === null) refuse("material_receipt_invalid");
    const raw = canonical(r);
    if (unique.has(receipt.receipt_id) && unique.get(receipt.receipt_id)!.raw !== raw) refuse("material_receipt_conflict");
    unique.set(receipt.receipt_id, {receipt, raw});
  }
  return [...unique.values()].map(value => value.receipt);
}
function selectReceipt(rows: Receipt[], materialId: string): Receipt | null {
  const matches = rows.filter(r => r.material_id === materialId);
  matches.sort((a, b) => {
    const at = timestamp(a.recorded_at, "material_receipt_invalid"), bt = timestamp(b.recorded_at, "material_receipt_invalid");
    return at === bt ? compare(a.receipt_id, b.receipt_id) : at > bt ? -1 : 1;
  });
  const latest = matches[0];
  if (!latest) return null;
  for (const candidate of matches) {
    if (timestamp(candidate.recorded_at, "material_receipt_invalid") !== timestamp(latest.recorded_at, "material_receipt_invalid")) break;
    if (candidate.outcome !== latest.outcome || candidate.observed_revision !== latest.observed_revision) refuse("material_receipt_conflict");
  }
  return latest;
}
function empty(goal: string, agent: string, generated: string): DirectionBaseline {
  return { schema_version: GOAL_DIRECTION_SCHEMA, goal_id: goal, agent_id: agent, generated_at: generated,
    baseline_digest: null, direction_state: "blocked", reason_codes: [],
    summary: {required_count: 0, current_count: 0, stale_count: 0, blocked_count: 0}, items: [], advisory: null,
    truth_contract: {authority_is_goal_owned: true, projection_is_read_only: true, receipt_is_agent_scoped: true,
      provider_is_not_write_authority: true, raw_source_body_recorded: false} };
}

/** No declaration means no projection. A refused complete read has no partial items or digest. */
export function buildGoalDirectionBaseline(input: unknown): DirectionBaseline | null {
  const request = row(input, "direction_input_invalid");
  const registry = request.authority_registry;
  // An absent declaration leaves existing callers completely untouched, even with legacy inputs.
  if (registry && typeof registry === "object" && !Array.isArray(registry)
      && !Object.hasOwn(registry, "goal_direction_requirements")) return null;
  const goal = validateDirectionIdentity(request.goal_id);
  const agent = validateDirectionIdentity(request.agent_id);
  const generated = text(request.generated_at, "direction_input_invalid");
  timestamp(generated, "direction_input_invalid");
  const result = empty(goal, agent, generated);
  try {
    capacity(bytes(request), GOAL_DIRECTION_LIMITS.input_bytes);
    const authority = row(registry, "authority_registry_missing");
    const declarations = requirements(authority.goal_direction_requirements);
    const materialIndex = materials(authority.project_materials);
    const topicIndex = topics(authority.topic_authority);
    const todo = optionalText(request.todo_id, "direction_input_invalid");
    const allReceipts = receipts(request.receipts);
    const selectedReceipts = allReceipts.filter(r => r.goal_id === goal && r.agent_id === agent && r.todo_id === todo);
    const boundaries = request.available_boundaries ?? [];
    if (!Array.isArray(boundaries)) refuse("direction_input_invalid");
    const allowed = new Set(["public", "public_safe", ...boundaries.map(b => text(b, "direction_input_invalid", 80))]);
    const resolved = new Map<string, Requirement[]>();
    for (const declaration of declarations) {
      const ids = declaration.material_id ? [declaration.material_id] : topicIndex.get(declaration.topic!);
      if (!ids?.length) refuse("direction_requirement_unresolved");
      for (const id of ids) resolved.set(id, [...(resolved.get(id) ?? []), declaration]);
    }
    capacity(resolved.size, GOAL_DIRECTION_LIMITS.materials);
    const basis: unknown[] = [];
    let blocked = false, drift = false;
    for (const id of [...resolved.keys()].sort(compare)) {
      const bindings = resolved.get(id)!;
      const required = bindings.some(binding => binding.required);
      const material = materialIndex.get(id);
      const revision = optionalText(material?.revision, "authority_registry_invalid");
      const metadata = Object.fromEntries(["role", "owner_status", "conflict_rule", "boundary", "gate_status", "freshness"].map(key => [key, optionalText(material?.[key], "authority_registry_invalid")]));
      const materialTopics = [...topicIndex].filter(([, ids]) => ids.includes(id)).map(([topic]) => topic).sort(compare);
      basis.push({material_id: id, topics: materialTopics, revision, ...metadata});
      const receipt = selectReceipt(selectedReceipts, id);
      const item: DirectionItem = {material_id: id, bound_by: bindings.map(b => b.requirement_id), required, topics: materialTopics,
        required_revision: revision, observed_revision: receipt?.observed_revision ?? null,
        state: "current", reason_codes: [], receipt_ref: receipt ? `material_receipt:${receipt.receipt_id}` : null};
      let reason: DirectionReason | null = null;
      if (!material || !revision) {
        item.state = "missing"; reason = !material ? "direction_requirement_unresolved" : "authority_revision_missing";
      } else if (!metadata.boundary || metadata.boundary === "unknown" || !allowed.has(metadata.boundary)
          || !["ready", "registered", "readable"].includes(metadata.gate_status ?? "") || receipt?.outcome === "unavailable") {
        item.state = "inaccessible"; reason = "material_inaccessible";
      } else if (metadata.owner_status !== "reviewed" || metadata.conflict_rule !== "owner_wins") {
        item.state = "stale"; reason = "authority_conflict";
      } else if (metadata.freshness !== "current") {
        item.state = "stale"; reason = "authority_freshness_stale";
      } else if (!receipt) {
        item.state = "required_unread"; reason = "material_required_unread";
      } else if (receipt.observed_revision !== revision) {
        item.state = "stale"; reason = "material_revision_changed";
      }
      if (reason) item.reason_codes.push(reason);
      if (required) {
        result.summary.required_count++;
        if (item.state === "current") result.summary.current_count++;
        if (item.state === "stale") result.summary.stale_count++;
        if (reason && reason !== "material_revision_changed") { blocked = true; result.summary.blocked_count++; }
        if (reason === "material_revision_changed") drift = true;
        if (reason) result.reason_codes.push(reason);
      }
      result.items.push(item);
    }
    result.reason_codes = [...new Set(result.reason_codes)].sort(compare);
    result.direction_state = blocked ? "blocked" : drift ? "re_evaluation_required" : "current";
    if (result.direction_state === "re_evaluation_required") result.advisory = {
      kind: "agent_vision_re_evaluation", creates_work: false, rewrites_vision: false, changes_goal_route: false,
    };
    result.baseline_digest = `sha256:${createHash("sha256").update(canonical({schema_version: GOAL_DIRECTION_SCHEMA,
      goal_id: goal, requirements: declarations, materials: basis})).digest("hex")}`;
    capacity(bytes(result), GOAL_DIRECTION_LIMITS.output_bytes);
    return result;
  } catch (error) {
    if (!(error instanceof DirectionBaselineInputError)) throw error;
    return {...empty(goal, agent, generated), reason_codes: [error.reason]};
  }
}
