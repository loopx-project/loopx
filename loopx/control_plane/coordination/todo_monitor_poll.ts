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
export {monitorPollRequestHash} from "../scheduler/monitor_batch.ts";

export const COORDINATION_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v0";
export const COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v1";
export const COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v2";
export const COORDINATION_GUARDED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v3";
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
  /** Authority clock supplied by the runtime, never observation.generated_at. */
  now?: Date;
}

function failure(reason_code: string, reason: string): JsonObject & {schema_version: typeof COORDINATION_MONITOR_POLL_RESULT_SCHEMA} {
  return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA, status: "failed", changed: false, reason_code, reason};
}

function monitorReceipt(input: CoordinationMonitorPollInput, hash: string) {
  return new CoordinationCommandReceipt({result_schema: COORDINATION_MONITOR_POLL_RESULT_SCHEMA,
    identity: {schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id,
      operation_id: input.operation_id, request_sha256: hash}, failure,
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
    lease_proof: decodeTaskLeaseProof(raw.lease_proof), now: raw.now ?? new Date()};
  return input;
}

function planWriteback(input: NormalizedMonitorPollInput, head: JsonObject) {
  const indexed = indexCoordinationProjection(head, input.goal_id);
  validateCoordinationTodoReadModel(head, input.goal_id);
  const readModel = canonicalAuthorityObject(head.todo_read_model, "Todo read model");
  const result = planMonitorBatch({...input, todos: [...indexed.todos.values()],
    read_model_schema: String(readModel.schema_version),
    admit_monitor(monitor) {
      const rejected = monitorMutationRejection({goal_id: input.goal_id, todo: monitor,
        lease: indexed.leases.get(String(monitor.todo_id)), handoff_mode: head.handoff_mode,
        actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        operation: "observe", proof: input.lease_proof, now: input.now});
      if (rejected !== null) throw new Error(rejected.reason);
    }});
  return {mutations: result.mutations as {kind: "todo_upsert"; todo: JsonObject}[],
    writeback: canonicalAuthorityObject(result.writeback, "Monitor writeback")};
}

export async function executeCoordinationMonitorPoll(store: AuthorityStore,
  raw: CoordinationMonitorPollInput,
  authoritySourcesCurrent: AuthoritySourceCheck = uncheckedAuthoritySource): Promise<JsonObject> {
  let input: NormalizedMonitorPollInput;
  try { input = normalize(raw); }
  catch (error) { return failure("invalid_monitor_poll_request", String(error)); }
  // Original wire identity, before any normalization/default route inference.
  const hash = monitorPollRequestHash(input);
  const receipt = monitorReceipt(input, hash);
  const previous = await receipt.read(store);
  if (previous) return previous;
  if (!await authoritySourcesCurrent()) return failure(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  const observation = await receipt.observe(store);
  if (observation.kind === "receipt") return observation.result;
  const head = observation.authority;
  if (head.status !== "loaded") return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA, ...head};
  let plan: ReturnType<typeof planWriteback>;
  try { plan = planWriteback(input, head.head); }
  catch (error) {
    // Receipt lookup succeeded and planning failed before commit. Only this
    // boundary can certify no effect; outages and commit failures cannot.
    return {...failure("monitor_poll_rejected", error instanceof Error ? error.message : String(error)),
      no_effect: {schema_version: "monitor_poll_no_effect_v0", goal_id: input.goal_id,
        operation_id: input.operation_id, request_sha256: hash}};
  }
  if (!await authoritySourcesCurrent()) return failure(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  if (input.dry_run) return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA,
    status: "planned", changed: true, writeback: plan.writeback, provider_revision: head.provider_revision};
  const commit = prepareCoordinationProjectionCommit({goal_id: input.goal_id, operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision, projection: head.head, mutations: plan.mutations});
  commit.receipts = [{schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id, operation_id: input.operation_id,
    request_sha256: hash, writeback: plan.writeback}];
  return receipt.commit(store, commit);
}
