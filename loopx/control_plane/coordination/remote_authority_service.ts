/** Authenticated task commands over the existing semantic and persistence owners. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore, AuthorityStoreReadFailure} from "./authority_store.ts";
import {canonicalAuthoritySha256, parseAuthorityCursor} from "./authority_store_codec.ts";
import {PostgreSqlAuthorityService} from "./postgresql_authority_service.ts";
import type {PostgreSqlAuthorityDatabase} from "./postgresql_authority_store.ts";
import {executeCoordinationTodoClaim} from "./todo_claim.ts";
import {executeCoordinationTodoUpdate} from "./todo_update.ts";
import {executeCoordinationTodoCreate} from "./todo_create.ts";
import {executeCoordinationTodoTerminalLifecycle, type CoordinationTodoTerminalLifecycleInput} from "./todo_terminal_lifecycle.ts";
import {executeCanonicalTaskLeaseLifecycle} from "./task_lease_lifecycle.ts";
import {leaseVersion, leaseEpoch} from "../work_items/task_lease_acquire.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "./coordination_state_contract.ts";
import {CoordinationProjectionRead} from "./coordination_projection.ts";
import {readGoalAcceptanceLifecycle, sameExactGoalRef, type WireExactGoalRef} from "../goals/acceptance_lifecycle.ts";
import {decodeRemoteAuthorityRequest, remoteAuthorityOperationId, remoteAuthorityResponse, remoteAuthoritySnapshotDigest, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE,
  type RemoteAuthorityMode, type RemoteAuthorityRequest} from "./remote_authority_contract.ts";

export type RemoteAuthorityAdmission =
  | {status: "allowed"; principal_id: string; policy_revision: string; registered_agents: readonly string[];
      read_scope?: {goal_ref: WireExactGoalRef; todo_ids: readonly string[]; receipt_operation_id?: string}}
  | {status: "rejected"; reason_code: string};
export interface RemoteAuthorityDependencies {
  database: PostgreSqlAuthorityDatabase;
  /** One immutable, server-owned authorization decision per request. Revocation
   * affects later admissions; it never lies about an already admitted commit. */
  admit(credential: string, request: RemoteAuthorityRequest, mode: RemoteAuthorityMode): Promise<RemoteAuthorityAdmission>;
  now?: () => Date;
}
const incarnationFailure: AuthorityStoreReadFailure = {status: "unavailable",
  reason_code: "remote_authority_incarnation_changed", reason: "Rebind the authorized service scope before continuing"};
const scopeDenied = {status: "rejected", reason_code: "remote_authority_scope_denied"};
const MAX_READ_NOTE_BYTES = 4096;

/** Pin every observation and write to the admitted incarnation. The provider's
 * transactional revision check also fences rotation after our last read. */
function pinnedStore(store: AuthorityStore, identity: string, admission: JsonObject,
  operationId: string): AuthorityStore {
  const matches = (revision: string) => revision.startsWith(`${identity}:`);
  return {
    providerKind: "postgresql",
    async storeIdentity() {
      const result = await store.storeIdentity();
      return result.status === "available" && result.store_identity !== identity ? incarnationFailure : result;
    },
    async loadAuthority() {
      const result = await store.loadAuthority();
      return result.status === "loaded" && !matches(result.provider_revision) ? incarnationFailure : result;
    },
    async readReceipt(id) {
      // Both found and missing must come from the admitted incarnation. Never
      // manufacture a snapshot from separate identity and receipt reads.
      if (!store.readReceiptSnapshot) return {status: "unavailable",
        reason_code: "remote_authority_receipt_snapshot_unavailable", reason: "Atomic receipt observation is required"};
      const snapshot = await store.readReceiptSnapshot(id);
      if (snapshot.status !== "snapshot") return snapshot;
      if (snapshot.store_identity !== identity) return incarnationFailure;
      const result = snapshot.receipt;
      if (result.status !== "found") return result;
      if (!matches(result.provider_revision)) return incarnationFailure;
      if (id === operationId) {
        const stamp = result.receipts[0]?.remote_admission as JsonObject | undefined;
        if (result.receipts.length !== 1 || !stamp || ["principal_id", "actor_id", "request_sha256"].some(k =>
          stamp[k] !== admission[k])) return {status: "failed", reason_code: "remote_authority_operation_mismatch",
          reason: "Operation belongs to a different admitted request"};
      }
      return result;
    },
    async scanCommitted(after, limit) {
      const result = await store.scanCommitted(after, limit);
      return result.status === "page" && result.transactions.some(row => !matches(row.provider_revision)) ? incarnationFailure : result;
    },
    async commitAuthority(commit) {
      if (commit.operation_id !== operationId || commit.expected_provider_revision === null ||
          !matches(commit.expected_provider_revision)) return {status: "failed",
        reason_code: "remote_authority_commit_binding_invalid", reason: "Command does not match admitted authority"};
      if (commit.next_projection.handoff_mode !== "hard_lease") return {
        status: "failed", reason_code: "remote_authority_requires_hard_lease",
        reason: "The committing authority must use hard leases",
      };
      // Metadata shares the existing business receipt transaction; no second ledger.
      return store.commitAuthority({...commit, receipts: commit.receipts.map(receipt =>
        ({...receipt, remote_admission: admission}))});
    },
  };
}

function projectReadTodo(todo: JsonObject, includePlanning: boolean, includeNote: boolean): JsonObject {
  const fields = ["todo_id", "status", "claimed_by", ...(includeNote ? ["note"] : []),
    ...(includePlanning ? ["role", "priority"] : [])];
  const output = Object.fromEntries(fields.filter(field => Object.hasOwn(todo, field)).map(field => [field, todo[field]]));
  if (Object.hasOwn(output, "note") && Buffer.byteLength(JSON.stringify(output.note)) > MAX_READ_NOTE_BYTES) {
    delete output.note;
    output.note_omitted = true;
  }
  return output;
}

function isActiveGoal(head: JsonObject, goalRef: WireExactGoalRef): boolean {
  const lifecycle = readGoalAcceptanceLifecycle(head, goalRef.goal_id);
  return lifecycle?.state === "active" && sameExactGoalRef(lifecycle.goal_ref, goalRef);
}

/** Authorize from the current pinned head, then prove the original transaction
 * was visible at that head and committed under the same exact Goal lifetime. */
async function readScopedReceipt(store: AuthorityStore, goalRef: WireExactGoalRef,
  todoId: string, operationId: string): Promise<JsonObject> {
  const unavailable = {status: "unavailable", reason_code: "remote_authority_read_unavailable"};
  const head = await store.loadAuthority();
  if (head.status !== "loaded") return unavailable;
  if (!isActiveGoal(head.head, goalRef)) return scopeDenied;
  const current = new CoordinationProjectionRead(head.head, goalRef.goal_id);
  current.validateTodoReadModel();
  if (!current.todoIndex.todos.has(todoId)) return scopeDenied;

  // The pinned store checks the incarnation, principal, actor and exact request
  // digest on the receipt. An ACL cannot reassign an older or unrelated intent.
  const receipt = await store.readReceipt(operationId);
  if (receipt.status !== "found") return receipt.status === "missing" ||
    receipt.reason_code === "remote_authority_operation_mismatch" ? scopeDenied : unavailable;
  const cursor = parseAuthorityCursor(receipt.cursor);
  if (cursor > parseAuthorityCursor(head.cursor)) return scopeDenied;

  // The provider reconstructs this window from its existing checkpoint and
  // validates the committed projection. No raw ledger or current fallback.
  const offset = cursor - 1n;
  const page = await store.scanCommitted(offset === 0n ? null : offset.toString(), 1);
  if (page.status !== "page" || page.transactions.length !== 1) return unavailable;
  const transaction = page.transactions[0]!;
  const digest = canonicalAuthoritySha256(receipt.receipts[0]);
  if (transaction.cursor !== receipt.cursor || transaction.provider_revision !== receipt.provider_revision ||
      transaction.operation_id !== operationId || transaction.receipts.length !== 1 ||
      canonicalAuthoritySha256(transaction.receipts[0]) !== digest) return unavailable;
  if (!isActiveGoal(transaction.projection, goalRef)) return scopeDenied;
  return {status: "found", historical_only: true, todo_id: todoId, operation_id: operationId, receipt_sha256: digest};
}

/** Deliberately bounded response projection: no SQL errors, paths, arbitrary
 * canonical fields, workspace observations or provider credentials cross here. */
function commandResult(result: JsonObject): JsonObject {
  const output: JsonObject = {};
  for (const key of ["status", "reason_code", "changed", "todo_id", "claimed_by", "operation_id",
    "claim_operation_id", "provider_revision", "cursor", "handoff_mode", "released", "renewed", "missing",
    "completed", "completed_at", "completion_receipt_id"]) {
    if (Object.hasOwn(result, key)) output[key] = result[key];
  }
  if (result.lease && typeof result.lease === "object" && !Array.isArray(result.lease)) {
    const lease = result.lease as JsonObject;
    output.lease = {...Object.fromEntries(["goal_id", "todo_id", "owner", "idempotency_key", "status",
      "acquired_at", "updated_at", "expires_at"].filter(k => k in lease).map(k => [k, lease[k]])),
      version: leaseVersion(lease), lease_epoch: leaseEpoch(lease)};
  }
  if (result.todo && typeof result.todo === "object" && !Array.isArray(result.todo)) {
    const todo = result.todo as JsonObject;
    output.todo = Object.fromEntries(["todo_id", "status", "role", "priority", "claimed_by", "note"].filter(k => k in todo).map(k => [k, todo[k]]));
  }
  for (const key of ["blocked_lifecycle_transition", "deferred_transition"]) {
    const raw = result[key];
    if (raw && typeof raw === "object" && !Array.isArray(raw)) {
      const transition = raw as JsonObject;
      output[key] = Object.fromEntries(["kind", "lease_retirement", "execution_authority_granted"]
        .filter(field => Object.hasOwn(transition, field)).map(field => [field, transition[field]]));
    }
  }
  return output;
}

export class RemoteAuthorityService {
  readonly #dependencies: RemoteAuthorityDependencies;
  constructor(dependencies: RemoteAuthorityDependencies) { this.#dependencies = dependencies; }

  async dispatch(credential: string, raw: unknown, mode: RemoteAuthorityMode = "execute"): Promise<JsonObject> {
    let request: RemoteAuthorityRequest;
    try { request = decodeRemoteAuthorityRequest(raw); }
    catch { return {status: "rejected", reason_code: "invalid_remote_authority_request"}; }
    const reply = (payload: JsonObject) => remoteAuthorityResponse(request, mode, payload);
    if (mode !== "execute" && mode !== "recover" || mode === "recover" && (request.operation === "read" || request.operation === "snapshot")) {
      return reply({status: "rejected", reason_code: "invalid_remote_authority_mode"});
    }
    let admission: RemoteAuthorityAdmission;
    try { admission = structuredClone(await this.#dependencies.admit(credential, request, mode)); }
    catch { return reply({status: "unavailable", reason_code: "remote_authority_policy_unavailable"}); }
    if (admission.status !== "allowed") return reply(admission);
    const isRead = request.operation === "read" || request.operation === "snapshot";
    const readScope = admission.read_scope;
    const allowedTodos = readScope && new Set(readScope.todo_ids);
    const operationId = remoteAuthorityOperationId(request, admission.principal_id);
    if (allowedTodos && (allowedTodos.size === 0 || request.todo_id && !allowedTodos.has(request.todo_id))) return reply(scopeDenied);
    if (readScope && !isRead && (mode !== "recover" || request.todo_id === undefined ||
        readScope.receipt_operation_id !== operationId)) return reply(scopeDenied);
    // Open through the established service seam with the already authenticated
    // snapshot. Never re-evaluate a different policy halfway through a request.
    const service = new PostgreSqlAuthorityService({database: this.#dependencies.database,
      authenticatePrincipal: () => ({status: "authenticated", principal: {principal_id: admission.principal_id}}),
      authorizeTenant: () => ({status: "allowed"})});
    const opened = await service.openStore({...request, credential: null});
    if (opened.status !== "opened") return reply({status: "unavailable", reason_code: opened.reason_code});
    // Explicitly reauthorizing a restored incarnation may recover old history.
    // The incarnation gates current access; it is not part of business intent.
    const {store_identity: admittedIdentity, ...intent} = request;
    const binding: JsonObject = {principal_id: admission.principal_id, actor_id: request.actor_id,
      request_sha256: canonicalAuthoritySha256(intent), admitted_store_identity: admittedIdentity,
      policy_revision: admission.policy_revision};
    const store = pinnedStore(opened.store, request.store_identity, binding, operationId);
    let dispatched = false;
    try {
      if (readScope && mode === "recover" && request.todo_id !== undefined) {
        const result = await readScopedReceipt(store, readScope.goal_ref, request.todo_id, operationId);
        return result.status === "found"
          ? remoteAuthorityResponse(request, mode, {...result, goal_ref: readScope.goal_ref}, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE)
          : reply(result);
      }
      const previous = isRead ? null : await store.readReceipt(operationId);
      if (previous?.status === "found") {
        const original = previous.receipts[0];
        const recorded = original?.remote_admission as JsonObject | undefined;
        if (previous.receipts.length !== 1 || !recorded || recorded.principal_id !== admission.principal_id ||
            recorded.actor_id !== request.actor_id || recorded.request_sha256 !== binding.request_sha256) {
          return reply({status: "rejected", reason_code: "remote_authority_operation_mismatch"});
        }
        if (mode === "recover") return reply({status: "found", operation_id: operationId,
          provider_revision: previous.provider_revision, cursor: previous.cursor,
          receipt_sha256: canonicalAuthoritySha256(original), historical_only: true,
          result: commandResult(original.result && typeof original.result === "object"
            ? original.result as JsonObject : original)});
      } else if (previous && previous.status !== "missing") {
        return reply({status: previous.reason_code === "remote_authority_operation_mismatch" ? "rejected" : "unavailable",
          reason_code: previous.reason_code});
      }
      if (mode === "recover") return reply({status: "missing", operation_id: operationId});
      const head = await store.loadAuthority();
      if (head.status !== "loaded") return reply({status: "unavailable", reason_code: "remote_authority_head_unavailable"});
      if (readScope && !isActiveGoal(head.head, readScope.goal_ref)) return reply(scopeDenied);
      const projection = new CoordinationProjectionRead(head.head, request.goal_id);
      projection.validateTodoReadModel();
      const index = projection.coordinationIndex;
      const replyRead = (payload: JsonObject) => readScope
        ? remoteAuthorityResponse(request, mode, {...payload, goal_ref: readScope.goal_ref}, REMOTE_AUTHORITY_SCOPED_READ_RESPONSE)
        : reply({...payload, provider_revision: head.provider_revision});
      if (request.operation === "read") {
        const todo = index.todos.get(request.todo_id), lease = index.leases.get(request.todo_id);
        if (!todo) return reply({status: "missing"});
        return replyRead({status: "found",
          todo: projectReadTodo(todo, false, readScope === undefined),
          lease: lease ? {...Object.fromEntries(["owner", "status", "expires_at"].filter(k => k in lease).map(k => [k, lease[k]])),
            version: leaseVersion(lease), lease_epoch: leaseEpoch(lease)} : null});
      }
      if (request.operation === "snapshot") {
        const visible = (id: string) => (!request.todo_id || id === request.todo_id) && (!allowedTodos || allowedTodos.has(id));
        const todos = index.todo_ids
          .filter(visible)
          .map(id => projectReadTodo(index.todos.get(id)!, true, readScope === undefined));
        const leases = index.lease_todo_ids
          .filter(visible)
          .map(id => {
            const l = index.leases.get(id)!;
            return {
              ...Object.fromEntries(["goal_id", "todo_id", "owner", "status", "expires_at"].filter(k => k in l).map(k => [k, l[k]])),
              version: leaseVersion(l),
              lease_epoch: leaseEpoch(l),
            };
          });
        return replyRead({
          status: "snapshot",
          store_identity: request.store_identity,
          goal_id: request.goal_id,
          receipt_sha256: remoteAuthoritySnapshotDigest(request, head.provider_revision, todos, leases, readScope?.goal_ref),
          todos,
          leases,
        });
      }
      if (head.head.handoff_mode !== "hard_lease") return reply({status: "rejected", reason_code: "remote_authority_requires_hard_lease"});
      const now = this.#dependencies.now?.() ?? new Date();
      if (!Number.isFinite(now.valueOf())) return reply({status: "unavailable", reason_code: "remote_authority_clock_unavailable"});
      const common = {goal_id: request.goal_id, todo_id: request.todo_id,
        registered_agents: admission.registered_agents, now};
      let result: JsonObject;
      dispatched = true;
      if (request.operation === "claim") result = await executeCoordinationTodoClaim(store, {...common,
        operation_id: operationId, claimed_by: request.actor_id, actor_agent_id: request.actor_id,
        expected_role: "agent", manage_execution: true, required_handoff_mode: "hard_lease", dry_run: false});
      else if (request.operation === "progress") result = await executeCoordinationTodoUpdate(store, {...common,
        operation_id: operationId, actor_agent_id: request.actor_id, expected_role: "agent", dry_run: false,
        claim_operation_id: remoteAuthorityOperationId(request, admission.principal_id, request.claim_request_id),
        patch: {note: request.note}, clear_fields: []});
      else if (request.operation === "complete") {
        const claimOpId = remoteAuthorityOperationId(request, admission.principal_id, request.claim_request_id);
        const input: CoordinationTodoTerminalLifecycleInput = {
          goal_id: request.goal_id, todo_id: request.todo_id, command: "complete",
          expected_role: "agent", actor_agent_id: request.actor_id,
          registered_agents: admission.registered_agents, lifecycle_grants: [], authority_reason: null,
          decision_outcome: null, operation_identity: {kind: "explicit", operation_id: operationId},
          claim_operation_id: claimOpId, lease_idempotency_key: null, lease_expected_version: null,
          allow_user_gate_auto_acquire: false, requested_no_followup: request.no_followup === true,
          requested_completion_turn_key: null, requested_completion_identity_source: null,
          linked_successor_todo_ids: [], successor_intents: [], note: request.note ?? null,
          evidence: request.evidence, reason: null, clear_claim: true, validation_declaration: null,
          validation_receipt: null, completion_policy_request: null, dry_run: false, now,
        };
        result = await executeCoordinationTodoTerminalLifecycle(store, input);
        // Older complete requests implied no_followup. Use that intent only to
        // replay an already committed receipt for the exact authenticated request.
        if (previous?.status === "found" && request.no_followup === undefined &&
            result.reason_code === "coordination_operation_identity_mismatch") {
          result = await executeCoordinationTodoTerminalLifecycle(store, {...input, requested_no_followup: true});
        }
        if (result.status === "applied") result.completed = true;
      } else if (request.operation === "block" || request.operation === "defer") {
        const claimOpId = request.claim_request_id
          ? remoteAuthorityOperationId(request, admission.principal_id, request.claim_request_id)
          : undefined;
        result = await executeCoordinationTodoUpdate(store, {
          ...common, operation_id: operationId, actor_agent_id: request.actor_id,
          expected_role: "agent", dry_run: false, ...(claimOpId ? {claim_operation_id: claimOpId} : {}),
          patch: {...(request.note ? {note: request.note} : {})}, clear_fields: [],
          planning_intent: {
            status: request.operation === "block" ? "blocked" : "deferred",
            reason: request.reason,
            ...(request.resume_when ? {resume_when: request.resume_when} : {}),
          },
        });
      } else if (request.operation === "todo_add") {
        const todoRecord = {
          schema_version: TODO_DOMAIN_ITEM_SCHEMA,
          todo_id: request.todo_id,
          role: request.role ?? "agent",
          status: "open",
          done: false,
          text: request.text,
          archive_state: "active",
          ...(request.priority ? {priority: request.priority} : {}),
          ...(request.task_class ? {task_class: request.task_class} : {}),
          ...(request.action_kind ? {action_kind: request.action_kind} : {}),
          ...(request.task_repository ? {task_repository: request.task_repository} : {}),
          ...(request.required_write_scopes ? {required_write_scopes: [...request.required_write_scopes]} : {}),
          ...(request.claimed_by ? {claimed_by: request.claimed_by} : {}),
          ...(request.note ? {note: request.note} : {}),
        };
        result = await executeCoordinationTodoCreate(store, {
          goal_id: request.goal_id, todo: todoRecord, actor_agent_id: request.actor_id,
          registered_agents: admission.registered_agents, operation_id: operationId, dry_run: false, now,
        });
        // The create owner can return a different, semantically duplicate
        // Todo. That is a valid local no-op but not proof of this requested id.
        if (result.status === "no_change" && result.todo_id !== request.todo_id) {
          result = {status: "failed", reason_code: "remote_authority_todo_id_not_created"};
        }
      } else result = await executeCanonicalTaskLeaseLifecycle(store, {...common, operation: request.operation,
        owner: request.actor_id, idempotency_key: request.idempotency_key, required_handoff_mode: "hard_lease",
        expected_version: request.expected_version, ttl_seconds: request.operation === "renew" ? request.ttl_seconds : null});
      return reply({status: "result", operation_id: operationId, result: commandResult(result)});
    } catch {
      if (readScope) return reply({status: "unavailable", reason_code: "remote_authority_read_unavailable"});
      return reply({status: dispatched ? "ambiguous" : "unavailable", operation_id: operationId,
        reason_code: dispatched ? "remote_authority_recovery_required" : "remote_authority_read_unavailable"});
    }
  }
}
