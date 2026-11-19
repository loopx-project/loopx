/** Admission gates and record, markdown, index, and payload builders for monitor-poll commits. */
import { AgentScopeFrontierAction } from "../agents/agent_scope_frontier.generated.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import type { JsonObject } from "../effect_program.ts";
import { jsonObject, requireJsonObject as requiredObject } from "../runtime_decode.ts";
import { isMonitorUnavailableWait } from "./blocked_wait.ts";
import { EffectiveAction, type QuotaEffectiveActionValue } from "./effective_action.generated.ts";
import {
  candidateMatches,
  compactDecision,
  compactProviderWriteback,
  exactBlockedWait,
  monitorLaneFacts,
  monitorTarget,
  optionalTrimmed,
  QUOTA_MONITOR_POLL_CLASSIFICATION,
  QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
  successorReceipts,
  type MonitorDecision,
  type MonitorLaneFacts,
  type MonitorObservation,
  type MonitorRequest,
} from "./monitor_poll_codec.ts";
import {
  QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
  readQuotaSettlementForAdmittedOwnerFromSnapshot,
  readQuotaSettlementSnapshot,
} from "./settlement_readback.ts";
import { quotaGoalRef } from "./source_admission.ts";
import { readMonitorHeartbeatRecommendation } from "./turn_envelope.ts";

const EXTERNAL_MONITOR_POLICIES = new Set([
  "material_transition_only",
  "read_only_observation_then_no_spend_if_unchanged",
]);
const EXTERNAL_MONITOR_REASON_CODES = new Set([
  "external_monitor_context",
  "external_evidence_poll_signal",
]);

export interface Admission {
  blocked: boolean;
  due: boolean;
  external: boolean;
}

function blockedSuccessorAllowed(decision: MonitorDecision): boolean {
  return ([
    AgentScopeFrontierAction.AGENT_SCOPE_WAIT, EffectiveAction.MONITOR_QUIET_SKIP,
  ] satisfies readonly QuotaEffectiveActionValue[] as readonly string[]).includes(
    decision.effective_action ?? "",
  ) && !decision.should_run &&
    !decision.requires_user_action && exactBlockedWait(decision) !== null;
}

function externalMonitorAllowed(decision: MonitorDecision, facts: MonitorLaneFacts): boolean {
  if (decision.requires_user_action || !decision.should_run) return false;
  if (facts.external_observation_required) return true;
  if (!facts.must_attempt_work) return false;
  if (!EXTERNAL_MONITOR_POLICIES.has(facts.monitor_policy)) return false;
  if ([...facts.reason_codes].some((code) => EXTERNAL_MONITOR_REASON_CODES.has(code))) return true;
  return facts.has_external_observation;
}

function dueMonitorAllowed(
  decision: MonitorDecision,
  observation: MonitorObservation,
  facts: MonitorLaneFacts,
): boolean {
  if (!facts.must_attempt_work) return false;
  const candidate = decision.due_monitor_candidates.find((item) => candidateMatches(item, observation));
  if (facts.obligation === "attempt_due_monitor") return Boolean(candidate);
  if (facts.reason_codes.has("due_monitor_context") && candidate) return true;
  const registry = decision.registry_due_monitor;
  return facts.reason_codes.has("due_monitor_context") && registry.due === true &&
    registry.claimed_by === decision.agent_id && candidateMatches(registry, observation);
}

function monitorGateAllowsObservation(decision: MonitorDecision): boolean {
  if (!decision.requires_user_action) return true;
  const gateScope = decision.auxiliary_gate_scope;
  const monitor = decision.registry_due_monitor;
  return decision.safe_bypass_allowed &&
    decision.safe_bypass_kind === "scoped_user_gate_fallback" &&
    gateScope?.schema_version === "todo_gate_scope_projection_v0" &&
    gateScope.agent_id === decision.agent_id && gateScope.todo_id === monitor.todo_id &&
    gateScope.state === "independent" && typeof gateScope.gate_count === "number" &&
    Number.isInteger(gateScope.gate_count) && gateScope.gate_count > 0;
}

export async function readMonitorSettlement(request: MonitorRequest): Promise<JsonObject | null> {
  const todoId = request.observation.settlement_todo_id ?? request.observation.todo_id;
  // The Turn/effect and run record belong to the decision Agent; provider actor
  // metadata cannot select a different historical settlement to evade its fence.
  const agentId = request.decision.agent_id;
  if (!request.runtime_root || !request.turn_instance_id || !agentId || !todoId) return null;
  const snapshot = await readQuotaSettlementSnapshot(request.runtime_root, request.goal_id);
  return readQuotaSettlementForAdmittedOwnerFromSnapshot({
    schema_version: QUOTA_SETTLEMENT_READBACK_REQUEST_SCHEMA,
    runtime_root: request.runtime_root,
    goal_id: request.goal_id,
    agent_id: agentId,
    todo_id: todoId,
    turn_instance_id: request.turn_instance_id,
    replan_obligation_id: null,
    infer_turn_instance_id: false,
    allow_unbound_binding: false,
  }, request.owner, snapshot);
}

async function auxiliaryMonitorAllowed(
  request: MonitorRequest,
  historicalAdmission: boolean,
): Promise<boolean | null> {
  const { decision, observation } = request;
  const settlementTodo = observation.settlement_todo_id;
  if (!settlementTodo || settlementTodo === observation.todo_id) return null;
  const conflict = () => new EffectRuntimeRequestError(
    "turn-scoped monitor-poll conflicts with the committed advancement settlement identity: " +
      `settlement Todo ${settlementTodo}, observation Todo ${observation.todo_id}`,
    "heartbeat_receipt_identity_conflict",
  );
  const todo = decision.auxiliary_settlement_todo;
  // A verified pending v1 receipt already admitted this exact observation.
  // Earlier receipts predate the lifecycle-fact field; preserve their recovery
  // basis, never reuse it for a new effect or a changed settlement binding.
  if (historicalAdmission && todo === null) return null;
  if (
    !request.runtime_root || !request.turn_instance_id || !decision.agent_id ||
    observation.actor_agent_id !== decision.agent_id ||
    todo === null || todo.todo_id !== settlementTodo || todo.task_class !== "advancement_task" ||
    !["open", "done", "blocked"].includes(String(todo.status)) ||
    (todo.claimed_by != null && todo.claimed_by !== decision.agent_id) ||
    (todo.excluded_agents != null && (!Array.isArray(todo.excluded_agents) ||
      todo.excluded_agents.some((value) => typeof value !== "string") ||
      todo.excluded_agents.includes(decision.agent_id)))
  ) throw conflict();
  if (!historicalAdmission) {
    const settlement = await readMonitorSettlement(request);
    const progress = jsonObject(settlement?.progress);
    // The advancement's single debit is not an observation-admission fence.
    // A settled exact binding still permits independently admitted due Monitors.
    if (
      settlement?.found !== true || jsonObject(jsonObject(settlement.identity)?.result)?.failure !== null ||
      progress?.schema_version !== "quota_settlement_progress_v0" ||
      progress.state === "identity_required"
    ) throw conflict();
    // A later lifecycle hold cannot invalidate an already settled Turn's
    // independent observation. An unsettled hold still requires recovery;
    // neither case grants execution of the held advancement Todo.
    if (todo.status === "blocked" && progress.state !== "settled") throw conflict();
  }
  const monitor = decision.registry_due_monitor;
  // Retain ordinary quota/due-work admission and capability/gate projections;
  // lifecycle lookup must not become a second should-run bypass.
  return monitorGateAllowsObservation(decision) &&
    dueMonitorAllowed(decision, observation, monitorLaneFacts(decision)) &&
    monitor.due === true && candidateMatches(monitor, observation) &&
    (monitor.claimed_by == null || monitor.claimed_by === decision.agent_id);
}

export async function admission(
  request: MonitorRequest,
  historicalAdmission = false,
): Promise<Admission> {
  const observation = request.observation;
  const settlementTodo = observation.settlement_todo_id ?? observation.todo_id;
  if (settlementTodo && (!observation.todo_id || settlementTodo === observation.todo_id)) {
    const settlement = await readMonitorSettlement(request);
    // The readback owner verifies admission, exact identity and durable blocked
    // evidence. A late observation cannot replace that frozen no-spend outcome.
    // Durable poll replay returns before admission; independent auxiliary
    // Monitors retain their advancement-bound admission below.
    if (
      jsonObject(settlement?.progress)?.closeout_kind === "typed_blocked_writeback_no_spend" &&
      isMonitorUnavailableWait(jsonObject(settlement?.writeback_run)?.blocked_retry, settlementTodo)
    ) {
      throw new EffectRuntimeRequestError(
        "monitor-poll conflicts with the committed unavailable attempt; use a fresh Turn for a new observation",
        "monitor_poll_admission_rejected",
      );
    }
  }
  const laneFacts = monitorLaneFacts(request.decision);
  const blocked = blockedSuccessorAllowed(request.decision);
  const external = externalMonitorAllowed(request.decision, laneFacts);
  const auxiliary = await auxiliaryMonitorAllowed(request, historicalAdmission);
  const due = auxiliary ?? dueMonitorAllowed(request.decision, request.observation, laneFacts);
  if (auxiliary === false) {
    throw new EffectRuntimeRequestError(
      "auxiliary monitor-poll requires its own due Monitor target", "monitor_poll_admission_rejected",
    );
  }
  if (due && !monitorGateAllowsObservation(request.decision)) {
    throw new EffectRuntimeRequestError(
      "Monitor observation is blocked by current User gate dependencies", "monitor_poll_admission_rejected",
    );
  }
  if (request.decision.effective_action !== EffectiveAction.MONITOR_QUIET_SKIP && !external && !due && !blocked) {
    throw new EffectRuntimeRequestError(
      "monitor-poll requires monitor_quiet_skip, due monitor todo, external monitor observation, or exact blocked successor wait",
      "monitor_poll_admission_rejected",
    );
  }
  if (
    readMonitorHeartbeatRecommendation(request.decision).recommended_mode !== "monitor_quiet_until_material_transition" &&
    !external && !due && !blocked
  ) {
    throw new EffectRuntimeRequestError(
      "quota monitor-poll requires monitor_quiet_until_material_transition or exact blocked successor wait mode",
      "monitor_poll_admission_rejected",
    );
  }
  return { blocked, due, external };
}

export function buildRecord(request: MonitorRequest, allowed: Admission): JsonObject {
  const material = request.observation.material_change;
  let kind = "monitor";
  let prefix = "monitor";
  if (allowed.blocked) { kind = "blocked successor wait"; prefix = "blocked_successor_wait"; }
  else if (allowed.external) { kind = "external monitor"; prefix = "external_monitor"; }
  else if (allowed.due) { kind = "due monitor"; prefix = "due_monitor"; }
  const heartbeat = readMonitorHeartbeatRecommendation(request.decision);
  const recommendationReason = typeof heartbeat.reason === "string" ? heartbeat.reason : null;
  let monitorMode: string;
  let defaultReason: string;
  let healthCheck: string;
  if (material) {
    monitorMode = `${prefix}_material_transition`;
    defaultReason = `${kind} observation produced a material transition`;
    healthCheck = `${kind} material transition observed; follow-up state updated; no quota spend by monitor-poll`;
  } else if (allowed.blocked) {
    monitorMode = "blocked_successor_wait_without_material_transition";
    defaultReason = recommendationReason ?? request.decision.reason ?? "exact blocked successor wait produced no material transition";
    healthCheck = "exact blocked successor wait unchanged; no quota spend; bounded replan after two identical frontier observations";
  } else if (allowed.external) {
    monitorMode = "external_monitor_observed_without_material_transition";
    defaultReason = "external monitor observation produced no material transition";
    healthCheck = "external monitor observation unchanged; no quota spend; no material transition";
  } else if (allowed.due) {
    monitorMode = "due_monitor_observed_without_material_transition";
    defaultReason = recommendationReason ?? request.decision.reason ?? "due monitor poll had no material transition";
    healthCheck = "due monitor observation unchanged; no quota spend; next due updated";
  } else {
    monitorMode = "monitor_quiet_until_material_transition";
    defaultReason = recommendationReason ?? request.decision.reason ?? "monitor-only poll had no material transition";
    healthCheck = "monitor-only poll unchanged; no quota spend; no material transition";
  }
  const target = monitorTarget(request.decision, monitorMode);
  const event: JsonObject = {
    event_type: QUOTA_MONITOR_POLL_CLASSIFICATION,
    source: request.source,
    monitor_mode: monitorMode,
    monitor_target: target,
    reason_summary: request.observation.reason_summary ?? defaultReason,
    material_change: material,
    ...(request.observation.material_change_scope == null ? {} : {
      material_change_scope: request.observation.material_change_scope,
    }),
    settlement_todo_id: request.observation.settlement_todo_id,
    todo_id: request.observation.todo_id,
    target_key: request.observation.target_key,
    result_hash: request.observation.result_hash,
    before: compactDecision(request.decision),
  };
  const record: JsonObject = {
    generated_at: request.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_MONITOR_POLL_CLASSIFICATION,
    material_change: material,
    recommended_action: request.decision.recommended_action ?? recommendationReason ?? request.decision.reason,
    health_check: healthCheck,
    delivery_outcome: material ? "outcome_progress" : "surface_only",
    settlement_todo_id: request.observation.settlement_todo_id,
    monitor_target: target,
    monitor_event: event,
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) { record.goal_ref = goalRef; event.goal_ref = goalRef; }
  if (request.decision.agent_id) { record.agent_id = request.decision.agent_id; event.agent_id = request.decision.agent_id; }
  if (request.turn_instance_id) { record.turn_instance_id = request.turn_instance_id; event.turn_instance_id = request.turn_instance_id; }
  if (request.provider_receipt) event.todo_writeback = compactProviderWriteback(request.provider_receipt);
  if (request.status_reload_warning) event.status_reload_warning = request.status_reload_warning;
  return record;
}

export function indexRecordFor(
  request: MonitorRequest,
  record: JsonObject,
  jsonPath: string,
  markdownPath: string,
  fingerprint: string,
): JsonObject {
  const event = requiredObject(record.monitor_event, "record.monitor_event");
  const indexRecord: JsonObject = {
    generated_at: record.generated_at,
    goal_id: request.goal_id,
    classification: QUOTA_MONITOR_POLL_CLASSIFICATION,
    recommended_action: record.recommended_action,
    health_check: record.health_check,
    delivery_outcome: record.delivery_outcome,
    monitor_target: record.monitor_target,
    json_path: jsonPath,
    markdown_path: markdownPath,
    quota_monitor_poll_commit: {
      schema_version: QUOTA_MONITOR_POLL_COMMIT_RECEIPT_SCHEMA,
      effect_id: request.effect_id,
      request_digest: fingerprint,
    },
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) indexRecord.goal_ref = goalRef;
  for (const [field, value] of Object.entries({
    agent_id: record.agent_id,
    turn_instance_id: record.turn_instance_id,
    settlement_todo_id: event.settlement_todo_id,
    todo_id: event.todo_id,
    target_key: event.target_key,
    material_change: event.material_change === true ? true : null,
  })) {
    if (value !== null && value !== undefined && value !== "") indexRecord[field] = value;
  }
  const successorIds = successorReceipts(request.provider_receipt)
    .map((receipt) => String(receipt.todo_id ?? "").trim()).filter(Boolean);
  if (successorIds.length) indexRecord.successor_todo_ids = successorIds;
  return indexRecord;
}

function pyValue(value: unknown): string {
  if (value === true) return "True";
  if (value === false) return "False";
  if (value === null || value === undefined) return "None";
  return String(value);
}

export function monitorMarkdown(record: JsonObject): string {
  const event = requiredObject(record.monitor_event, "record.monitor_event");
  const before = requiredObject(event.before, "record.monitor_event.before");
  const target = requiredObject(event.monitor_target, "record.monitor_event.monitor_target");
  const writeback = jsonObject(event.todo_writeback) ?? {};
  const lines = [
    "# LoopX Quota Monitor Poll",
    "",
    `- goal_id: \`${pyValue(record.goal_id)}\``,
    `- classification: \`${pyValue(record.classification)}\``,
    `- agent_id: \`${pyValue(record.agent_id ?? event.agent_id ?? "")}\``,
    `- source: \`${pyValue(event.source)}\``,
    `- effective_action: \`${pyValue(before.effective_action)}\``,
    `- monitor_target: \`${pyValue(target.target_id)}\``,
    `- settlement_todo_id: \`${pyValue(event.settlement_todo_id ?? "")}\``,
    `- todo_id: \`${pyValue(event.todo_id ?? "")}\``,
    `- target_key: \`${pyValue(event.target_key ?? "")}\``,
    `- material_change: \`${pyValue(event.material_change)}\``,
    `- should_run: \`${pyValue(before.should_run)}\``,
    `- self_repair_allowed: \`${pyValue(before.self_repair_allowed)}\``,
    `- state: \`${pyValue(before.state)}\``,
    `- health_check: ${pyValue(record.health_check)}`,
    `- reason: ${pyValue(event.reason_summary)}`,
  ];
  if (Object.keys(writeback).length) {
    lines.push(
      `- todo_writeback: dry_run=${pyValue(writeback.dry_run)} ` +
        `consecutive_no_change=${pyValue(writeback.consecutive_no_change)} ` +
        `last_checked_at=${pyValue(writeback.last_checked_at)} ` +
        `next_due_at=${pyValue(writeback.next_due_at)}`,
    );
  }
  return `${lines.join("\n")}\n`;
}

export async function projectAuxiliaryContinuation(
  request: MonitorRequest,
  payload: JsonObject,
): Promise<void> {
  const continuation = jsonObject(payload.turn_continuation);
  if (!request.execute || continuation?.settlement_binding_matches_observation !== false) return;
  const settlement = await readMonitorSettlement(request);
  if (jsonObject(settlement?.progress)?.state !== "settled") return;
  // This is current readback, not a change to the historical Monitor receipt
  // or permission for another advancement. Replays must not reopen a paid Turn.
  payload.turn_continuation = {
    ...continuation,
    current_turn_settled: true,
    next_turn_required: true,
    next_action: "rerun quota should-run with a fresh --turn-instance-id before independent work",
    reason: "the original advancement Turn is already settled; the auxiliary observation adds no spend or delivery identity",
  };
}

export async function payloadFor(
  request: MonitorRequest,
  record: JsonObject,
  jsonPath: string,
  markdownPath: string,
  indexPath: string,
  options: { appended: boolean; replayed: boolean; repaired: boolean },
): Promise<JsonObject> {
  const event = requiredObject(record.monitor_event, "record.monitor_event");
  const receipts = successorReceipts(request.provider_receipt);
  const payload: JsonObject = {
    ok: true,
    mode: "monitor-poll",
    dry_run: !request.execute,
    goal_id: request.goal_id,
    appended: options.appended,
    registry_mutated: false,
    source: event.source,
    classification: QUOTA_MONITOR_POLL_CLASSIFICATION,
    generated_at: record.generated_at,
    agent_id: record.agent_id ?? null,
    settlement_todo_id: event.settlement_todo_id ?? null,
    todo_id: event.todo_id ?? null,
    target_key: event.target_key ?? null,
    material_change: event.material_change === true,
    monitor_event: event,
    todo_writeback: request.provider_receipt,
    successor_todo_ids: receipts.map((receipt) => receipt.todo_id).filter(Boolean),
    successor_receipts: receipts,
    health_check: record.health_check,
    delivery_outcome: record.delivery_outcome,
    json_path: jsonPath,
    markdown_path: markdownPath,
    index_path: indexPath,
    transaction_repaired: options.repaired,
    reason: options.replayed
      ? "replayed existing monitor poll event for the same effect identity"
      : options.repaired
      ? "quota monitor-poll commit repaired its durable transaction artifacts"
      : `${request.execute ? "appended" : "dry-run preview"} monitor poll event: ${request.goal_id} effective_action=${request.decision.effective_action}`,
  };
  const goalRef = quotaGoalRef(request.owner);
  if (goalRef !== null) payload.goal_ref = goalRef;
  if (request.turn_instance_id) {
    payload.turn_instance_id = request.turn_instance_id;
    payload.replayed = options.replayed;
    if (request.execute) {
      const settlementTodoId = optionalTrimmed(event.settlement_todo_id, "monitor_event.settlement_todo_id");
      const observedTodoId = optionalTrimmed(event.todo_id, "monitor_event.todo_id");
      const exactSettlement = settlementTodoId !== null && observedTodoId === settlementTodoId;
      const auxiliaryObservation = settlementTodoId !== null && observedTodoId !== null && observedTodoId !== settlementTodoId;
      payload.turn_continuation = exactSettlement
        ? {
          schema_version: "quota_turn_continuation_v0",
          settlement_binding_matches_observation: true,
          current_turn_settled: true,
          same_turn_independent_settlement_allowed: false,
          next_turn_required: true,
          next_action: "rerun quota should-run with a fresh --turn-instance-id before independent work",
          reason: "the committed monitor-poll is this Turn's single settlement identity",
        }
        : {
          schema_version: "quota_turn_continuation_v0",
          settlement_binding_matches_observation: auxiliaryObservation ? false : null,
          current_turn_settled: false,
          same_turn_independent_settlement_allowed: false,
          next_turn_required: false,
          next_action: auxiliaryObservation
            ? "continue the original advancement settlement in this Turn"
            : "obtain a typed settlement binding before claiming this Turn settled",
          reason: auxiliaryObservation
            ? "the committed monitor-poll is an auxiliary observation and does not settle the advancement Turn"
            : "the committed monitor-poll has no exact Todo settlement binding",
        };
    }
  }
  if (request.status_reload_warning) payload.status_reload_warning = request.status_reload_warning;
  await projectAuxiliaryContinuation(request, payload);
  return payload;
}
