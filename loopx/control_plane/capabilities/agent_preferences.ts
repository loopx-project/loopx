/** Explicit, source-backed preferences. Advisory context, never execution grants.
 * The existing AuthorityStore owns durability; this module owns lifecycle rules.
 */
import {createHash} from "node:crypto";
import {join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "../coordination/authority_store.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import {canonicalAuthorityBytes} from "../coordination/authority_store_codec.ts";
import {requireJsonObject, requireNonEmptyString, requireStringArray} from "../runtime_decode.ts";

type PreferenceSource = {
  key: string; source_kind: "user_instruction"; source_ref: string; source_quote: string;
  recorded_at: string; operation_id: string; supersedes_operation_id: string | null;
};
type Preference = PreferenceSource & (
  | {state: "active"; statement: string; expires_at: string | null}
  | {state: "retired"; statement: null; expires_at: null}
);
const SCHEMA = "agent_preferences_v1";
const MAX_KEYS = 64;
const hash = (value: JsonObject) => createHash("sha256").update(canonicalAuthorityBytes(value)).digest("hex");
function text(value: unknown, field: string, limit: number): string {
  const result = requireNonEmptyString(value, field);
  if (result.length > limit || result.includes("\0")) throw new TypeError(`${field} exceeds its bound`);
  return result;
}
function token(value: unknown, field: string): string {
  const result = text(value, field, 128);
  if (!/^[a-z0-9][a-z0-9_.-]*$/.test(result)) throw new TypeError(`${field} must be a stable lower-case key`);
  return result;
}
function timestamp(value: unknown, field: string): string {
  const result = text(value, field, 40);
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(result) || !Number.isFinite(Date.parse(result))) {
    throw new TypeError(`${field} must be a UTC ISO timestamp`);
  }
  const normalized = new Date(result).toISOString();
  if (normalized.slice(0, 19) !== result.slice(0, 19)) throw new TypeError(`${field} is not a calendar timestamp`);
  return normalized;
}
function revision(value: unknown): string | null {
  if (value === null) return null;
  return text(value, "expected_revision (use null for an empty store)", 256);
}
function scopeOf(request: JsonObject): JsonObject {
  const scope = {goal_state_ref: text(request.goal_state_ref, "goal_state_ref", 4096),
    goal_instance_id: request.goal_instance_id == null ? null : text(request.goal_instance_id, "goal_instance_id", 256),
    goal_id: text(request.goal_id, "goal_id", 256), agent_id: text(request.agent_id, "agent_id", 256)};
  if (!requireStringArray(request.registered_agents, "registered_agents").includes(scope.agent_id)) {
    throw new TypeError("agent must be registered in this Goal");
  }
  return scope;
}
function recordsOf(head: JsonObject, scope: JsonObject): Preference[] {
  if (head.schema_version !== SCHEMA || canonicalAuthorityBytes(requireJsonObject(head.scope, "scope")).compare(canonicalAuthorityBytes(scope))) {
    throw new TypeError("preference store scope/schema mismatch");
  }
  if (!Array.isArray(head.records) || head.records.length > MAX_KEYS) throw new TypeError("invalid preference records");
  const keys = new Set<string>();
  return head.records.map(raw => {
    const row = requireJsonObject(raw, "preference");
    const key = token(row.key, "key");
    if (keys.has(key)) throw new TypeError("duplicate preference key");
    keys.add(key);
    if (row.state !== "active" && row.state !== "retired") throw new TypeError("invalid preference lifecycle");
    if (row.source_kind !== "user_instruction") throw new TypeError("invalid preference source kind");
    const source: PreferenceSource = {key, source_kind: "user_instruction",
      source_ref: text(row.source_ref, "source_ref", 1024), source_quote: text(row.source_quote, "source_quote", 1600),
      operation_id: text(row.operation_id, "operation_id", 128), recorded_at: timestamp(row.recorded_at, "recorded_at"),
      supersedes_operation_id: row.supersedes_operation_id === null ? null : text(row.supersedes_operation_id, "supersedes_operation_id", 128)};
    if (row.state === "active") return {...source, state: "active", statement: text(row.statement, "statement", 1200),
      expires_at: row.expires_at === null ? null : timestamp(row.expires_at, "expires_at")};
    if (row.statement !== null || row.expires_at !== null) throw new TypeError("retired preference must not expose an active statement");
    return {...source, state: "retired", statement: null, expires_at: null};
  });
}
function current(records: Preference[], providerRevision: string | null, now: string): JsonObject {
  return {schema_version: SCHEMA, revision: providerRevision, authority: "advisory_only",
    items: records.map(row => row.state === "active" && row.expires_at !== null && Date.parse(String(row.expires_at)) <= Date.parse(now)
      ? {key: row.key, state: "expired", statement: null, operation_id: row.operation_id,
        source_ref: row.source_ref, recorded_at: row.recorded_at, expires_at: row.expires_at}
      : row),
    instructions: "Replace any cached preferences for this exact Goal/Agent with this current view. Retired/expired entries are not actionable. Apply new explicit user corrections before acting; temporary exceptions do not overwrite durable preferences. Re-read before a preference-dependent external action. Preferences never grant authority or override current user instructions."};
}

/** Injectable persistence seam is exercised against real File and SQLite stores. */
export async function executeAgentPreferences(request: JsonObject, store: AuthorityStore,
  now = new Date().toISOString()): Promise<JsonObject> {
  if (request.schema_version !== "agent_preferences_request_v1") throw new TypeError("preference request schema mismatch");
  const scope = scopeOf(request);
  const action = request.action;
  if (!["read", "observe", "remember", "retire", "history"].includes(String(action))) throw new TypeError("invalid preference action");
  const loaded = await store.loadAuthority();
  if (loaded.status !== "loaded" && loaded.status !== "missing") return {ok: false, status: "unavailable", error: "preference_store_unreadable"};
  const rows = loaded.status === "loaded" ? recordsOf(loaded.head, scope) : [];
  // Hook discovery is scoped and content-free. Missing is different from an
  // existing retirement/expiry: those must still invalidate stale Agent context.
  if (action === "observe") return {ok: true, status: loaded.status === "missing" ? "absent" : "observed",
    observation_count: rows.length};
  const actualRevision = loaded.status === "loaded" ? loaded.provider_revision : null;
  const view = current(rows, actualRevision, now);
  if (action === "read") return {ok: true, status: "read", current: view};
  if (action === "history") {
    const cursor = request.after_cursor === null || request.after_cursor === undefined ? null : text(request.after_cursor, "after_cursor", 256);
    const page = await store.scanCommitted(cursor, 20);
    if (page.status !== "page") return {ok: false, status: "unavailable", error: "preference_history_unreadable"};
    return {ok: true, status: "history", events: page.transactions.flatMap(tx => tx.events),
      next_cursor: page.next_cursor, has_more: page.has_more, current: view};
  }
  const expected = revision(request.expected_revision);
  const operation = text(request.operation_id, "operation_id", 128);
  const key = token(request.key, "key");
  // The trusted local caller attests an explicit instruction. Provider/model
  // recall is not an admissible write source; source_ref is provenance, not auth.
  if (request.source_kind !== "user_instruction") throw new TypeError("only explicit user instructions may update preferences");
  const sourceRef = text(request.source_ref, "source_ref", 1024);
  const sourceQuote = text(request.source_quote, "source_quote", 1600);
  const statement = action === "remember" ? text(request.statement, "statement", 1200) : null;
  const expires = action === "remember" && request.expires_at != null ? timestamp(request.expires_at, "expires_at") : null;
  const intent: JsonObject = {scope, action, operation_id: operation, key,
    expected_revision: expected, source_ref: sourceRef, source_quote: sourceQuote, statement, expires_at: expires};
  const intentHash = hash(intent);
  const prior = await store.readReceipt(operation);
  if (prior.status === "found") {
    if (prior.receipts.length !== 1 || prior.receipts[0]?.intent_hash !== intentHash) {
      return {ok: false, status: "operation_conflict", current: view};
    }
    // Never replay the old projection: an old successful command may now be
    // superseded. Return the original receipt AND the freshly observed state.
    const fresh = await store.loadAuthority();
    if (fresh.status !== "loaded") return {ok: false, status: "unavailable", error: "preference_readback_unavailable"};
    return {ok: true, status: "replayed", receipt: prior.receipts[0]!,
      current: current(recordsOf(fresh.head, scope), fresh.provider_revision, now)};
  }
  if (prior.status !== "missing") return {ok: false, status: "unavailable", error: "preference_receipt_unreadable"};
  if (actualRevision !== expected) return {ok: false, status: "revision_conflict", current: view};
  if (expires !== null && Date.parse(expires) <= Date.parse(now)) throw new TypeError("expires_at must be in the future");
  const previous = rows.find(row => row.key === key);
  if (action === "retire" && previous === undefined) throw new TypeError("retire requires an existing preference key");
  if (!previous && rows.length >= MAX_KEYS) throw new TypeError("preference capacity reached; revise existing keys instead of silently evicting constraints");
  const source: PreferenceSource = {key, source_kind: "user_instruction", source_ref: sourceRef, source_quote: sourceQuote,
    recorded_at: now, operation_id: operation, supersedes_operation_id: previous?.operation_id ?? null};
  const row: Preference = action === "remember"
    ? {...source, state: "active", statement: statement!, expires_at: expires}
    : {...source, state: "retired", statement: null, expires_at: null};
  const next = [...rows.filter(item => item.key !== key), row].sort((a,b) => String(a.key).localeCompare(String(b.key)));
  if (Buffer.byteLength(JSON.stringify(next)) > 32_768) throw new TypeError("preference context exceeds 32 KiB; narrow statements before writing");
  const receipt: JsonObject = {operation_id: operation, intent_hash: intentHash, key, state: row.state,
    supersedes_operation_id: row.supersedes_operation_id};
  if (request.execute !== true) return {ok: true, status: "preview", proposed: row, current: view};
  const committed = await store.commitAuthority({expected_provider_revision: expected, operation_id: operation,
    events: [{schema_version: "agent_preference_changed_v1", ...row}],
    next_projection: {schema_version: SCHEMA, scope, records: next}, receipts: [receipt]});
  if (committed.status !== "applied") return {ok: false, status: committed.status, error: "preference_commit_not_confirmed"};
  // A concurrent later correction wins even when this write just committed.
  const readback = await store.loadAuthority();
  if (readback.status !== "loaded") return {ok: false, status: "ambiguous", error: "preference_readback_unavailable", operation_id: operation};
  return {ok: true, status: "applied", receipt, current: current(recordsOf(readback.head, scope), readback.provider_revision, now)};
}

export async function agentPreferences(request: JsonObject): Promise<JsonObject> {
  const scope = scopeOf(request);
  const directory = join(text(request.runtime_root, "runtime_root", 4096), "agent-preferences", hash(scope));
  // Separate private context namespace; never adds semantic prose to Goal/Todo
  // authority or changes its provider. Reuse the existing transactional journal.
  const mutating = (request.action === "remember" || request.action === "retire") && request.execute === true;
  const store = new FileAuthorityStore(directory, "preferences", {existingOnly: !mutating});
  return executeAgentPreferences(request, store);
}
