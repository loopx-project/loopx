import {monitorMutationRejection} from "./todo_monitor_cycle.ts";
import {AUTHORITY_SOURCE_CHANGED, uncheckedAuthoritySource, type AuthoritySourceCheck} from "./authority_source.ts";
/** One canonical transaction for an observation and its independent successors.
 * Network polling, quota settlement and display delivery are separate effects. */
import type {JsonObject} from "../effect_program.ts";
import type {AuthorityStore} from "./authority_store.ts";
import {AuthorityStoreProtocolError, canonicalAuthorityObject, canonicalAuthoritySha256, requireAuthorityStoreId} from "./authority_store_codec.ts";
import {normalizeRegisteredTodoAgents, normalizeTodoAgent} from "./todo_agents.ts";
import {indexCoordinationProjection, prepareCoordinationProjectionCommit, validateCoordinationTodoReadModel} from "./coordination_projection.ts";
import {TODO_DOMAIN_ITEM_SCHEMA} from "./coordination_state_contract.ts";
import {planCoordinationTodoCreate} from "./todo_create.ts";
import {planMonitorMetadata, TODO_MONITOR_METADATA_REQUEST_SCHEMA} from "../todos/monitor_metadata.ts";
import {planMonitorSuccessor, selectMonitorTodo, MONITOR_SUCCESSOR_REQUEST_SCHEMA} from "../scheduler/monitor_successor.ts";
import {optionalNonEmptyString, requireBoolean} from "../runtime_decode.ts";
import {planTodoAuthoringScope, TODO_AUTHORING_SCOPE_REQUEST_SCHEMA} from "../todos/authoring_scope.ts";
import {CoordinationCommandReceipt} from "./command_receipt.ts";
import {decodeTaskLeaseProof, type TaskLeaseProof} from "./task_lease_proof.ts";

export const COORDINATION_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v0";
export const COORDINATION_LEASED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v1";
export const COORDINATION_WITNESSED_MONITOR_POLL_REQUEST_SCHEMA = "loopx_coordination_monitor_poll_request_v2";
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
    observation: canonicalAuthorityObject(raw.observation, "Monitor observation"),
    intent: canonicalAuthorityObject(raw.intent, "Monitor successor intent"),
    lease_proof: decodeTaskLeaseProof(raw.lease_proof), now: raw.now ?? new Date()};
  const allowed = new Set(["todo_id", "target_key", "generated_at", "result_hash", "material_change", "cadence", "next_due_at", "reason_summary"]);
  for (const key of Object.keys(input.observation)) if (!allowed.has(key)) throw new Error(`unsupported Monitor observation field: ${key}`);
  const intentFields = new Set(["next_agent_todo", "next_action_kind", "next_task_repository", "next_required_capabilities",
    "next_continuation_policy", "next_target_key", "next_claimed_by", "next_user_todo", "next_user_task_class"]);
  for (const key of Object.keys(input.intent)) if (!intentFields.has(key)) throw new Error(`unsupported Monitor successor field: ${key}`);
  return input;
}

function planWriteback(input: NormalizedMonitorPollInput, head: JsonObject) {
  const indexed = indexCoordinationProjection(head, input.goal_id);
  validateCoordinationTodoReadModel(head, input.goal_id);
  const observation = input.observation;
  const monitor = selectMonitorTodo([...indexed.todos.values()],
    optionalNonEmptyString(observation.todo_id, "todo_id"), optionalNonEmptyString(observation.target_key, "target_key"));
  const actor = input.actor_agent_id;
  const rejected = monitorMutationRejection({goal_id: input.goal_id, todo: monitor, lease: indexed.leases.get(String(monitor.todo_id)),
    handoff_mode: head.handoff_mode, actor_agent_id: actor, registered_agents: input.registered_agents,
    operation: "observe", proof: input.lease_proof, now: input.now});
  if (rejected !== null) throw new Error(rejected.reason);
  const successorPlan = planMonitorSuccessor({schema_version: MONITOR_SUCCESSOR_REQUEST_SCHEMA,
    todo_id: monitor.todo_id, result_hash: observation.result_hash, source_task_repository: monitor.task_repository ?? null,
    intent: {...input.intent, material_change: observation.material_change}});
  const intent = canonicalAuthorityObject(successorPlan.intent, "Monitor successor intent");
  const route = canonicalAuthorityObject(successorPlan.agent_route, "Monitor successor route");
  const monitorPlan = planMonitorMetadata({schema_version: TODO_MONITOR_METADATA_REQUEST_SCHEMA,
    existing: monitor, role: "agent", task_class: "continuous_monitor", enforce_boundedness: false,
    observation: {...observation, monitor_effect_id: input.operation_id}});
  const transition = canonicalAuthorityObject(monitorPlan.transition, "Monitor transition");
  if ((intent.next_agent_todo || intent.next_user_todo) && transition.material_change_applied !== true) {
    throw new Error("successor authoring requires a new material-change generation; poll without successor options for unchanged evidence");
  }
  const metadata = canonicalAuthorityObject(monitorPlan.metadata, "Monitor metadata");
  // Generation is an integer in the persisted Todo contract. The older
  // observation tokens remain strings (including "0" and "false"); retain
  // their existing wire types so permanent Markdown projection is lossless.
  if (metadata.material_change_generation != null) metadata.material_change_generation = Number(metadata.material_change_generation);
  const updated: JsonObject = {...monitor, last_actor_agent_id: actor, updated_at: observation.generated_at};
  for (const [key, value] of Object.entries(metadata)) {
    if (value === null) delete updated[key]; else updated[key] = value;
  }
  const reason = optionalNonEmptyString(observation.reason_summary, "reason_summary");
  if (reason) updated.reason = reason;
  const nextTodos: JsonObject[] = [];
  const plannedTodos = new Map(indexed.todos);
  const mutations: {kind: "todo_upsert"; todo: JsonObject}[] = [{kind: "todo_upsert", todo: updated}];
  const readModel = canonicalAuthorityObject(head.todo_read_model, "Todo read model");
  for (const role of ["agent", "user"] as const) {
    const text = intent[role === "agent" ? "next_agent_todo" : "next_user_todo"];
    if (!text) continue;
    const id = `todo_${canonicalAuthoritySha256({monitor: monitor.todo_id,
      generation: transition.material_change_generation, role}).slice(0, 24)}`;
    const todo: JsonObject = {schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: id, role, text,
      status: "open", done: false, archive_state: "active",
      task_class: role === "agent" ? "advancement_task" : intent.next_user_task_class};
    if (role === "agent") {
      Object.assign(todo, Object.fromEntries(Object.entries(route).filter(([, value]) => value !== null)),
        {unblocks_todo_id: monitor.todo_id});
    } else {
      // Reuse public authoring scope: an actor-bound gate, never an inferred
      // all-agent/global gate. User actions retain their actor binding too.
      const scope = planTodoAuthoringScope({schema_version: TODO_AUTHORING_SCOPE_REQUEST_SCHEMA,
        command: "create", role, todo: {}, goal_id: input.goal_id, registered_agents: input.registered_agents,
        intent: {task_class: todo.task_class, actor_agent_id: actor}});
      for (const key of ["bound_agent", "blocks_agent", "goal_bound", "global_gate"]) {
        if (scope[key] != null && scope[key] !== false) todo[key] = scope[key];
      }
      if (todo.task_class === "user_gate") Object.assign(todo, {action_kind: "gate", unblocks_todo_id: monitor.todo_id});
    }
    const created = planCoordinationTodoCreate({goal_id: input.goal_id, operation_id: input.operation_id,
      actor_agent_id: actor, registered_agents: input.registered_agents, dry_run: input.dry_run,
      now: new Date(String(observation.generated_at)), todo}, plannedTodos, readModel.schema_version);
    if (created.status !== "planned" && created.status !== "no_change") throw new Error(String(created.reason));
    const record = canonicalAuthorityObject(created.todo, "successor Todo");
    nextTodos.push({...record, todo: record.text, ok: true, dry_run: input.dry_run});
    if (created.status === "planned") mutations.push({kind: "todo_upsert", todo: record});
    plannedTodos.set(String(record.todo_id), record);
  }
  const receiptFields = ["todo_id", "role", "task_class", "action_kind", "task_repository",
    "continuation_policy", "required_capabilities", "claimed_by", "unblocks_todo_id", "target_key"];
  const writeback: JsonObject = {schema_version: "monitor_poll_todo_writeback_v0", dry_run: input.dry_run,
    goal_id: input.goal_id, todo_id: monitor.todo_id, monitor_effect_id: input.operation_id,
    target_key: transition.target_key || null, result_hash: observation.result_hash,
    material_change: observation.material_change, material_change_generation: transition.material_change_generation,
    consecutive_no_change: transition.consecutive_no_change, last_checked_at: observation.generated_at,
    next_due_at: transition.next_due_at ?? null, cadence: transition.cadence || null,
    todo_update: {ok: true, todo_id: monitor.todo_id, monitor_poll_transition: transition},
    ...(input.lease_proof ? {lease_proof: {...input.lease_proof}} : {}),
    next_todos: nextTodos, successor_receipts: nextTodos.map(todo => Object.fromEntries(
      receiptFields.filter(key => todo[key] != null).map(key => [key, todo[key]]))), provider_replayed: false};
  return {mutations, writeback};
}

export async function executeCoordinationMonitorPoll(store: AuthorityStore,
  raw: CoordinationMonitorPollInput,
  authoritySourcesCurrent: AuthoritySourceCheck = uncheckedAuthoritySource): Promise<JsonObject> {
  let input: NormalizedMonitorPollInput;
  try { input = normalize(raw); }
  catch (error) { return failure("invalid_monitor_poll_request", String(error)); }
  // Original wire identity, before any normalization/default route inference.
  const hash = canonicalAuthoritySha256({goal_id: input.goal_id, observation: input.observation,
    intent: input.intent, actor_agent_id: input.actor_agent_id, dry_run: input.dry_run,
    ...(input.lease_proof ? {lease_proof: input.lease_proof} : {})});
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
  catch (error) { return failure("monitor_poll_rejected", error instanceof Error ? error.message : String(error)); }
  if (!await authoritySourcesCurrent()) return failure(AUTHORITY_SOURCE_CHANGED.code, AUTHORITY_SOURCE_CHANGED.reason);
  if (input.dry_run) return {schema_version: COORDINATION_MONITOR_POLL_RESULT_SCHEMA,
    status: "planned", changed: true, writeback: plan.writeback, provider_revision: head.provider_revision};
  const commit = prepareCoordinationProjectionCommit({goal_id: input.goal_id, operation_id: input.operation_id,
    expected_provider_revision: head.provider_revision, projection: head.head, mutations: plan.mutations});
  commit.receipts = [{schema_version: RECEIPT_SCHEMA, goal_id: input.goal_id, operation_id: input.operation_id,
    request_sha256: hash, writeback: plan.writeback}];
  return receipt.commit(store, commit);
}
