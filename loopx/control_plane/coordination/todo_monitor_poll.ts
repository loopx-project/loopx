import {monitorMutationRejection} from "./todo_monitor_cycle.ts";
import {AUTHORITY_SOURCE_CHANGED, uncheckedAuthoritySource, type AuthoritySourceCheck} from "./authority_source.ts";
/** One canonical transaction for an observation and its independent successors.
 * Network polling, quota settlement and display delivery are separate effects. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {normalizeRegisteredTodoAgents, normalizeTodoAgent} from "./todo_agents.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {requireBoolean} from "../runtime_decode.ts";
import {CoordinationCommandReceipt} from "./command_receipt.ts";
import {decodeTaskLeaseProof, type TaskLeaseProof} from "./task_lease_proof.ts";
import {monitorPollRequestHash, normalizeMonitorPollFields, planMonitorBatch} from "../scheduler/monitor_batch.ts";
import {evaluateTodoResumeConditionsFromSource, todoResumeDependencyId, TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION} from "../todos/resume_condition.ts";
export {monitorPollRequestHash} from "../scheduler/monitor_batch.ts";

export const COORDINATION_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v0";
export const COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v1";
export const COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v2";
export const COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v3";
/** v4 can only recover an existing receipt; older runtimes reject its schema. */
export const COORDINATION_MONITOR_RECEIPT_ONLY_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v4";
export const COORDINATION_MONITOR_POLL_RESULT_SCHEMA = "loopx_coordination_monitor_poll_result_v0";
const RECEIPT_SCHEMA = "loopx_coordination_monitor_poll_receipt_v0";

export interface CoordinationMonitorPollInput {
  goal_id: string;
  operation_id: string;
  actor_agent_id: string | null;
  registered_agents: readonly string[];
  dry_run: boolean;
  observation: JsonObject;
  intent: JsonObject;
  lease_proof?: TaskLeaseProof | null;
  /** New auxiliary effects must qualify dependencies on the commit head. */
  gate_scope_guard?: boolean;
  /** A damaged display cannot authorize a fresh canonical observation. */
  receipt_only?: boolean;
  /** Authority clock supplied by the runtime, never observation.generated_at. */
  now?: Date;
  /** Physical runtime already selected by the canonical provider. */
  runtime_root?: string;
  /** Capacity depends on this dispatch's runtime facts and admitted condition. */
  available_capabilities?: readonly string[];
  admitted_resume_when?: string;
}

type CoordinationMonitorPollFailureKind = "decision_rejection" | "protocol_failure";

function failure(
  reason_code: string,
  reason: string,
  kind: CoordinationMonitorPollFailureKind = "protocol_failure",
): JsonObject & {schema_version: typeof COORDINATION_MONITOR_POLL_RESULT_SCHEMA} {
  return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA, status: "failed",
    changed: false, failure_kind: kind, reason_code, reason};
}

function rejection(
  reason_code: string,
  reason: string,
): JsonObject & {schema_version: typeof COORDINATION_MONITOR_POLL_RESULT_SCHEMA} {
  return failure(reason_code, reason, "decision_rejection");
}

function monitorReceipt(input: CoordinationMonitorPollInput, hash: string) {
  return new CoordinationCommandReceipt({result_schema: COORDINATION_MONITOR_POLL_RESULT_SCHEMA,
    identity: {schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id,
      operation_id: input.operation_id, request_sha256: hash},
    failure: (code, reason) => failure(code, reason,
      code === "coordination_operation_identity_mismatch"
        ? "decision_rejection"
        : "protocol_failure"),
    decode(original, phase) {
      const writeback = canonicalAuthorityObject(original.writeback, "Monitor writeback");
      if (writeback.schema_version !== "monitor_poll_todo_writeback_v0" ||
          writeback.goal_id !== input.goal_id || writeback.monitor_effect_id !== input.operation_id ||
          !Array.isArray(writeback.next_todos)) {
        throw new AuthorityStoreProtocolError("Monitor receipt writeback identity or successors invalid");
      }
      let proof: TaskLeaseProof | null;
      try { proof = decodeTaskLeaseProof(writeback.lease_proof); }
      catch { throw new AuthorityStoreProtocolError("Monitor receipt lease proof is malformed"); }
      if (canonicalAuthoritySha256(proof ?? {}) !== canonicalAuthoritySha256(input.lease_proof ?? {})) {
        throw new AuthorityStoreProtocolError("Monitor receipt belongs to a different lease proof");
      }
      return {fields: {writeback: {...writeback, provider_replayed: phase === "replayed"}}, changed: true};
    }});
}

type NormalizedMonitorPollInput = CoordinationMonitorPollInput & {now: Date; lease_proof: TaskLeaseProof | null};

function normalize(raw: CoordinationMonitorPollInput): NormalizedMonitorPollInput {
  const input = {...raw, goal_id: requireAuthorityStoreId(raw.goal_id, "goal id"),
    operation_id: requireAuthorityStoreId(raw.operation_id, "operation id"),
    actor_agent_id: raw.actor_agent_id == null ? null : normalizeTodoAgent(raw.actor_agent_id, "actor_agent_id"),
    registered_agents: normalizeRegisteredTodoAgents(raw.registered_agents),
    dry_run: requireBoolean(raw.dry_run, "dry_run"),
    ...normalizeMonitorPollFields(raw.observation, raw.intent),
    gate_scope_guard: raw.gate_scope_guard == null ? false : requireBoolean(raw.gate_scope_guard, "gate_scope_guard"),
    receipt_only: raw.receipt_only == null ? false : requireBoolean(raw.receipt_only, "receipt_only"),
    lease_proof: decodeTaskLeaseProof(raw.lease_proof), now: raw.now ?? new Date()};
  return input;
}

async function assertMonitorWait(
  input: NormalizedMonitorPollInput, monitor: JsonObject, todos: readonly JsonObject[],
): Promise<void> {
  if (typeof monitor.resume_when !== "string" || !monitor.resume_when.trim()) return;
  const evaluated = await evaluateTodoResumeConditionsFromSource({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION, items: [monitor],
    source_items: todoResumeDependencyId(monitor.resume_when) === null ? [] : todos,
    evaluated_at: input.now.toISOString(), available_capabilities: input.available_capabilities ?? null,
    ...(input.runtime_root === undefined ? {} : {rollout_event_source: {
      runtime_root: input.runtime_root, goal_id: input.goal_id,
    }}),
  });
  const [wait] = evaluated.conditions as {condition: JsonObject}[];
  if (wait?.condition.satisfied !== true) throw new Error("Monitor resume condition is not satisfied");
  if (wait.condition.kind === "capacity_available" && wait.condition.resume_when !== input.admitted_resume_when) {
    throw new Error("Monitor capacity wait changed since admission; re-evaluate the current condition");
  }
}

async function planWriteback(input: NormalizedMonitorPollInput, head: JsonObject) {
  const indexed = indexCoordinationProjection(head, input.goal_id);
  validateCoordinationTodoReadModel(head, input.goal_id);
  const readModel = canonicalAuthorityObject(head.todo_read_model, "Todo read model");
  const todos = [...indexed.todos.values()];
  const result = planMonitorBatch({...input, todos,
    read_model_schema: String(readModel.schema_version),
    admit_monitor(monitor) {
      const rejected = monitorMutationRejection({goal_id: input.goal_id, todo: monitor, todos: indexed.todos,
        lease: indexed.leases.get(String(monitor.todo_id)), handoff_mode: head.handoff_mode,
        actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        operation: "observe", proof: input.lease_proof, now: input.now});
      if (rejected !== null) throw new Error(rejected.reason);
    }});
  const writeback = canonicalAuthorityObject(result.writeback, "Monitor writeback");
  const monitor = indexed.todos.get(String(writeback.todo_id));
  if (!monitor) throw new Error("Monitor target is unavailable in the commit snapshot");
  await assertMonitorWait(input, monitor, todos);
  return {mutations: result.mutations as {kind: "todo_upsert"; todo: JsonObject}[], writeback};
}

export async function executeCoordinationMonitorPoll(store: AuthorityStore,
  raw: CoordinationMonitorPollInput,
  authoritySourcesCurrent: AuthoritySourceCheck = uncheckedAuthoritySource): Promise<JsonObject> {
  let input: NormalizedMonitorPollInput;
  try { input = normalize(raw); }
  catch (error) { return rejection("invalid_monitor_poll_request", String(error)); }
  // Original wire identity, before any normalization/default route inference.
  const hash = monitorPollRequestHash(input);
  const receipt = monitorReceipt(input, hash);
  const previous = await receipt.read(store);
  if (previous) return previous;
  if (input.receipt_only) return rejection("monitor_poll_original_receipt_required",
    "Original Monitor receipt is unavailable; restore the historical source before retrying recovery");
  if (!await authoritySourcesCurrent()) {
    return rejection(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  }
  const observation = await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA, ...head};
  let plan: Awaited<ReturnType<typeof planWriteback>>;
  try { plan = await planWriteback(input, head.head); }
  catch (error) {
    // Receipt lookup succeeded and planning failed before commit. Only this
    // boundary can certify no effect; outages and commit failures cannot.
    return {...rejection("monitor_poll_rejected", error instanceof Error ? error.message : String(error)),
      no_effect: {schema_version: "monitor_poll_no_effect_v0", goal_id: input.goal_id,
        operation_id: input.operation_id, request_sha256: hash}};
  }
  if (!await authoritySourcesCurrent()) {
    return rejection(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  }
  if (input.dry_run) return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA,
    status: "planned", changed: true, writeback: plan.writeback, provider_revision: head.provider_revision};
  const commit = prepareCoordinationProjectionCommit({goal_id: input.goal_id, operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision, projection: head.head, mutations: plan.mutations});
  commit.receipts = [{schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id, operation_id: input.operation_id,
    request_sha256: hash, writeback: plan.writeback}];
  return receipt.commit(store, commit);
}
