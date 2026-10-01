import {observeLeaseWorktree, type LeaseWorkspace} from "./task_lease_workspace.ts";
import {executeCanonicalTaskLeaseAcquire} from "../coordination/task_lease_acquire.ts";
import {withCanonicalTaskLeaseAuthority} from "./canonical_task_lease_lifecycle.ts";
import {evaluateTaskLeaseWriteScopesOverlap, evaluateTaskLeaseAcquireDecision, materializeTaskLeaseAcquire, type AcquireDecisionOtherLease} from "./task_lease_acquire_decision.ts";
import {leaseOwnerRejection as ownerRejection} from "./task_lease_eligibility.ts";
import { ShadowManagementError, requireShadowPrimaryWriteAllowed } from "../coordination/shadow_management.ts";
import { parseIsoTimestamp } from "../runtime_timestamp.ts";
import { LegacyCoordinationWriteError, requireLegacyCoordinationPrimaryWriteAllowed } from "../coordination/legacy_writer_fence.ts";
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";

import {
  EffectRuntimeLockTimeoutError,
  EffectRuntimeRequestError,
} from "../effect_runtime_errors.ts";
import { atomicWriteJson, withFileMutationLock } from "../effect_runtime_io.ts";
import {
  legacyCoordinationLeaseLockPath,
  taskLeaseLockPath,
} from "../coordination/legacy_writer_lock_paths.ts";
import {
  settlementIdentity,
  type JsonObject,
} from "../effect_program.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import {
  beginLeaseOutboxEntry,
  decodeLocalAuthorityShadowBinding,
  type LocalAuthorityShadowBinding,
} from "../coordination/local_authority_shadow_outbox.ts";
import { TASK_LEASE_ACQUIRE_REQUEST_SCHEMA, TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA } from "../coordination/coordination_state_contract.generated.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";

export const TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION =
  TASK_LEASE_ACQUIRE_REQUEST_SCHEMA;
export const TASK_LEASE_SCHEMA_VERSION = "task_lease_v0";

const DEFAULT_TTL_SECONDS = 45 * 60;
const MAX_TTL_SECONDS = 24 * 60 * 60;
const TODO_WRITE_SCOPE_MAX_CHARS = 160;

type FileState = "file" | "missing";
export type TodoFactField =
  | "status"
  | "claimed_by"
  | "excluded_agents"
  | "role"
  | "task_class"
  | "bound_agent"
  | "blocks_agent";

export interface SourceReceipt {
  source_id: string;
  path: string;
  state: FileState;
  sha256: string | null;
}

export interface TodoFact {
  todo_id: string;
  status: string;
  claimed_by: string | null;
  excluded_agents: readonly string[];
  role?: string;
  task_class?: string | null;
  bound_agent?: string | null;
  blocks_agent?: string | null;
  task_repository?: string | null;
  /** Fields explicitly supplied by a legacy caller snapshot, if known. */
  provided_fields?: readonly TodoFactField[];
}

export interface AuthorityFacts {
  handoff_mode: string;
  registered_agents: readonly string[];
  todos: ReadonlyMap<string, TodoFact>;
  todo_projection_error: TaskLeaseFailure | null;
  source_receipts: readonly SourceReceipt[];
}

interface AcquireRequest {
  canonical: boolean;
  write_worktree: string | null;
  write_workspace?: LeaseWorkspace | null;
  runtime_root: string;
  goal_id: string;
  owner: string;
  todo_id: string;
  idempotency_key: string;
  write_scopes: readonly string[];
  ttl_seconds: number;
  expected_version: number | null;
  authority: AuthorityFacts;
  runtime_shadow: LocalAuthorityShadowBinding | null;
}

export interface LeaseRecord extends JsonObject {
  schema_version?: unknown;
  goal_id?: unknown;
  todo_id?: unknown;
  owner?: unknown;
  idempotency_key?: unknown;
  write_scopes?: unknown;
  write_repository?: unknown;
  acquire_ttl_seconds?: unknown;
  version?: unknown;
  lease_epoch?: unknown;
  status?: unknown;
  acquired_at?: unknown;
  updated_at?: unknown;
  expires_at?: unknown;
}

interface TaskLeaseFailure {
  code: string;
  message: string;
  payload: JsonObject;
  stage?: "validation" | "durable_writeback";
  kind?: string;
}

interface ExecutionContext {
  effectId: string | null;
  leasePath: string | null;
}

export interface TaskLeaseAcquireDependencies {
  authorityProvider?: import("../coordination/local_authority_provider.ts").LocalAuthorityProviderDependencies;
  now?: () => Date;
  beforeWrite?: (lease: JsonObject) => void | Promise<void>;
}

export type TaskLeaseAcquireEnvelope = JsonObject;

export class TaskLeaseAcquireError extends Error {
  readonly code: string;
  readonly payload: JsonObject;

  constructor(message: string, code: string, payload: JsonObject = {}) {
    super(message);
    this.name = "TaskLeaseAcquireError";
    this.code = code;
    this.payload = payload;
  }
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== "string") {
    throw new EffectRuntimeRequestError(`${label} must be a string`);
  }
  return value;
}

function optionalInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "number" || !Number.isInteger(value)) {
    throw new EffectRuntimeRequestError(`${label} must be an integer or null`);
  }
  return value;
}

function compactString(value: unknown): string {
  if (value === null || value === undefined) return "";
  switch (typeof value) {
    case "string":
      return value;
    case "number":
    case "bigint":
    case "symbol":
      return value.toString();
    case "boolean":
      return value ? "true" : "false";
    case "function":
      return Function.prototype.toString.call(value);
    case "object":
      return Array.isArray(value) ? value.join(",") : Object.prototype.toString.call(value);
  }
  return "";
}

function compact(value: unknown): string {
  return compactString(value).trim().split(/\s+/u).filter(Boolean).join(" ");
}

export function normalizeAgent(value: unknown): string | null {
  const candidate = compact(value).toLowerCase().replaceAll(" ", "-");
  return /^[a-z][a-z0-9_.:@-]{0,79}$/u.test(candidate) ? candidate : null;
}

export function normalizeGoalId(value: unknown): string {
  const goalId = stringValue(value, "goal_id").trim();
  if (
    goalId.length === 0 || goalId === "." || goalId === ".." ||
    goalId.includes("/") || goalId.includes("\\")
  ) {
    throw new TaskLeaseAcquireError(
      "goal id must be a single path segment",
      "invalid_goal_id",
    );
  }
  return goalId;
}

export function normalizeTodoId(value: unknown, label = "todo_id"): string {
  const todoId = stringValue(value, label).trim().toLowerCase();
  if (!/^todo_[a-z0-9_-]{3,64}$/u.test(todoId)) {
    throw new TaskLeaseAcquireError(
      "todo id must use the todo_<token> shape",
      "invalid_todo_id",
    );
  }
  return todoId;
}

export function normalizeOwner(value: unknown): string {
  const owner = normalizeAgent(stringValue(value, "owner"));
  if (owner === null) {
    throw new TaskLeaseAcquireError(
      "owner must be a public-safe agent id",
      "invalid_owner",
    );
  }
  return owner;
}

export function normalizeIdempotencyKey(value: unknown): string {
  const key = stringValue(value, "idempotency_key").trim();
  if (!/^[A-Za-z0-9_.:@/-]{1,160}$/u.test(key)) {
    throw new TaskLeaseAcquireError(
      "idempotency key must be a public-safe token",
      "invalid_idempotency_key",
    );
  }
  return key;
}

export function normalizeWriteScopes(value: unknown): string[] {
  let raw: unknown[];
  if (Array.isArray(value)) {
    raw = value;
  } else if (typeof value === "string") {
    raw = value.split(/[,;|]/u);
  } else {
    raw = [value];
  }
  const scopes: string[] = [];
  const seen = new Set<string>();
  for (const item of raw) {
    const scope = compact(item);
    if (
      scope.length === 0 || scope.length > TODO_WRITE_SCOPE_MAX_CHARS ||
      scope.startsWith("/") || scope.startsWith("~") ||
      scope.split("/").includes("..") || /[\s<>]/u.test(scope) ||
      seen.has(scope)
    ) {
      continue;
    }
    seen.add(scope);
    scopes.push(scope);
  }
  return scopes;
}

export function normalizeTtl(value: unknown): number {
  const ttl = value === null || value === undefined
    ? DEFAULT_TTL_SECONDS
    : optionalInteger(value, "ttl_seconds");
  if (ttl === null || ttl <= 0 || ttl > MAX_TTL_SECONDS) {
    throw new TaskLeaseAcquireError(
      `ttl seconds must be between 1 and ${MAX_TTL_SECONDS}`,
      "invalid_ttl",
    );
  }
  return ttl;
}

function decodeSourceReceipt(value: unknown, index: number): SourceReceipt {
  const receipt = requireJsonObject(value, `authority.source_receipts[${index}]`);
  const sourceId = stringValue(receipt.source_id, "source receipt source_id").trim();
  const path = stringValue(receipt.path, "source receipt path");
  const state = stringValue(receipt.state, "source receipt state");
  if (!sourceId || !path || (state !== "file" && state !== "missing")) {
    throw new EffectRuntimeRequestError("authority source receipt is invalid");
  }
  const sha256 = receipt.sha256;
  if (
    (state === "file" &&
      (typeof sha256 !== "string" || !BARE_SHA256_PATTERN.test(sha256))) ||
    (state === "missing" && sha256 !== null)
  ) {
    throw new EffectRuntimeRequestError("authority source receipt digest is invalid");
  }
  return { source_id: sourceId, path, state, sha256: sha256 as string | null };
}

function decodeRegisteredAgentCandidate(value: unknown): string | null {
  if (Array.isArray(value)) return null;
  if (typeof value !== "object" || value === null) return normalizeAgent(value);
  const record = value as Record<string, unknown>;
  return normalizeAgent(record.id ?? record.agent_id ?? record.name);
}

function decodeRegisteredAgents(value: unknown): string[] {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("authority.registered_agent_candidates must be an array");
  }
  const agents: string[] = [];
  const seen = new Set<string>();
  for (const candidate of value) {
    const entries = Array.isArray(candidate) ? candidate : [candidate];
    for (const entry of entries) {
      const agent = decodeRegisteredAgentCandidate(entry);
      if (agent && !seen.has(agent)) {
        seen.add(agent);
        agents.push(agent);
      }
    }
  }
  return agents;
}

function excludedAgentCandidates(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === "string") return value.split(",");
  if (value === null || value === undefined) return [];
  return [value];
}

function decodeTodoFacts(value: unknown): ReadonlyMap<string, TodoFact> {
  if (!Array.isArray(value)) {
    throw new EffectRuntimeRequestError("authority.todos must be an array");
  }
  const todos = new Map<string, TodoFact>();
  for (let index = 0; index < value.length; index += 1) {
    const record = requireJsonObject(value[index], `authority.todos[${index}]`);
    const todoId = normalizeTodoId(record.todo_id, `authority.todos[${index}].todo_id`);
    const excluded = excludedAgentCandidates(record.excluded_agents);
    const excludedAgents = [...new Set(
      excluded.map(normalizeAgent).filter((agent): agent is string => agent !== null),
    )].sort((left, right) => left.localeCompare(right));
    todos.set(todoId, {
      todo_id: todoId,
      status: compact(record.status).toLowerCase(),
      claimed_by: normalizeAgent(record.claimed_by),
      excluded_agents: excludedAgents,
      role: typeof record.role === "string" ? compact(record.role).toLowerCase() : undefined,
      task_class: typeof record.task_class === "string"
        ? compact(record.task_class).toLowerCase()
        : null,
      bound_agent: normalizeAgent(record.bound_agent),
      blocks_agent: normalizeAgent(record.blocks_agent),
    });
  }
  return todos;
}

function decodeProjectionError(value: unknown): TaskLeaseFailure | null {
  if (value === null || value === undefined) return null;
  const error = requireJsonObject(value, "authority.todo_projection_error");
  const code = stringValue(error.code, "authority.todo_projection_error.code");
  const message = stringValue(error.message, "authority.todo_projection_error.message");
  const payload = error.payload === null || error.payload === undefined
    ? {}
    : requireJsonObject(error.payload, "authority.todo_projection_error.payload");
  return { code, message, payload };
}

/** Decode the compact authority projection shared by all local lease verbs. */
export function decodeTaskLeaseAuthority(value: unknown): AuthorityFacts {
  const authority = requireJsonObject(value, "authority");
  const receiptsValue = authority.source_receipts;
  if (!Array.isArray(receiptsValue) || receiptsValue.length === 0) {
    throw new EffectRuntimeRequestError(
      "authority.source_receipts must be a non-empty array",
    );
  }
  const receipts = receiptsValue.map((receipt, index) =>
    decodeSourceReceipt(receipt, index)
  );
  if (new Set(receipts.map((receipt) => receipt.source_id)).size !== receipts.length) {
    throw new EffectRuntimeRequestError("authority source ids must be unique");
  }
  return {
    handoff_mode: normalizeHandoffMode(authority.handoff_mode),
    registered_agents: decodeRegisteredAgents(authority.registered_agent_candidates),
    todos: decodeTodoFacts(authority.todos),
    todo_projection_error: decodeProjectionError(authority.todo_projection_error),
    source_receipts: receipts,
  };
}

export function normalizeHandoffMode(value: unknown): string {
  const mode = compact(value) || "legacy";
  if (!new Set(["legacy", "soft_claim", "hard_lease"]).has(mode)) {
    throw new TaskLeaseAcquireError(
      `unsupported handoff_mode '${mode}'; expected one of: legacy, soft_claim, hard_lease`,
      "invalid_handoff_mode",
      { handoff_mode: mode, supported: ["legacy", "soft_claim", "hard_lease"] },
    );
  }
  return mode;
}

function decodeRequest(value: unknown): AcquireRequest {
  const request = requireJsonObject(value, "task lease acquire request");
  const canonical = request.schema_version === TASK_LEASE_CANONICAL_ACQUIRE_REQUEST_SCHEMA;
  if (request.schema_version !== TASK_LEASE_ACQUIRE_REQUEST_SCHEMA_VERSION && !canonical) {
    throw new EffectRuntimeRequestError("Task-lease acquire request schema mismatch");
  }
  // Decode the authority envelope first.  Besides keeping the boundary
  // fail-closed, this preserves the public error ordering used by the native
  // CLI: a missing authority projection is reported before unrelated fields.
  if (canonical) {
    const allowed = new Set(["schema_version", "runtime_root", "goal_id", "todo_id", "owner", "idempotency_key",
      "ttl_seconds", "write_scopes", "write_worktree", "expected_version", "authority"]);
    const unsupported = Object.keys(request).find(key => !allowed.has(key));
    if (unsupported) throw new TaskLeaseAcquireError(`canonical acquire does not accept ${unsupported}`, "invalid_canonical_acquire_request");
  }
  const rawAuthority = requireJsonObject(request.authority, "acquire authority");
  const authority = decodeTaskLeaseAuthority(canonical ? {...rawAuthority, handoff_mode: "legacy", todos: [], todo_projection_error: null} : rawAuthority);
  return {
    canonical,
    write_worktree: request.write_worktree == null ? null : stringValue(request.write_worktree, "write_worktree"),
    runtime_root: stringValue(request.runtime_root, "runtime_root"),
    goal_id: normalizeGoalId(request.goal_id),
    owner: normalizeOwner(request.owner),
    todo_id: normalizeTodoId(request.todo_id),
    idempotency_key: normalizeIdempotencyKey(request.idempotency_key),
    write_scopes: normalizeWriteScopes(request.write_scopes),
    ttl_seconds: normalizeTtl(request.ttl_seconds),
    expected_version: optionalInteger(request.expected_version, "expected_version"),
    authority,
    runtime_shadow: decodeLocalAuthorityShadowBinding(request.runtime_shadow),
  };
}

export function taskLeaseDirectory(request: { runtime_root: string; goal_id: string }): string {
  return join(request.runtime_root, "goals", request.goal_id, "task-leases");
}

export function taskLeasePath(request: { runtime_root: string; goal_id: string; todo_id: string }): string {
  return join(taskLeaseDirectory(request), `${request.todo_id}.json`);
}

export { taskLeaseLockPath } from "../coordination/legacy_writer_lock_paths.ts";

function executionContext(value: unknown): ExecutionContext {
  const context: ExecutionContext = { effectId: null, leasePath: null };
  let request: JsonObject;
  try {
    request = requireJsonObject(value, "task lease acquire request");
  } catch {
    return context;
  }
  let runtimeRoot: string;
  let goalId: string;
  let todoId: string;
  try {
    runtimeRoot = stringValue(request.runtime_root, "runtime_root");
    goalId = normalizeGoalId(request.goal_id);
    todoId = normalizeTodoId(request.todo_id);
    context.leasePath = join(
      runtimeRoot,
      "goals",
      goalId,
      "task-leases",
      `${todoId}.json`,
    );
  } catch {
    return context;
  }
  try {
    context.effectId = settlementIdentity({
      goal_id: goalId,
      agent_id: normalizeOwner(request.owner),
      todo_id: todoId,
      turn_instance_id: normalizeIdempotencyKey(request.idempotency_key),
    }).effect_id;
  } catch {
    // An invalid settlement identity has no effect id. The CLI can still
    // report the deterministic lease path for a valid goal/work-item pair.
  }
  return context;
}

function asLeaseRecord(value: unknown, path: string): LeaseRecord {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new TaskLeaseAcquireError(
      `lease file must contain an object: ${path}`,
      "corrupt_lease",
      { lease_path: path },
    );
  }
  return value as LeaseRecord;
}

export async function readLease(path: string): Promise<LeaseRecord | null> {
  let text: string;
  try {
    text = await readFile(path, "utf8");
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return null;
    throw error;
  }
  try {
    return asLeaseRecord(JSON.parse(text), path);
  } catch (error) {
    if (error instanceof TaskLeaseAcquireError) throw error;
    throw new TaskLeaseAcquireError(
      `lease file is not valid JSON: ${path}`,
      "corrupt_lease",
      { lease_path: path, error: String(error) },
    );
  }
}

export function leaseInteger(
  lease: LeaseRecord | null,
  field: "version" | "lease_epoch" | "acquire_ttl_seconds",
): number | null {
  const raw = lease?.[field];
  if (raw === null || raw === undefined) return null;
  let number = Number.NaN;
  if (typeof raw === "number") {
    number = raw;
  } else if (typeof raw === "string" && /^-?\d+$/u.test(raw)) {
    number = Number(raw);
  }
  const minimum = field === "version" ? 0 : 1;
  if (
    typeof raw === "boolean" || !Number.isSafeInteger(number) || number < minimum
  ) {
    let message = "lease acquire_ttl_seconds must be a positive integer";
    if (field === "lease_epoch") {
      message = "lease epoch must be a positive integer";
    } else if (field === "version") {
      message = "lease version must be a non-negative integer";
    }
    throw new TaskLeaseAcquireError(message, "corrupt_lease", { [field]: raw ?? null });
  }
  return number;
}

export function leaseVersion(lease: LeaseRecord | null): number {
  return leaseInteger(lease, "version") ?? 0;
}

export function leaseEpoch(lease: LeaseRecord | null): number {
  if (lease === null) return 0;
  return leaseInteger(lease, "lease_epoch") ?? 1;
}


export function leaseIsActive(lease: LeaseRecord | null, at: Date): boolean {
  if (
    lease?.schema_version !== TASK_LEASE_SCHEMA_VERSION ||
    lease.status !== "active"
  ) {
    return false;
  }
  if (typeof lease.expires_at !== "string") {
    throw new TaskLeaseAcquireError(
      "active lease expires_at must be a valid timestamp",
      "corrupt_lease",
      { expires_at: lease.expires_at ?? null },
    );
  }
  const expiresAt = parseIsoTimestamp(lease.expires_at);
  if (expiresAt === null) {
    throw new TaskLeaseAcquireError(
      "active lease expires_at must be a valid timestamp",
      "corrupt_lease",
      { expires_at: lease.expires_at },
    );
  }
  return expiresAt.valueOf() > at.valueOf();
}

export function utcIsoformat(value: Date): string {
  return value.toISOString().replace(/\.\d{3}Z$/u, "Z");
}

function ownerFailure(
  code: string,
  request: AcquireRequest,
  todo: TodoFact | undefined,
): TaskLeaseAcquireError {
  let message: string;
  const detail: JsonObject = { effective: false, reason: code };
  if (code === "todo_not_found") {
    message = "todo is missing from the canonical projection";
  } else if (code === "todo_not_open") {
    detail.todo_status = todo?.status || "unknown";
    message = `task lease requires an open todo; '${request.todo_id}' is '${detail.todo_status}'`;
  } else if (code === "owner_excluded_from_todo") {
    detail.excluded_agents = [...(todo?.excluded_agents ?? [])];
    message = `task lease owner '${request.owner}' is excluded from todo '${request.todo_id}'`;
  } else if (code === "owner_conflicts_with_claim") {
    detail.claimed_by = todo?.claimed_by ?? null;
    message = `task lease owner '${request.owner}' conflicts with todo claim '${todo?.claimed_by}'`;
  } else {
    message = `task lease owner '${request.owner}' is not registered for goal '${request.goal_id}'`;
  }
  return new TaskLeaseAcquireError(message, code, {
    goal_id: request.goal_id,
    todo_id: request.todo_id,
    owner: request.owner,
    ...detail,
  });
}

async function currentSourceReceipt(receipt: SourceReceipt): Promise<SourceReceipt> {
  try {
    const bytes = await readFile(receipt.path);
    return {
      source_id: receipt.source_id,
      path: receipt.path,
      state: "file",
      sha256: createHash("sha256").update(bytes).digest("hex"),
    };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return { source_id: receipt.source_id, path: receipt.path, state: "missing", sha256: null };
    }
    throw error;
  }
}

export async function revalidateAuthoritySources(receipts: readonly SourceReceipt[]): Promise<void> {
  const changed: string[] = [];
  const actuals = await Promise.all(receipts.map(currentSourceReceipt));
  for (let index = 0; index < receipts.length; index += 1) {
    const expected = receipts[index];
    const actual = actuals[index];
    if (actual.state !== expected.state || actual.sha256 !== expected.sha256) {
      changed.push(expected.source_id);
    }
  }
  if (changed.length > 0) {
    throw new TaskLeaseAcquireError(
      "task-lease authority sources changed before acquire commit; retry with a fresh projection",
      "authority_source_changed",
      { changed_sources: changed },
    );
  }
}

async function leaseFiles(directory: string): Promise<string[]> {
  try {
    return (await readdir(directory))
      .filter((name) => /^todo_[a-z0-9_-]{3,64}\.json$/u.test(name))
      .sort((left, right) => left.localeCompare(right))
      .map((name) => join(directory, name));
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
}

function conflictPayload(
  lease: LeaseRecord,
  path: string,
): JsonObject {
  return {
    todo_id: lease.todo_id ?? null,
    owner: lease.owner ?? null,
    expires_at: lease.expires_at ?? null,
    version: lease.version ?? null,
    lease_epoch: leaseEpoch(lease),
    write_scopes: lease.write_scopes || [],
    lease_path: path,
  };
}

async function otherLeaseFacts(
  request: AcquireRequest,
  at: Date,
): Promise<Array<{ decision: AcquireDecisionOtherLease; payload: JsonObject }>> {
  const facts: Array<{ decision: AcquireDecisionOtherLease; payload: JsonObject }> = [];
  for (const path of await leaseFiles(taskLeaseDirectory(request))) {
    const lease = await readLease(path);
    if (!leaseIsActive(lease, at) || lease === null) continue;
    const leaseTodoId = normalizeTodoId(lease.todo_id, "lease.todo_id");
    if (leaseTodoId === request.todo_id) continue;
    const leaseOwner = normalizeAgent(lease.owner);
    const effective = ownerRejection(
        request.authority.todos.get(leaseTodoId),
        leaseOwner,
        request.authority.registered_agents,
      ) === null;
    facts.push({
      decision: {
        todo_id: leaseTodoId,
        active: true,
        effective,
        write_scopes: normalizeWriteScopes(lease.write_scopes),
      },
      payload: conflictPayload(lease, path),
    });
  }
  return facts;
}

function transitionError(
  code: string,
  request: AcquireRequest,
  lease: LeaseRecord | null,
  leasePath: string,
  conflicts: readonly JsonObject[] = [],
  idempotencyReuseKind: "retired" | "acquire_parameters" = "acquire_parameters",
): TaskLeaseAcquireError {
  if (code === "handoff_mode_forbids_lease") {
    return new TaskLeaseAcquireError(
      "goal handoff mode 'soft_claim' forbids task lease acquire; " +
        "release and inspect remain available for legacy leftovers",
      code,
      {
        goal_id: request.goal_id,
        todo_id: request.todo_id,
        action: "acquire",
        handoff_mode: request.authority.handoff_mode,
      },
    );
  }
  if (code === "version_mismatch") {
    const actual = leaseVersion(lease);
    return new TaskLeaseAcquireError(
      `lease version mismatch: expected ${request.expected_version}, got ${actual}`,
      code,
      { expected_version: request.expected_version, actual_version: actual },
    );
  }
  if (code === "idempotency_key_reuse") {
    if (idempotencyReuseKind === "retired") {
      return new TaskLeaseAcquireError(
        "idempotency key belongs to an expired or released lease generation; " +
          "a new execution must use a new key",
        code,
        { lease, lease_path: leasePath },
      );
    }
    return new TaskLeaseAcquireError(
      "idempotency key was reused with different acquire parameters",
      code,
      {
        lease,
        lease_path: leasePath,
        requested_write_scopes: [...request.write_scopes],
        requested_ttl_seconds: request.ttl_seconds,
      },
    );
  }
  if (code === "todo_lease_conflict") {
    return new TaskLeaseAcquireError(
      "todo already has an active lease",
      code,
      { lease, lease_path: leasePath },
    );
  }
  if (code === "write_scope_conflict") {
    return new TaskLeaseAcquireError(
      "write scope overlaps another active task lease",
      code,
      { conflicts: [...conflicts] },
    );
  }
  return new TaskLeaseAcquireError(
    `task lease acquire rejected by authority core: ${code}`,
    code,
    { lease, lease_path: leasePath },
  );
}

function successEnvelope(
  request: AcquireRequest,
  lease: LeaseRecord,
  leasePath: string,
  effectId: string,
  idempotent: boolean,
): TaskLeaseAcquireEnvelope {
  const status = idempotent ? "idempotent" : "committed";
  return {
    ok: true,
    schema_version: TASK_LEASE_SCHEMA_VERSION,
    action: "acquire",
    acquired: !idempotent,
    idempotent,
    lease,
    lease_path: leasePath,
    settlement: {
      effect_id: effectId,
      receipts: [
        { step: "validation", status, effect_id: effectId },
        { step: "durable_writeback", status, effect_id: effectId },
      ],
    },
  };
}

const INVALID_IDENTITY_CODES = new Set([
  "invalid_goal_id",
  "invalid_todo_id",
  "invalid_owner",
  "invalid_idempotency_key",
  "invalid_ttl",
  "idempotency_key_reuse",
]);
const PERMISSION_DENIED_CODES = new Set([
  "owner_not_registered",
  "todo_not_found",
  "todo_not_open",
  "owner_excluded_from_todo",
  "owner_conflicts_with_claim",
]);
const VALIDATION_FAILURE_CODES = new Set([
  "invalid_goal_id",
  "invalid_todo_id",
  "invalid_owner",
  "invalid_idempotency_key",
  ...PERMISSION_DENIED_CODES,
  "todo_lease_conflict",
  "write_scope_conflict",
  "lease_generation_exhausted",
  "authority_source_changed",
  "corrupt_lease",
]);

function failureKind(code: string): string {
  if (INVALID_IDENTITY_CODES.has(code)) return "invalid_identity";
  if (code === "corrupt_lease") return "permission_denied";
  if (PERMISSION_DENIED_CODES.has(code)) return "permission_denied";
  return "writeback_rejected";
}

/**
 * A fenced legacy writer is a terminal permission decision taken by the
 * promoted authority before this verb's first side effect: no settlement step
 * ran, so no receipt exists and the rejection is a validation-stage denial.
 */
function fencedFailure(error: LegacyCoordinationWriteError): TaskLeaseFailure {
  return { code: error.code, message: error.message, payload: error.payload, stage: "validation", kind: "permission_denied" };
}

function failureEnvelope(
  failure: TaskLeaseFailure,
  context: ExecutionContext,
): TaskLeaseAcquireEnvelope {
  const step = failure.stage ?? ((VALIDATION_FAILURE_CODES.has(failure.code)
    || failure.code.startsWith("shadow_management_"))
    ? "validation"
    : "durable_writeback");
  const kind = failure.kind ?? failureKind(failure.code);
  const receipts = step === "validation" || context.effectId === null
    ? []
    : [{ step: "validation", status: "committed", effect_id: context.effectId }];
  return {
    ok: false,
    schema_version: TASK_LEASE_SCHEMA_VERSION,
    action: "acquire",
    error: failure.message,
    error_code: failure.code,
    ...(context.leasePath === null ? {} : { lease_path: context.leasePath }),
    ...failure.payload,
    settlement: {
      effect_id: receipts.length > 0 ? context.effectId : null,
      receipts,
      failure: { step, kind, code: failure.code },
    },
  };
}

function validateAcquireAuthority(request: AcquireRequest): TodoFact | null {
  if (request.authority.todo_projection_error !== null) {
    throw new TaskLeaseAcquireError(
      request.authority.todo_projection_error.message,
      request.authority.todo_projection_error.code,
      request.authority.todo_projection_error.payload,
    );
  }
  return request.authority.todos.get(request.todo_id) ?? null;
}

function acquireEffectId(request: AcquireRequest): string {
  return settlementIdentity({
    goal_id: request.goal_id,
    agent_id: request.owner,
    todo_id: request.todo_id,
    turn_instance_id: request.idempotency_key,
  }).effect_id;
}

async function commitAcquire(
  request: AcquireRequest,
  dependencies: TaskLeaseAcquireDependencies,
): Promise<TaskLeaseAcquireEnvelope> {
  const at = (dependencies.now ?? (() => new Date()))();
  if (Number.isNaN(at.valueOf())) {
    throw new EffectRuntimeRequestError("task-lease acquire clock returned an invalid date");
  }
  await revalidateAuthoritySources(request.authority.source_receipts);
  const todo = validateAcquireAuthority(request);

  const leasePath = taskLeasePath(request);
  const existing = await readLease(leasePath);
  const version = leaseVersion(existing);
  const epoch = leaseEpoch(existing);
  const active = leaseIsActive(existing, at);
  const otherLeases = await otherLeaseFacts(request, at);
  const decision = evaluateTaskLeaseAcquireDecision({
    handoff_mode: request.authority.handoff_mode,
    registered_agents: [...request.authority.registered_agents],
    todo,
    lease: existing === null
      ? null
      : {
        present: true,
        active,
        status: typeof existing.status === "string" ? existing.status : null,
        owner: normalizeAgent(existing.owner),
        idempotency_key: typeof existing.idempotency_key === "string"
          ? existing.idempotency_key
          : null,
        version,
        lease_epoch: epoch,
        write_scopes: normalizeWriteScopes(existing.write_scopes),
        acquire_ttl_seconds: leaseInteger(existing, "acquire_ttl_seconds"),
      },
    other_leases: otherLeases.map((item) => item.decision),
    command: {
      owner: request.owner,
      idempotency_key: request.idempotency_key,
      ttl_seconds: request.ttl_seconds,
      write_scopes: [...request.write_scopes],
      expected_version: request.expected_version,
    },
  });
  if (decision.outcome === "no_change") {
    if (existing === null) {
      throw new EffectRuntimeRequestError(
        "task-lease acquire decision returned replay without an existing lease",
      );
    }
    return successEnvelope(
      request,
      existing,
      leasePath,
      acquireEffectId(request),
      true,
    );
  }
  if (decision.outcome !== "apply") {
    if (PERMISSION_DENIED_CODES.has(decision.code)) {
      throw ownerFailure(decision.code, request, todo ?? undefined);
    }
    const conflicts = decision.conflict_indexes.map((index) => {
      const fact = otherLeases[index];
      if (fact === undefined) {
        throw new EffectRuntimeRequestError(
          "task-lease acquire decision returned an invalid conflict index",
        );
      }
      return fact.payload;
    });
    throw transitionError(
      decision.code,
      request,
      existing,
      leasePath,
      conflicts,
      active ? "acquire_parameters" : "retired",
    );
  }
  if (decision.next_lease === null) {
    throw new EffectRuntimeRequestError(
      "task-lease acquire decision returned apply without next_lease",
    );
  }

  const lease = materializeTaskLeaseAcquire(request, request, decision, at);
  await dependencies.beforeWrite?.(lease);
  await revalidateAuthoritySources(request.authority.source_receipts);
  const captureRequired = request.runtime_shadow !== null ||
    await requireShadowPrimaryWriteAllowed(request.runtime_root, request.goal_id) !== null;
  const shadowCapture = !captureRequired
    ? null
    : await beginLeaseOutboxEntry({
      runtime_root: request.runtime_root,
      goal_id: request.goal_id,
      lease_directory: taskLeaseDirectory(request),
      write_class: "task_lease_acquire",
      operation_id: request.idempotency_key,
      previous_lease: existing,
      planned_lease: lease,
      active_todo_ids: [...request.authority.todos.keys()],
      goal_ref: request.runtime_shadow !== null && "goal_ref" in request.runtime_shadow
        ? request.runtime_shadow.goal_ref
        : undefined,
    });
  if (shadowCapture?.failure && await requireShadowPrimaryWriteAllowed(request.runtime_root, request.goal_id) !== null) {
    throw new ShadowManagementError("shadow_capture_prepare_failed", "durable shadow preparation failed; the primary lease was not changed");
  }
  await atomicWriteJson(leasePath, lease);
  await shadowCapture?.commit();
  const response = successEnvelope(request, lease, leasePath, acquireEffectId(request), false);
  if (shadowCapture !== null) {
    response.coordination_runtime_shadow_capture = {
      entry_id: shadowCapture.entry_id,
      seq: shadowCapture.seq,
      source_bytes_digest: shadowCapture.source_bytes_digest,
      failure: shadowCapture.failure,
      skipped_reason: shadowCapture.skipped_reason,
    };
  }
  return response;
}

export async function executeTaskLeaseAcquire(
  value: unknown,
  dependencies: TaskLeaseAcquireDependencies = {},
): Promise<TaskLeaseAcquireEnvelope> {
  let request: AcquireRequest;
  const context = executionContext(value);
  try {
    request = decodeRequest(value);
    if (request.write_worktree !== null) {
      if (!request.canonical || request.write_scopes.length === 0) throw new TaskLeaseAcquireError(
        "--write-worktree requires canonical authority and nonempty code-edit scopes", "invalid_worktree_lease_request");
      try { request.write_workspace = await observeLeaseWorktree(request.write_worktree, path =>
        evaluateTaskLeaseWriteScopesOverlap({left: request.write_scopes, right: [path]}).overlap === true); }
      catch { throw new TaskLeaseAcquireError("cannot verify independent Git worktree and origin for --write-worktree", "invalid_worktree_lease_request"); }
    }
  } catch (error) {
    if (error instanceof TaskLeaseAcquireError || error instanceof ShadowManagementError || error instanceof LegacyCoordinationWriteError) {
      return failureEnvelope(
        { code: error.code, message: error.message, payload: error.payload },
        context,
      );
    }
    throw error;
  }

  try {
    if (request.canonical) {
      const result = await withCanonicalTaskLeaseAuthority({...request, operation: "acquire"}, {
        ...dependencies, now: dependencies.now ?? (() => new Date()),
      }, async (store, guards) => {
        await guards.revalidate();
        return await executeCanonicalTaskLeaseAcquire(store, {...request,
          registered_agents: request.authority.registered_agents, now: (dependencies.now ?? (() => new Date()))()}, guards.beforeCommit);
      });
      if (["applied", "no_change", "replayed", "recovered"].includes(String(result.status))) {
        const idempotent = result.status !== "applied";
        const envelope = successEnvelope(request, result.lease as LeaseRecord, "", acquireEffectId(request), idempotent);
        delete envelope.lease_path;
        return {...result, ...envelope, acquired: !idempotent && result.changed === true};
      }
      const envelope = failureEnvelope({code: String(result.reason_code ?? result.conflict_kind ?? "canonical_acquire_failed"),
        message: String(result.reason ?? "canonical acquisition failed; inspect before retrying"),
        kind: result.reason_code === "legacy_writer_fence_read_failed" ? "permission_denied" : undefined,
        payload: result, stage: result.failure_stage === "durable_writeback" ? "durable_writeback" : "validation"}, context);
      delete envelope.lease_path;
      // Keep the public acquire schema even when the inner command has diagnostics.
      return {...envelope, schema_version: TASK_LEASE_SCHEMA_VERSION};
    }
    return await withFileMutationLock(
      legacyCoordinationLeaseLockPath(request.runtime_root, request.goal_id),
      () => withFileMutationLock(taskLeaseLockPath(request), async () => {
        await requireLegacyCoordinationPrimaryWriteAllowed(request.runtime_root, request.goal_id);
        return await commitAcquire(request, dependencies);
      }),
    );
  } catch (error) {
    if (error instanceof LegacyCoordinationWriteError) {
      return failureEnvelope(fencedFailure(error), context);
    }
    if (error instanceof TaskLeaseAcquireError || error instanceof ShadowManagementError) {
      return failureEnvelope(
        { code: error.code, message: error.message, payload: error.payload },
        context,
      );
    }
    if (error instanceof EffectRuntimeLockTimeoutError) {
      return failureEnvelope(
        {
          code: "lock_acquire_timeout",
          message: "task lease mutation lock timed out",
          payload: {},
        },
        context,
      );
    }
    throw error;
  }
}
