/** Read-only standing decisions. Storage order is not decision chronology;
 * this projection neither grants a lease nor commits an owner decision. */
import type {JsonObject} from "../effect_program.ts";
import {parseTodoTimestampMicros} from "../runtime_timestamp.ts";
import {authorityUnicodeCompare} from "../coordination/authority_store_codec.ts";
import {todoPresentationMetadata} from "../coordination/todo_presentation.ts";

const SCOPE_KINDS = new Set(["private_read", "write_scope", "resource", "production", "public_claim", "direction", "other"]);
const BROAD_SCOPES = new Set(["goal", "project", "global"]);
// The existing decision_scope_v0 wire grammar; not a prose classifier.
const SCOPE_KEY = /^(?:\*|[a-z0-9][a-z0-9_.:@*/-]{0,95})$/u;
const OUTCOMES = new Set(["approve", "reject", "cancel"]);
const object = (value: unknown): value is JsonObject => value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): string => typeof value === "string" ? value.trim() : "";

/** Shared with archive selection: only explicit, broad, unlinked user gates.
 * Notification heuristics are deliberately not a source of standing authority. */
export function isStandingDecisionReceipt(todo: JsonObject): boolean {
  const scope = todo.decision_scope;
  return todo.role === "user" && todo.task_class === "user_gate" && todo.status === "done" &&
    (todo.unblocks_todo_id == null || todo.unblocks_todo_id === "") && object(scope) && SCOPE_KINDS.has(String(scope.kind)) &&
    BROAD_SCOPES.has(String(scope.granularity)) && typeof scope.scope_key === "string" && SCOPE_KEY.test(scope.scope_key) &&
    OUTCOMES.has(String(todo.decision_outcome)) &&
    (todo.global_gate === true || Boolean(text(todo.blocks_agent)));
}

interface Candidate {
  readonly item: JsonObject;
  readonly receipt: JsonObject;
  readonly time: bigint | null;
  readonly hasTime: boolean;
}

function candidate(item: JsonObject): Candidate {
  const scope = item.decision_scope as JsonObject;
  const normalizedScope: JsonObject = {schema_version: "decision_scope_v0", kind: scope.kind,
    granularity: scope.granularity, scope_key: scope.scope_key,
    ...(text(scope.decision_id) ? {decision_id: scope.decision_id} : {})};
  const completed = text(item.completed_at), updated = text(item.updated_at);
  // Editing receipt prose must not make an older decision newer.
  const at = completed || updated;
  const receipt: JsonObject = {schema_version: "standing_decision_receipt_v0",
    source_todo_id: item.todo_id, decision_scope: normalizedScope,
    outcome: item.decision_outcome, active: item.decision_outcome === "approve",
    global_gate: item.global_gate === true};
  if (text(item.blocks_agent)) receipt.blocks_agent = item.blocks_agent;
  if (completed) receipt.completed_at = completed;
  if (updated) receipt.updated_at = updated;
  return {item, receipt, time: at ? parseTodoTimestampMicros(at) : null, hasTime: Boolean(at)};
}

function deterministic(candidates: readonly Candidate[]): Candidate {
  return [...candidates].sort((a, b) => authorityUnicodeCompare(String(a.item.todo_id), String(b.item.todo_id))).at(-1)!;
}

function latest(candidates: readonly Candidate[], legacySourceOrder: boolean): Candidate | null {
  if (candidates.length === 1) return candidates[0]!;
  if (candidates.every(c => c.time !== null)) {
    const max = candidates.reduce((at, c) => c.time! > at ? c.time! : at, candidates[0]!.time!);
    const newest = candidates.filter(c => c.time === max);
    return new Set(newest.map(c => c.receipt.outcome)).size === 1 ? deterministic(newest) : null;
  }
  if (candidates.every(c => !c.hasTime)) {
    if (legacySourceOrder) return candidates.at(-1)!;
    // v0 records can retain actual source positions; never use synthetic
    // display indexes assigned to native records by a downstream adapter.
    const presentation = candidates.map(c => todoPresentationMetadata(c.item));
    const indexed = presentation.every(metadata =>
      (metadata.order_source === "source_index" || metadata.order_source === "legacy_index") &&
      metadata.display_order !== null);
    if (indexed && new Set(presentation.map(metadata => metadata.display_section)).size === 1 &&
        new Set(presentation.map(metadata => metadata.display_order)).size === candidates.length) {
      return [...candidates].sort((a, b) =>
        todoPresentationMetadata(a.item).display_order! -
        todoPresentationMetadata(b.item).display_order!).at(-1)!;
    }
  }
  // Missing, invalid or mixed chronology cannot silently choose approval.
  // Equal outcomes are unambiguous; choosing a representative is only display.
  return new Set(candidates.map(c => c.receipt.outcome)).size === 1 ? deterministic(candidates) : null;
}

export function projectStandingDecisions(items: readonly JsonObject[], legacySourceOrder = false): JsonObject | null {
  const groups = new Map<string, Candidate[]>();
  for (const item of items) {
    if (!isStandingDecisionReceipt(item) || !text(item.todo_id)) continue;
    const scope = item.decision_scope as JsonObject;
    const identity = JSON.stringify([scope.kind, scope.granularity, scope.scope_key,
      item.global_gate === true ? "global" : `agent:${item.blocks_agent}`]);
    const group = groups.get(identity) ?? [];
    group.push(candidate(item));
    groups.set(identity, group);
  }
  if (!groups.size) return null;
  const entries: JsonObject[] = [], conflicts: JsonObject[] = [];
  const ordered = legacySourceOrder ? [...groups] : [...groups].sort(([a], [b]) => authorityUnicodeCompare(a, b));
  for (const [, group] of ordered) {
    const selected = latest(group, legacySourceOrder);
    if (selected) entries.push(selected.receipt);
    else {
      const first = group[0]!.receipt;
      conflicts.push({reason_code: "standing_decision_order_unresolved", decision_scope: first.decision_scope,
        global_gate: first.global_gate, ...(first.blocks_agent ? {blocks_agent: first.blocks_agent} : {}),
        source_todo_ids: group.map(c => String(c.item.todo_id)).sort(authorityUnicodeCompare)});
    }
  }
  return {schema_version: "standing_decision_authority_v0", active_count: entries.filter(e => e.active).length,
    inactive_count: entries.filter(e => !e.active).length, entries,
    ...(conflicts.length ? {conflict_count: conflicts.length, conflicts} : {})};
}
