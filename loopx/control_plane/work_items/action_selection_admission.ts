/** Final selected-work admission, shared by CLI recovery and packet readers.
 * Receipt labels are reconciliation facts, never proof of current lease authority. */
import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {jsonObject, requireBoolean, requireJsonObject} from "../runtime_decode.ts";
import {EffectiveAction} from "../quota/effective_action.generated.ts";
import {isCanonicalTodoId} from "../todos/identity.ts";

interface AgentAdmissionFacts {must_attempt: boolean; delivery_allowed: boolean | null}
interface SelectionAdmission {selected_todo_id: string | null; admitted: boolean}

function selectionTodoId(value: unknown): string | null {
  if (typeof value !== "string") return null;
  // Preserve the Python selection reader's strip, not JS trim's BOM policy.
  const whitespace = /[\p{White_Space}\u001c-\u001f]/u;
  let start = 0, end = value.length;
  while (start < end && whitespace.test(value[start]!)) start++;
  while (end > start && whitespace.test(value[end - 1]!)) end--;
  const candidate = value.slice(start, end).toLowerCase();
  return isCanonicalTodoId(candidate) ? candidate : null;
}

export function currentActionSelectionAdmission(
  packet: JsonObject, requestedTodoId: string, agent: AgentAdmissionFacts,
): SelectionAdmission {
  const selected = jsonObject(packet.selected_todo) ?? {};
  const qualification = jsonObject(packet.action_selection_qualification) ?? {};
  let selectedId = selectionTodoId(selected.todo_id);
  if (selectedId === null && qualification.state === "qualified") {
    selectedId = selectionTodoId(jsonObject(qualification.selected_todo)?.todo_id);
  }
  const pending = selected.selection_binding === "pending_action_selection";
  const delivery = pending && packet.normal_delivery_allowed === true
    && agent.must_attempt && agent.delivery_allowed === true;
  const workspaceRepair = pending && packet.workspace_repair_allowed === true
    && packet.effective_action === EffectiveAction.AGENT_WORKSPACE_REPAIR
    && agent.must_attempt && agent.delivery_allowed === false;
  const currentWork = !pending && agent.must_attempt;
  return {selected_todo_id: selectedId,
    admitted: selectedId !== null && selectedId === requestedTodoId
      && packet.ok === true && packet.should_run === true && (delivery || workspaceRepair || currentWork)};
}

export function actionSelectionNeedsRecovery(packet: JsonObject, agent: AgentAdmissionFacts): boolean {
  const qualification = jsonObject(packet.action_selection_qualification);
  if (qualification === null) return false;
  if (qualification.state !== null && typeof qualification.state === "object") {
    throw new EffectRuntimeRequestError("action selection qualification state must be scalar");
  }
  if (qualification.state !== "deferred" && qualification.state !== "rejected") return false;
  const selected = jsonObject(packet.selected_todo), replan = jsonObject(packet.autonomous_replan_obligation);
  if (selected?.selection_binding === "heartbeat_receipt" || replan?.selection_binding === "heartbeat_receipt") return false;
  const requested = typeof qualification.requested_todo_id === "string" ? qualification.requested_todo_id : "";
  if (currentActionSelectionAdmission(packet, requested, agent).admitted) return false;
  if (qualification.recovery_action !== "reenter_guard_without_selection") {
    throw new EffectRuntimeRequestError("rejected action selection omitted its typed recovery action");
  }
  return true;
}

function admissionRequest(request: JsonObject) {
  return {packet: requireJsonObject(request.packet, "selection admission packet"), agent: {
    must_attempt: requireBoolean(request.agent_must_attempt, "agent_must_attempt"),
    delivery_allowed: request.agent_delivery_allowed === null ? null
      : requireBoolean(request.agent_delivery_allowed, "agent_delivery_allowed"),
  }};
}

export function projectCurrentActionSelectionAdmission(request: JsonObject): JsonObject {
  const {packet, agent} = admissionRequest(request);
  if (typeof request.requested_todo_id !== "string") throw new EffectRuntimeRequestError("requested_todo_id must be a string");
  return {...currentActionSelectionAdmission(packet, request.requested_todo_id, agent)};
}

export function projectActionSelectionRecovery(request: JsonObject): JsonObject {
  const {packet, agent} = admissionRequest(request);
  return {required: actionSelectionNeedsRecovery(packet, agent)};
}

const ROOT_EXECUTION_FLAGS = ["should_run", "normal_delivery_allowed", "recovery_delivery_allowed",
  "self_repair_allowed", "capability_repair_allowed", "workspace_repair_allowed", "safe_bypass_allowed",
  "actionable_by_codex", "spend_allowed_now", "spend_after_validation"] as const;

/** Reject stale grants without inspecting command prose or consuming receipts. */
export function assertActionSelectionConsistency(packet: JsonObject, interaction: JsonObject): void {
  const agent = jsonObject(interaction.agent_channel) ?? {}, cli = jsonObject(interaction.cli_channel) ?? {};
  if (!actionSelectionNeedsRecovery(packet, {
    must_attempt: agent.must_attempt === true,
    delivery_allowed: typeof agent.delivery_allowed === "boolean" ? agent.delivery_allowed : null,
  })) return;
  const granted = (value: unknown) => value !== undefined && value !== null && value !== false;
  if (ROOT_EXECUTION_FLAGS.some(field => granted(packet[field]))
      || granted(agent.must_attempt) || granted(agent.delivery_allowed)
      || granted(cli.spend_allowed_now) || granted(cli.spend_after_validation) || granted(cli.selection_required)
      || ["settlement_plan", "replan_settlement_contract", "selection_command", "selection_policy_ref"]
        .some(field => cli[field] !== undefined && cli[field] !== null)) {
    throw new EffectRuntimeRequestError("unadmitted action selection cannot carry execution or settlement authority");
  }
}
