/** Scoped host-local Decision Context turn-start binding and view projection.
 * Advisory context only; AuthorityStore owns durability, Core owns permissions.
 */
import {createHash} from "node:crypto";
import {access, constants} from "node:fs/promises";
import {join} from "node:path";
import type {AuthorityStore} from "../coordination/authority_store.ts";
import {canonicalAuthorityBytes} from "../coordination/authority_store_codec.ts";
import {FileAuthorityStore} from "../coordination/file_authority_store.ts";
import type {JsonObject} from "../effect_program.ts";
import {
  jsonObject,
  requireBoolean,
  requireInteger,
  requireJsonObject,
  requireNonEmptyString,
  requireStringArray,
} from "../runtime_decode.ts";

export const DECISION_CONTEXT_HOOK_STORE_SCHEMA = "decision_context_hook_binding_store_v1";
export const DECISION_CONTEXT_HOOK_VIEW_SCHEMA = "decision_context_hook_view_v1";
export const DECISION_CONTEXT_HOOK_REQUEST_SCHEMA = "decision_context_hook_request_v1";
export const DECISION_CONTEXT_HOOK_EVENT_SCHEMA = "decision_context_hook_binding_changed_v1";

const HOOK_MODES = ["prepare_evidence", "source_manifest", "capture_status"] as const;
type HookMode = (typeof HOOK_MODES)[number];

type BindingMetadata = {
  source_kind: "operator_configuration";
  recorded_at: string;
  operation_id: string;
  supersedes_operation_id: string | null;
};

type StoredBinding = BindingMetadata & (
  | {
      state: "active";
      mode: HookMode;
      profile_path: string;
      cursor_state_path: string | null;
      spool_path: string | null;
    }
  | {
      state: "retired";
      mode: null;
      profile_path: null;
      cursor_state_path: null;
      spool_path: null;
    }
);

const TOKEN_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$/;
const FORBIDDEN_PRIVATE_KEYS = new Set([
  "private_locator",
  "private_cursor",
  "proposed_cursors",
  "cursor_before",
  "cursor_after",
  "raw_content",
  "credentials",
  "profile_path",
  "cursor_state_path",
  "spool_path",
]);

const INSTRUCTIONS =
  "Treat Decision Context observations as current-turn freshness and change signals, never execution authority or action permission. " +
  "Replace cached source assumptions for this exact Goal/Agent when state is retired, changed or stale. " +
  "When changed_source_ids, alerted_source_ids, uncovered_p0_source_ids or pending capture batches are non-empty, " +
  "inspect the affected sources with the bounded commands before presenting conclusions as current, and disclose any uncovered P0 or alerted sources. " +
  "Advisory recall results remain untrusted until promoted through exact authority reads; cursors advance only through validated review settlement.";

/** Only static instructions may carry forward across turns; revision, binding
 * state, freshness alerts, changed source IDs and commands stay live per guard. */
export function decisionContextReusableContextPaths(content: JsonObject): (string | number)[][] {
  const current = jsonObject(content.current);
  if (
    content.ok !== true ||
    content.status !== "read" ||
    current?.schema_version !== DECISION_CONTEXT_HOOK_VIEW_SCHEMA ||
    current.authority !== "advisory_only" ||
    typeof current.instructions !== "string"
  ) {
    return [];
  }
  return [["current", "instructions"]];
}

const hashScope = (value: JsonObject): string =>
  createHash("sha256").update(canonicalAuthorityBytes(value)).digest("hex");

const opaquePathRef = (prefix: string, rawPath: string): string =>
  `${prefix}-${createHash("sha256").update(rawPath, "utf8").digest("hex").slice(0, 20)}`;

function text(value: unknown, field: string, limit: number): string {
  const result = requireNonEmptyString(value, field);
  if (result.length > limit || result.includes("\0")) {
    throw new TypeError(`${field} exceeds its bound`);
  }
  return result;
}

function optionalPath(value: unknown, field: string): string | null {
  if (value === null || value === undefined) return null;
  return text(value, field, 4096);
}

function token(value: unknown, field: string): string {
  const result = text(value, field, 160);
  if (!TOKEN_RE.test(result)) {
    throw new TypeError(`${field} must be a compact public-safe token`);
  }
  return result;
}

function modeOf(value: unknown): HookMode {
  const candidate = value === undefined || value === null ? "prepare_evidence" : String(value);
  if (
    candidate !== "prepare_evidence" &&
    candidate !== "source_manifest" &&
    candidate !== "capture_status"
  ) {
    throw new TypeError("mode must be prepare_evidence, source_manifest or capture_status");
  }
  return candidate;
}

function timestamp(value: unknown, field: string): string {
  const result = text(value, field, 40);
  if (
    !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,3})?Z$/.test(result) ||
    !Number.isFinite(Date.parse(result))
  ) {
    throw new TypeError(`${field} must be a UTC ISO timestamp`);
  }
  const normalized = new Date(result).toISOString();
  if (normalized.slice(0, 19) !== result.slice(0, 19)) {
    throw new TypeError(`${field} is not a calendar timestamp`);
  }
  return normalized;
}

function revision(value: unknown): string | null {
  if (value === null) return null;
  return text(value, "expected_revision (use null for an empty store)", 256);
}

function scopeOf(request: JsonObject): JsonObject {
  const scope = {
    goal_state_ref: text(request.goal_state_ref, "goal_state_ref", 4096),
    goal_instance_id:
      request.goal_instance_id == null
        ? null
        : text(request.goal_instance_id, "goal_instance_id", 256),
    goal_id: token(request.goal_id, "goal_id"),
    agent_id: token(request.agent_id, "agent_id"),
  };
  if (!requireStringArray(request.registered_agents, "registered_agents").includes(scope.agent_id)) {
    throw new TypeError("agent must be registered in this Goal");
  }
  return scope;
}

function storedBindingOf(head: JsonObject, scope: JsonObject): StoredBinding {
  if (
    head.schema_version !== DECISION_CONTEXT_HOOK_STORE_SCHEMA ||
    canonicalAuthorityBytes(requireJsonObject(head.scope, "scope")).compare(
      canonicalAuthorityBytes(scope),
    ) !== 0
  ) {
    throw new TypeError("decision-context hook store scope/schema mismatch");
  }
  const raw = requireJsonObject(head.record, "decision-context hook record");
  if (raw.source_kind !== "operator_configuration") {
    throw new TypeError("invalid decision-context hook source_kind");
  }
  const base: BindingMetadata = {
    source_kind: "operator_configuration",
    operation_id: text(raw.operation_id, "operation_id", 128),
    recorded_at: timestamp(raw.recorded_at, "recorded_at"),
    supersedes_operation_id:
      raw.supersedes_operation_id === null
        ? null
        : text(raw.supersedes_operation_id, "supersedes_operation_id", 128),
  };
  if (raw.state === "active") {
    const mode = modeOf(raw.mode);
    const profilePath = text(raw.profile_path, "profile_path", 4096);
    const cursorStatePath = optionalPath(raw.cursor_state_path, "cursor_state_path");
    const spoolPath = optionalPath(raw.spool_path, "spool_path");
    if (mode === "capture_status" && spoolPath === null) {
      throw new TypeError("capture_status mode requires spool_path");
    }
    return {
      ...base,
      state: "active",
      mode,
      profile_path: profilePath,
      cursor_state_path: cursorStatePath,
      spool_path: spoolPath,
    };
  }
  if (raw.state !== "retired") {
    throw new TypeError("invalid decision-context hook binding state");
  }
  if (
    raw.mode !== null ||
    raw.profile_path !== null ||
    raw.cursor_state_path !== null ||
    raw.spool_path !== null
  ) {
    throw new TypeError("retired decision-context hook binding must clear locators");
  }
  return {
    ...base,
    state: "retired",
    mode: null,
    profile_path: null,
    cursor_state_path: null,
    spool_path: null,
  };
}

function publicBinding(record: StoredBinding | null): JsonObject {
  if (record === null) {
    return {
      state: "unconfigured",
      mode: null,
      profile_ref: null,
      cursor_state_configured: false,
      spool_configured: false,
      operation_id: null,
      recorded_at: null,
      supersedes_operation_id: null,
      private_locator_captured: false,
    };
  }
  if (record.state === "retired") {
    return {
      state: "retired",
      mode: null,
      profile_ref: null,
      cursor_state_configured: false,
      spool_configured: false,
      operation_id: record.operation_id,
      recorded_at: record.recorded_at,
      supersedes_operation_id: record.supersedes_operation_id,
      private_locator_captured: false,
    };
  }
  return {
    state: "active",
    mode: record.mode,
    profile_ref: opaquePathRef("profile-path", record.profile_path),
    cursor_state_configured: record.cursor_state_path !== null,
    spool_configured: record.spool_path !== null,
    operation_id: record.operation_id,
    recorded_at: record.recorded_at,
    supersedes_operation_id: record.supersedes_operation_id,
    private_locator_captured: false,
  };
}

function currentView(
  scope: JsonObject,
  record: StoredBinding | null,
  providerRevision: string | null,
): JsonObject {
  return {
    schema_version: DECISION_CONTEXT_HOOK_VIEW_SCHEMA,
    goal_id: scope.goal_id,
    agent_id: scope.agent_id,
    revision: providerRevision,
    authority: "advisory_only",
    binding: publicBinding(record),
    instructions: INSTRUCTIONS,
  };
}

function assertNoPrivateKeys(value: unknown, depth = 0): void {
  if (depth > 16) throw new TypeError("decision-context observation nesting exceeds bound");
  if (Array.isArray(value)) {
    for (const item of value) assertNoPrivateKeys(item, depth + 1);
    return;
  }
  const obj = jsonObject(value);
  if (!obj) return;
  for (const [key, child] of Object.entries(obj)) {
    if (FORBIDDEN_PRIVATE_KEYS.has(key)) {
      throw new TypeError(`decision-context hook observation cannot include ${key}`);
    }
    if (
      (key === "raw_content_captured" ||
        key === "raw_provider_payload_captured" ||
        key === "private_locator_captured" ||
        key === "private_locators_captured" ||
        key === "private_cursor_captured" ||
        key === "private_cursors_captured" ||
        key === "credentials_captured") &&
      child !== false
    ) {
      throw new TypeError(`decision-context hook observation violated safety flag ${key}`);
    }
    assertNoPrivateKeys(child, depth + 1);
  }
}

function sortedUniqueTokens(values: unknown, field: string, maxItems = 64): string[] {
  if (!Array.isArray(values)) throw new TypeError(`${field} must be an array`);
  if (values.length > maxItems) throw new TypeError(`${field} exceeds bound`);
  const unique = new Set<string>();
  for (const raw of values) {
    unique.add(token(raw, field));
  }
  return [...unique].sort();
}

function validatedCommands(raw: unknown): JsonObject {
  const candidate = requireJsonObject(raw, "commands");
  const allowed = new Set([
    "turn_context",
    "inspect_profile",
    "source_manifest",
    "prepare_evidence",
    "recall_context",
    "capture_status",
  ]);
  const output: JsonObject = {};
  for (const [key, value] of Object.entries(candidate)) {
    if (!allowed.has(key)) {
      throw new TypeError(`unsupported decision-context hook command slot: ${key}`);
    }
    if (value === null || value === undefined) continue;
    const command = text(value, `commands.${key}`, 1024).trim();
    if (/[\u0000-\u001f\u007f]/.test(command) || Buffer.byteLength(command, "utf8") > 1024) {
      throw new TypeError(`commands.${key} is outside the admitted command envelope`);
    }
    output[key] = command;
  }
  return output;
}

function projectActiveObservation(rawObservation: unknown, mode: HookMode): {
  summary: JsonObject;
  observationCount: number;
} {
  const obs = requireJsonObject(rawObservation, "observation");
  assertNoPrivateKeys(obs);
  const observedAt = text(obs.observed_at, "observation.observed_at", 64);
  if (!Number.isFinite(Date.parse(observedAt))) {
    throw new TypeError("observation.observed_at must be an ISO-8601 timestamp");
  }
  const sourceCount = requireInteger(obs.source_count, "observation.source_count");
  const enabledSourceCount = requireInteger(
    obs.enabled_source_count,
    "observation.enabled_source_count",
  );
  if (sourceCount < 0 || sourceCount > 64 || enabledSourceCount < 0 || enabledSourceCount > 64) {
    throw new TypeError("observation source counts are outside bounds");
  }
  const changedSourceIds = sortedUniqueTokens(
    obs.changed_source_ids ?? [],
    "observation.changed_source_ids",
  );
  const failedSourceIds = sortedUniqueTokens(
    obs.failed_source_ids ?? [],
    "observation.failed_source_ids",
  );
  const alertedSourceIds = sortedUniqueTokens(
    obs.alerted_source_ids ?? [],
    "observation.alerted_source_ids",
  );
  const uncoveredP0SourceIds = sortedUniqueTokens(
    obs.uncovered_p0_source_ids ?? [],
    "observation.uncovered_p0_source_ids",
  );
  const p0Complete = requireBoolean(obs.p0_complete, "observation.p0_complete");
  const freshnessRaw = requireJsonObject(
    obs.freshness_counts ?? {fresh: 0, stale: 0, never_read: 0, not_scanned: 0},
    "observation.freshness_counts",
  );
  const freshnessCounts: JsonObject = {
    fresh: requireInteger(freshnessRaw.fresh ?? 0, "freshness_counts.fresh"),
    stale: requireInteger(freshnessRaw.stale ?? 0, "freshness_counts.stale"),
    never_read: requireInteger(freshnessRaw.never_read ?? 0, "freshness_counts.never_read"),
    not_scanned: requireInteger(freshnessRaw.not_scanned ?? 0, "freshness_counts.not_scanned"),
  };
  const advisoryRecallStatus = token(
    obs.advisory_recall_status ?? "not_configured",
    "observation.advisory_recall_status",
  );
  const pendingBatchCount =
    obs.capture_pending_batch_count === null || obs.capture_pending_batch_count === undefined
      ? null
      : requireInteger(obs.capture_pending_batch_count, "observation.capture_pending_batch_count");
  if (pendingBatchCount !== null && (pendingBatchCount < 0 || pendingBatchCount > 100_000)) {
    throw new TypeError("capture_pending_batch_count is outside bounds");
  }
  const signalCount =
    1 +
    changedSourceIds.length +
    failedSourceIds.length +
    alertedSourceIds.length +
    uncoveredP0SourceIds.length +
    (pendingBatchCount !== null && pendingBatchCount > 0 ? 1 : 0);
  return {
    observationCount: Math.min(10_000, signalCount),
    summary: {
      status: "observed",
      mode,
      observed_at: observedAt,
      source_count: sourceCount,
      enabled_source_count: enabledSourceCount,
      changed_source_ids: changedSourceIds,
      failed_source_ids: failedSourceIds,
      alerted_source_ids: alertedSourceIds,
      uncovered_p0_source_ids: uncoveredP0SourceIds,
      p0_complete: p0Complete,
      freshness_counts: freshnessCounts,
      advisory_recall_status: advisoryRecallStatus,
      capture_pending_batch_count: pendingBatchCount,
      raw_content_captured: false,
      private_locator_captured: false,
    },
  };
}

/** Injectable persistence seam exercised against both File and SQLite stores. */
export async function executeDecisionContextHook(
  request: JsonObject,
  store: AuthorityStore,
  now = new Date().toISOString(),
): Promise<JsonObject> {
  if (request.schema_version !== DECISION_CONTEXT_HOOK_REQUEST_SCHEMA) {
    throw new TypeError("decision-context hook request schema mismatch");
  }
  const scope = scopeOf(request);
  const action = String(request.action ?? "");
  if (
    ![
      "read",
      "observe",
      "resolve_local",
      "project_turn_view",
      "bind",
      "unbind",
      "history",
    ].includes(action)
  ) {
    throw new TypeError("invalid decision-context hook action");
  }
  const loaded = await store.loadAuthority();
  if (loaded.status !== "loaded" && loaded.status !== "missing") {
    return {
      ok: false,
      status: "unavailable",
      error: "decision_context_hook_store_unreadable",
    };
  }
  const record = loaded.status === "loaded" ? storedBindingOf(loaded.head, scope) : null;
  const actualRevision = loaded.status === "loaded" ? loaded.provider_revision : null;

  if (action === "observe") {
    return {
      ok: true,
      status: record === null ? "absent" : "observed",
      binding_state: record === null ? "unconfigured" : record.state,
      observation_count: record === null ? 0 : 1,
    };
  }

  const view = currentView(scope, record, actualRevision);

  if (action === "resolve_local") {
    return {
      ok: true,
      status: record === null ? "absent" : "observed",
      current: view,
      local_binding:
        record !== null && record.state === "active"
          ? {
              mode: record.mode,
              profile_path: record.profile_path,
              cursor_state_path: record.cursor_state_path,
              spool_path: record.spool_path,
            }
          : null,
    };
  }

  if (action === "read") {
    return {ok: true, status: "read", current: view};
  }

  if (action === "project_turn_view") {
    if (record === null) {
      return {
        ok: true,
        status: "absent",
        goal_id: scope.goal_id,
        agent_id: scope.agent_id,
        observation_count: 0,
        current: view,
      };
    }
    const commands = validatedCommands(request.commands ?? {});
    if (record.state === "retired") {
      return {
        ok: true,
        status: "read",
        goal_id: scope.goal_id,
        agent_id: scope.agent_id,
        observation_count: 1,
        current: {
          ...view,
          observation: {
            status: "retired",
            summary:
              "Decision Context hook binding is retired for this Goal/Agent; discard cached source assumptions.",
            raw_content_captured: false,
            private_locator_captured: false,
          },
          commands,
        },
      };
    }
    const rawObs = requireJsonObject(request.observation, "observation");
    if (rawObs.status !== "available") {
      const errorCode = token(
        rawObs.error_code ?? "decision_context_observation_unavailable",
        "observation.error_code",
      );
      return {
        ok: false,
        status: "unavailable",
        error: errorCode,
        goal_id: scope.goal_id,
        agent_id: scope.agent_id,
      };
    }
    const {summary, observationCount} = projectActiveObservation(rawObs, record.mode);
    return {
      ok: true,
      status: "read",
      goal_id: scope.goal_id,
      agent_id: scope.agent_id,
      observation_count: observationCount,
      current: {
        ...view,
        observation: summary,
        commands,
      },
    };
  }

  if (action === "history") {
    const cursor =
      request.after_cursor === null || request.after_cursor === undefined
        ? null
        : text(request.after_cursor, "after_cursor", 256);
    const page = await store.scanCommitted(cursor, 20);
    if (page.status !== "page") {
      return {
        ok: false,
        status: "unavailable",
        error: "decision_context_hook_history_unreadable",
      };
    }
    return {
      ok: true,
      status: "history",
      events: page.transactions.flatMap(tx => tx.events),
      next_cursor: page.next_cursor,
      has_more: page.has_more,
      current: view,
    };
  }

  const expected = revision(request.expected_revision);
  const operation = text(request.operation_id, "operation_id", 128);
  if (request.source_kind !== "operator_configuration") {
    throw new TypeError("only explicit operator configuration may update hook bindings");
  }
  let proposed: StoredBinding;
  if (action === "bind") {
    const mode = modeOf(request.mode);
    const profilePath = text(request.profile_path, "profile_path", 4096);
    const cursorStatePath = optionalPath(request.cursor_state_path, "cursor_state_path");
    const spoolPath = optionalPath(request.spool_path, "spool_path");
    if (mode === "capture_status" && spoolPath === null) {
      throw new TypeError("capture_status mode requires spool_path");
    }
    proposed = {
      source_kind: "operator_configuration",
      recorded_at: now,
      operation_id: operation,
      supersedes_operation_id: record?.operation_id ?? null,
      state: "active",
      mode,
      profile_path: profilePath,
      cursor_state_path: cursorStatePath,
      spool_path: spoolPath,
    };
  } else {
    if (record === null) {
      throw new TypeError("unbind requires an existing decision-context hook binding");
    }
    proposed = {
      source_kind: "operator_configuration",
      recorded_at: now,
      operation_id: operation,
      supersedes_operation_id: record.operation_id,
      state: "retired",
      mode: null,
      profile_path: null,
      cursor_state_path: null,
      spool_path: null,
    };
  }

  const intent: JsonObject = {
    scope,
    action,
    operation_id: operation,
    expected_revision: expected,
    mode: proposed.mode,
    profile_ref:
      proposed.state === "active"
        ? opaquePathRef("profile-path", proposed.profile_path)
        : null,
    cursor_state_ref:
      proposed.state === "active" && proposed.cursor_state_path !== null
        ? opaquePathRef("cursor-path", proposed.cursor_state_path)
        : null,
    spool_ref:
      proposed.state === "active" && proposed.spool_path !== null
        ? opaquePathRef("spool-path", proposed.spool_path)
        : null,
  };
  const intentHash = hashScope(intent);

  const prior = await store.readReceipt(operation);
  if (prior.status === "found") {
    if (prior.receipts.length !== 1 || prior.receipts[0]?.intent_hash !== intentHash) {
      return {ok: false, status: "operation_conflict", current: view};
    }
    const fresh = await store.loadAuthority();
    if (fresh.status !== "loaded") {
      return {
        ok: false,
        status: "unavailable",
        error: "decision_context_hook_readback_unavailable",
      };
    }
    return {
      ok: true,
      status: "replayed",
      receipt: prior.receipts[0]!,
      current: currentView(
        scope,
        storedBindingOf(fresh.head, scope),
        fresh.provider_revision,
      ),
    };
  }
  if (prior.status !== "missing") {
    return {
      ok: false,
      status: "unavailable",
      error: "decision_context_hook_receipt_unreadable",
    };
  }
  if (actualRevision !== expected) {
    return {ok: false, status: "revision_conflict", current: view};
  }

  const publicProposed = publicBinding(proposed);
  const receipt: JsonObject = {
    operation_id: operation,
    intent_hash: intentHash,
    state: proposed.state,
    mode: proposed.mode,
    profile_ref: publicProposed.profile_ref,
    supersedes_operation_id: proposed.supersedes_operation_id,
  };
  if (request.execute !== true) {
    return {ok: true, status: "preview", proposed: publicProposed, current: view};
  }

  const event: JsonObject = {
    schema_version: DECISION_CONTEXT_HOOK_EVENT_SCHEMA,
    ...publicProposed,
  };
  const committed = await store.commitAuthority({
    expected_provider_revision: expected,
    operation_id: operation,
    events: [event],
    next_projection: {
      schema_version: DECISION_CONTEXT_HOOK_STORE_SCHEMA,
      scope,
      record: proposed,
    },
    receipts: [receipt],
  });
  if (committed.status !== "applied") {
    return {
      ok: false,
      status: committed.status,
      error: "decision_context_hook_commit_not_confirmed",
    };
  }
  const readback = await store.loadAuthority();
  if (readback.status !== "loaded") {
    return {
      ok: false,
      status: "ambiguous",
      error: "decision_context_hook_readback_unavailable",
      operation_id: operation,
    };
  }
  return {
    ok: true,
    status: "applied",
    receipt,
    current: currentView(
      scope,
      storedBindingOf(readback.head, scope),
      readback.provider_revision,
    ),
  };
}

export async function decisionContextHook(request: JsonObject): Promise<JsonObject> {
  const scope = scopeOf(request);
  const directory = join(
    text(request.runtime_root, "runtime_root", 4096),
    "decision-context",
    "agent-bindings",
    hashScope(scope),
  );
  const mutating =
    (request.action === "bind" || request.action === "unbind") && request.execute === true;
  const store = new FileAuthorityStore(directory, "binding", {existingOnly: !mutating});
  try {
    await access(store.path, constants.R_OK);
  } catch (error) {
    if (["EACCES", "EPERM"].includes(String((error as NodeJS.ErrnoException).code))) {
      return {
        ok: false,
        status: "permission_denied",
        error: "decision_context_hook_permission_denied",
      };
    }
  }
  return executeDecisionContextHook(request, store);
}
