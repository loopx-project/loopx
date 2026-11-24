/** Read policy only: requirements are not enablement, credentials or write authority. */
import {readTodoPriority} from "../todos/priority.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireStringArray, requireInteger, requireNonEmptyString, requireBoolean, optionalNonEmptyString} from "../runtime_decode.ts";

const DEFAULT_AVAILABLE = ["shell", "filesystem_read", "filesystem_write"];
const OWNER_HELD = new Set(["credentials", "production_access"]);
export const OBSERVABLE_RUNTIME_CAPABILITIES: ReadonlySet<string> = new Set(["benchmark_runner", "network", "external_evidence_poll", "worker_bridge", "cli_bridge"]);
type CapabilityAction = "run" | "ask_owner" | "repair_bridge";
type ResolutionOwner = "user" | "agent";
interface Requirement {required: string[]; targets: string[]}
interface Candidate extends Requirement {payload: JsonObject; rank: number[]; ordinal: number}
interface Binding {
  owner: ResolutionOwner; action: "provide_or_authorize" | "repair_bridge"; capability: string;
  priority: string; primary_blocked_todo_id: string | null; blocked_todo_ids: string[];
}
const unique = (values: readonly string[]) => [...new Set(values)];
export const availableCapabilities = (values: readonly string[]): string[] => unique([...DEFAULT_AVAILABLE, ...values]);

/** A repair output is not its own prerequisite; */
export function missingRequiredCapabilities(required: readonly string[], targets: readonly string[], available: readonly string[]): string[] {
  const present = new Set([...availableCapabilities(available), ...targets]);
  return required.filter(capability => !present.has(capability));
}
const action = (missing: readonly string[]): CapabilityAction => !missing.length ? "run" :
  missing.some(capability => OWNER_HELD.has(capability)) ? "ask_owner" : "repair_bridge";
function requirement(value: unknown): Requirement {
  const row = requireJsonObject(value, "capability requirement");
  return {required: requireStringArray(row.required, "required"), targets: requireStringArray(row.targets, "targets")};
}
function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new TypeError("capability rows must be an array");
  return value;
}
const values = (rows: readonly JsonObject[], key: string) => unique(rows.flatMap(row => row[key] as string[] ?? []));
// A repair suggestion keeps its historical P1 fallback; declared P3/P4 remain intact.
const priority = (row: JsonObject): string => readTodoPriority(row) ?? "P1";

function bindings(blocked: readonly JsonObject[]): Binding[] {
  const result = new Map<string, Binding>();
  for (const row of blocked) for (const capability of row.missing_capabilities as string[]) {
    const owner: ResolutionOwner = OWNER_HELD.has(capability) ? "user" : "agent";
    const id = String(row.todo_id ?? "").trim() || null;
    const key = `${owner}:${capability}`;
    let binding = result.get(key);
    if (!binding) {
      binding = {owner, action: owner === "user" ? "provide_or_authorize" : "repair_bridge", capability,
        priority: priority(row), primary_blocked_todo_id: id, blocked_todo_ids: []};
      result.set(key, binding);
    } else if (priority(row) < binding.priority) {
      // Display/source order is not the urgency of a shared repair obligation.
      binding.priority = priority(row); binding.primary_blocked_todo_id = id;
    }
    if (id && !binding.blocked_todo_ids.includes(id)) binding.blocked_todo_ids.push(id);
  }
  return [...result.values()];
}

export function projectCapabilityGate(request: JsonObject): JsonObject | null {
  const available = availableCapabilities(requireStringArray(request.available, "available"));
  const source = requireNonEmptyString(request.source, "source");
  const candidates: Candidate[] = list(request.candidates).map((value, ordinal) => {
    const row = requireJsonObject(value, "capability candidate");
    const rank = list(row.rank).map(value => requireInteger(value, "rank"));
    if (rank.length !== 6) throw new TypeError("capability candidate rank must have six dimensions");
    return {...requirement(row), payload: requireJsonObject(row.payload, "payload"), rank, ordinal};
  });
  const blocked: JsonObject[] = [], runnable: {payload: JsonObject; rank: number[]; ordinal: number}[] = [];
  let sawRequirement = false;
  for (const candidate of candidates) {
    const {required, targets} = candidate;
    sawRequirement ||= !!(required.length || targets.length);
    const missing = missingRequiredCapabilities(required, targets, available);
    const missingTargets = missing.length ? [] : targets.filter(capability => !available.includes(capability));
    const repair = missingTargets.some(capability => OBSERVABLE_RUNTIME_CAPABILITIES.has(capability));
    const payload = {...candidate.payload, required_capabilities: required,
      ...(targets.length ? {target_capabilities: targets} : {}), missing_capabilities: missing,
      capability_action: repair ? "repair_bridge" : action(missing),
      ...(missingTargets.length ? {missing_target_capabilities: missingTargets} : {}),
      ...(repair ? {capability_repair_mode: true} : {})};
    if (missing.length) blocked.push(payload);
    else {
      const rank = [...candidate.rank]; rank[4] = repair ? 0 : 1;
      runnable.push({payload, rank, ordinal: candidate.ordinal});
    }
  }
  if (!sawRequirement && !blocked.length) return null;
  const policy = request.candidate_order_policy == null ? null : requireNonEmptyString(request.candidate_order_policy, "candidate_order_policy");
  if (policy) runnable.sort((a, b) => {
    for (let i = 0; i < a.rank.length; i++) if (a.rank[i] !== b.rank[i]) return a.rank[i]! - b.rank[i]!;
    return a.ordinal - b.ordinal;
  });
  const rows = runnable.map(row => row.payload), resolution = bindings(blocked);
  const forOwner = (owner: ResolutionOwner) => resolution.filter(binding => binding.owner === owner).map(binding => binding.capability);
  const ownerAction = resolution.filter(binding => binding.owner === "user").map(binding =>
    binding.primary_blocked_todo_id ? `${binding.capability} for ${binding.primary_blocked_todo_id}` : binding.capability);
  const common = {schema_version: "capability_gate_v0", source, available,
    runnable_count: rows.length, runnable_candidates: rows, blocked_candidates: blocked,
    owner_missing: forOwner("user"), unsupported_missing: [], resolution_bindings: resolution,
    owner_action: ownerAction.length ? `provide or authorize the missing owner-held capability: ${ownerAction.join(", ")}` : null};
  if (rows.length) return {...common, required: values(rows, "required_capabilities"), missing: [], action: "run",
    decision_owner: "agent", selection_policy: "agent_steering_audit_over_runnable_candidates",
    candidate_order_policy: policy ?? "projection_order", blocked_missing: values(blocked, "missing_capabilities"),
    repair_missing: unique([...values(rows.filter(row => row.capability_repair_mode === true), "missing_target_capabilities"), ...forOwner("agent")]),
    repair_candidate_count: rows.filter(row => row.capability_repair_mode === true).length,
    reason: "capability gate projected runnable candidate set; agent chooses the actual todo"};
  const missing = values(blocked, "missing_capabilities"), ownerMissing = forOwner("user"), repairMissing = forOwner("agent");
  return {...common, required: values(blocked, "required_capabilities"), missing, action: action(missing),
    decision_owner: ownerMissing.length ? "user" : "agent", repair_missing: repairMissing,
    resolution_steps: [
      ...(ownerMissing.length ? [{owner: "user", action: "provide_or_authorize", capabilities: ownerMissing}] : []),
      ...(repairMissing.length ? [{owner: "agent", action: "repair_bridge", capabilities: repairMissing}] : []),
    ], selection_policy: "no_runnable_candidate", blocks_delivery: true,
    reason: "all visible executable todo candidates require unavailable capabilities"};
}

/** Host-local read plan: never writes a grant or changes a committed Turn binding. */
export function projectRuntimeCapabilityReentry(request: JsonObject): JsonObject | null {
  const gate = requireJsonObject(request.gate, "gate");
  const observed = unique(requireStringArray(request.available, "available").filter(c => !OWNER_HELD.has(c)));
  const selectionRequired = requireBoolean(request.selection_required, "selection_required");
  const selectedId = optionalNonEmptyString(request.selected_todo_id, "selected_todo_id");
  const receiptId = optionalNonEmptyString(request.receipt_todo_id, "receipt_todo_id");
  // A receipt remains binding even if an inconsistent advisory flag accompanies it.
  const boundId = receiptId ?? (selectionRequired ? null : selectedId);
  const baseArgs = requireStringArray(request.command_prefix, "command_prefix");
  const schedulerArgs = requireStringArray(request.scheduler_args, "scheduler_args");
  const missing = requireStringArray(gate.repair_missing ?? [], "repair_missing")
    .filter(c => !OWNER_HELD.has(c) && !observed.includes(c));
  const resolutions = list(gate.resolution_bindings ?? []).map(value => {
    const row = requireJsonObject(value, "resolution binding");
    return {
      owner: requireNonEmptyString(row.owner, "owner"),
      capability: requireNonEmptyString(row.capability, "capability"),
      primary: optionalNonEmptyString(row.primary_blocked_todo_id, "primary_blocked_todo_id"),
      ids: requireStringArray(row.blocked_todo_ids ?? [], "blocked_todo_ids"),
    };
  });
  const blocked = list(gate.blocked_candidates ?? []).map(value => {
    const row = requireJsonObject(value, "blocked candidate");
    return {
      id: optionalNonEmptyString(row.todo_id, "todo_id"),
      instruction: optionalNonEmptyString(row.text, "text"),
      action: optionalNonEmptyString(row.action_kind, "action_kind") ?? "unspecified",
      target: optionalNonEmptyString(row.target_key, "target_key"),
      required: requireStringArray(row.required_capabilities ?? [], "required_capabilities"),
    };
  });
  if (!schedulerArgs.length) return null;
  const candidates: JsonObject[] = [];
  for (const capability of unique(missing)) {
    const bindings = resolutions.filter(row => row.owner === "agent" && row.capability === capability);
    const ids = new Set(bindings.flatMap(row => row.ids.length ? row.ids : row.primary ? [row.primary] : []));
    const eligible = blocked.filter(row => row.id && ids.has(row.id) && row.instruction &&
      row.required.includes(capability) && (!boundId || row.id === boundId));
    // Prefer the gate's highest-priority target, not incidental display order.
    const target = eligible.find(row => bindings.some(binding => binding.primary === row.id)) ?? eligible[0];
    if (!target) continue;
    candidates.push({
      capability, verification_required: "successful_real_callsite_observation",
      verification_target: {todo_id: target.id!, action_kind: target.action, instruction: target.instruction!,
        ...(target.target ? {target_ref: target.target} : {})},
      command_argv: [...baseArgs, ...observed.flatMap(c => ["--available-capability", c]),
        "--available-capability", capability, ...schedulerArgs],
    });
  }
  if (!candidates.length) return null;
  return {
    schema_version: "runtime_capability_reentry_v0", state: "verification_required",
    source: "quota_should_run.capability_gate.repair_missing", candidates,
    verification_contract: {scope: "real_task_facing_callsite_for_blocked_todo", ordinary_delivery_allowed: false,
      advancement_checkpoint: false, settles_turn: false,
      on_success: "rerun_quota_in_same_turn_then_continue_if_allowed",
      on_failure: "record_exact_blocker_without_capability_flag"},
    inheritance_contract: {source_invocation: "verified quota should-run reentry",
      propagates_to: ["interaction_contract.cli_channel.next_cli_actions", "quota spend-slot", "quota monitor-poll"],
      session_scoped: false, observation_scope: "host_registry_goal_agent",
      remembers: [...OBSERVABLE_RUNTIME_CAPABILITIES], durable_grant_written: false},
    failure_policy: "Do not add the capability flag when the real callsite check fails; continue the capability repair or record the concrete blocker.",
  };
}

/** Agent negatives override inherited and auto-probed declarations; a fresh explicit observation wins. */
export function projectCapabilityAvailability(request: JsonObject): JsonObject {
  const goal = unique(requireStringArray(request.goal, "goal"));
  const runtime = unique(requireStringArray(request.runtime, "runtime"));
  const probed = unique(requireStringArray(request.probed ?? [], "probed"))
    .filter(c => OBSERVABLE_RUNTIME_CAPABILITIES.has(c));
  const state = requireJsonObject(request.agent ?? {}, "agent");
  const remembered = requireStringArray(state.available ?? [], "agent.available")
    .filter(c => OBSERVABLE_RUNTIME_CAPABILITIES.has(c));
  const unavailable = requireStringArray(state.unavailable ?? [], "agent.unavailable")
    .filter(c => OBSERVABLE_RUNTIME_CAPABILITIES.has(c) && !runtime.includes(c));
  const hostUnavailable = requireStringArray(request.host_unavailable ?? [], "host_unavailable");
  const usable = (c: string) => !unavailable.includes(c) && !hostUnavailable.includes(c);
  const runtimeAvailable = unique([...probed, ...remembered, ...runtime]).filter(usable);
  return {goal, probed, agent: remembered, unavailable, invocation: runtime,
    runtime_available: runtimeAvailable,
    effective: unique([...goal, ...runtimeAvailable]).filter(usable)};
}

export function evaluateCapabilityGate(value: unknown): JsonObject {
  const request = requireJsonObject(value, "capability gate request");
  if (request.schema_version !== "capability_gate_request_v0") throw new TypeError("capability gate request schema mismatch");
  if (request.operation === "availability") return {schema_version: "capability_gate_result_v0", result: projectCapabilityAvailability(request)};
  if (request.operation === "project") return {schema_version: "capability_gate_result_v0", result: projectCapabilityGate(request)};
  if (request.operation === "reentry") return {schema_version: "capability_gate_result_v0", result: projectRuntimeCapabilityReentry(request)};
  if (request.operation === "missing") {
    const available = requireStringArray(request.available, "available");
    return {schema_version: "capability_gate_result_v0", result: list(request.items).map(item => {
      const {required, targets} = requirement(item);
      return missingRequiredCapabilities(required, targets, available);
    })};
  }
  throw new TypeError("unknown capability gate operation");
}
