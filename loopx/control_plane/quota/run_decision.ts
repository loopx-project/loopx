import type {JsonObject} from "../effect_program.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import {requireBoolean, requireJsonObject} from "../runtime_decode.ts";
import {EffectiveAction} from "./effective_action.generated.ts";

interface RunFacts {
  normal_delivery_allowed: boolean;
  recovery_delivery_allowed: boolean;
  self_repair_allowed: boolean;
  repair_effective_action: string;
  state: string;
  reason: string;
  quota_focus_wait: boolean;
  capability_gate: JsonObject | null;
  capability_monitor_fallback: boolean;
  workspace_guard: JsonObject | null;
  automation_prompt_upgrade: JsonObject | null;
  automation_prompt_upgrade_required: boolean;
  replan_required: boolean;
  replan_applies: boolean;
  goal_health_ok: boolean;
  inbox_reply_due: boolean;
  inbox_material_review_due: boolean;
  terminal_no_followup: boolean;
  task_orchestration_contract: JsonObject | null;
}

interface RunDecision extends JsonObject {
  normal_delivery_allowed: boolean;
  recovery_delivery_allowed: boolean;
  self_repair_allowed: boolean;
  capability_repair_allowed: boolean;
  workspace_repair_allowed: boolean;
  should_run: boolean;
  effective_action: string;
  reason: string;
  state: string;
  quota_patch: JsonObject;
  replan_decision_allowed: boolean;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string") throw new EffectRuntimeRequestError(`${label} must be a string`);
  return value;
}

function optionalRecord(value: unknown, label: string): JsonObject | null {
  if (value === null || value === undefined) return null;
  const record = requireJsonObject(value, label);
  return Object.keys(record).length ? record : null;
}

function decodeRunFacts(params: JsonObject): RunFacts {
  return {
    normal_delivery_allowed: requireBoolean(params.normal_delivery_allowed, "normal_delivery_allowed"),
    recovery_delivery_allowed: requireBoolean(params.recovery_delivery_allowed, "recovery_delivery_allowed"),
    self_repair_allowed: requireBoolean(params.self_repair_allowed, "self_repair_allowed"),
    repair_effective_action: text(params.repair_effective_action, "repair_effective_action"),
    state: text(params.state, "state"), reason: text(params.reason, "reason"),
    quota_focus_wait: requireBoolean(params.quota_focus_wait, "quota_focus_wait"),
    capability_gate: optionalRecord(params.capability_gate, "capability_gate"),
    capability_monitor_fallback: requireBoolean(params.capability_monitor_fallback, "capability_monitor_fallback"),
    workspace_guard: optionalRecord(params.workspace_guard, "workspace_guard"),
    automation_prompt_upgrade: optionalRecord(params.automation_prompt_upgrade, "automation_prompt_upgrade"),
    automation_prompt_upgrade_required: requireBoolean(params.automation_prompt_upgrade_required, "automation_prompt_upgrade_required"),
    replan_required: requireBoolean(params.replan_required, "replan_required"),
    replan_applies: requireBoolean(params.replan_applies, "replan_applies"),
    goal_health_ok: requireBoolean(params.goal_health_ok, "goal_health_ok"),
    inbox_reply_due: requireBoolean(params.inbox_reply_due, "inbox_reply_due"),
    inbox_material_review_due: requireBoolean(params.inbox_material_review_due, "inbox_material_review_due"),
    terminal_no_followup: requireBoolean(params.terminal_no_followup, "terminal_no_followup"),
    task_orchestration_contract: optionalRecord(params.task_orchestration_contract, "task_orchestration_contract"),
  };
}

function guardReason(guard: JsonObject | null, fallback: string): string {
  return (guard ? text(guard.reason ?? "", "guard.reason") : "") || fallback;
}

function clearExecutionLanes(decision: RunDecision): void {
  decision.normal_delivery_allowed = false;
  decision.recovery_delivery_allowed = false;
  decision.self_repair_allowed = false;
  decision.capability_repair_allowed = false;
  decision.workspace_repair_allowed = false;
}

function applyDeliveryGuards(facts: RunFacts, decision: RunDecision): void {
  const gate = facts.capability_gate;
  if (gate && gate.action !== "run" && !facts.capability_monitor_fallback) {
    decision.normal_delivery_allowed = false;
    decision.recovery_delivery_allowed = false;
    decision.capability_repair_allowed = gate.action === "repair_bridge";
    decision.reason = guardReason(gate, decision.capability_repair_allowed
      ? "capability bridge repair required" : "selected todo capability is unavailable");
  }
  if (facts.workspace_guard) {
    clearExecutionLanes(decision);
    decision.workspace_repair_allowed = true;
    decision.reason = guardReason(facts.workspace_guard, "agent workspace guard blocks delivery");
  }
  if (facts.automation_prompt_upgrade_required) {
    clearExecutionLanes(decision);
    decision.reason = guardReason(facts.automation_prompt_upgrade,
      "identity-aware automation prompt upgrade is required");
  }
  decision.should_run = decision.normal_delivery_allowed || decision.recovery_delivery_allowed
    || decision.self_repair_allowed || decision.capability_repair_allowed || decision.workspace_repair_allowed;
}

function quotaEffectiveAction(facts: RunFacts, decision: RunDecision): string {
  if (decision.normal_delivery_allowed) return EffectiveAction.NORMAL_RUN;
  if (decision.recovery_delivery_allowed) return EffectiveAction.OUTCOME_FLOOR_RECOVERY;
  if (decision.workspace_repair_allowed) return EffectiveAction.AGENT_WORKSPACE_REPAIR;
  if (decision.self_repair_allowed) return facts.repair_effective_action || EffectiveAction.CONTROL_PLANE_REPAIR;
  if (decision.capability_repair_allowed) return EffectiveAction.CAPABILITY_BRIDGE_REPAIR;
  if (facts.state === "operator_gate") return EffectiveAction.OPERATOR_GATE_NOTIFY;
  if (facts.state === "blocked_health") return EffectiveAction.BLOCKED_HEALTH;
  if (facts.state === "throttled") return EffectiveAction.THROTTLED_SKIP;
  if (facts.state === "focus_wait" || facts.state === "waiting" || facts.quota_focus_wait)
    return EffectiveAction.BLOCKED_WAIT;
  return EffectiveAction.QUOTA_SKIP;
}

function applyGoalOverrides(facts: RunFacts, decision: RunDecision): void {
  const inboxPriority = facts.inbox_reply_due || facts.inbox_material_review_due;
  decision.replan_decision_allowed = !inboxPriority && facts.replan_required && facts.replan_applies
    && facts.goal_health_ok && !facts.workspace_guard && !facts.automation_prompt_upgrade_required;
  if (decision.replan_decision_allowed) {
    decision.normal_delivery_allowed = false;
    decision.recovery_delivery_allowed = false;
    decision.should_run = true;
    decision.effective_action = EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED;
    decision.reason = "autonomous replan obligation is selected before monitor quiet "
      + "or agent-scope wait classification";
  }
  if (facts.terminal_no_followup && !inboxPriority) {
    clearExecutionLanes(decision);
    decision.should_run = false;
    decision.state = "terminal_no_followup";
    decision.quota_patch = {state: "terminal_no_followup",
      reason: "derived terminal no-follow-up is confirmed by complete todo sources and an empty frontier"};
    decision.effective_action = EffectiveAction.TERMINAL_NO_FOLLOWUP;
    decision.reason = "validated closure evidence derives terminal no-follow-up from "
      + "complete todo sources and an empty frontier; stop recurring "
      + "automation until an explicit resume";
  }
}

function applyInboxOverrides(facts: RunFacts, decision: RunDecision): void {
  // Preserve the terminal-source exception even when inbox work keeps the lane open.
  if (facts.automation_prompt_upgrade_required && !facts.terminal_no_followup) {
    decision.should_run = false;
    decision.effective_action = EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED;
  } else if (facts.inbox_reply_due || facts.inbox_material_review_due) {
    clearExecutionLanes(decision);
    decision.should_run = true;
    decision.normal_delivery_allowed = true;
    if (facts.inbox_reply_due) {
      decision.effective_action = EffectiveAction.LARK_INBOX_REPLY_DUE;
      decision.reason = "a direct Lark question, bot mention, or verified reply to the bot is pending reply";
    } else {
      decision.effective_action = EffectiveAction.OPERATOR_INBOX_MATERIAL_REVIEW_DUE;
      decision.reason = "captured unaddressed operator-inbox material is pending bounded review";
    }
  }
}

function taskOrchestrationEffectiveAction(facts: RunFacts, decision: RunDecision): [string, string] {
  const contract = facts.task_orchestration_contract;
  if (contract && (text(contract.execution_state ?? "", "execution_state") || "ready") === "ready"
      && decision.should_run && decision.normal_delivery_allowed
      && decision.effective_action === EffectiveAction.NORMAL_RUN) {
    return [EffectiveAction.COORDINATE_TASK_BUNDLE, contract.mode === "adaptive"
      ? "the task coordinator may use admitted child lanes before its own worker-lane delivery"
      : "the explicitly selected task coordinator must activate or resume eligible peer lanes before doing its own worker-lane delivery"];
  }
  return [decision.effective_action, decision.reason];
}

/** Compose base quota admission after source reads; receipt-bound overlays keep their owners. */
export function resolveQuotaRunDecision(params: JsonObject): JsonObject {
  const facts = decodeRunFacts(params);
  const decision: RunDecision = {
    normal_delivery_allowed: facts.normal_delivery_allowed,
    recovery_delivery_allowed: facts.recovery_delivery_allowed,
    self_repair_allowed: facts.self_repair_allowed,
    capability_repair_allowed: false, workspace_repair_allowed: false,
    should_run: false, effective_action: EffectiveAction.QUOTA_SKIP,
    reason: facts.reason, state: facts.state, quota_patch: {}, replan_decision_allowed: false,
  };
  applyDeliveryGuards(facts, decision);
  decision.effective_action = quotaEffectiveAction(facts, decision);
  applyGoalOverrides(facts, decision);
  applyInboxOverrides(facts, decision);
  [decision.effective_action, decision.reason] = taskOrchestrationEffectiveAction(facts, decision);
  return decision;
}
