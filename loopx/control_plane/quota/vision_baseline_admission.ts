import type {JsonObject} from "../effect_program.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {AgentScopeFrontierAction} from "../agents/agent_scope_frontier.generated.ts";
import {parseWireExactGoalRef} from "../goals/acceptance_lifecycle.ts";
import {
  committedGoalHeartbeatReceiptsFromSnapshot,
  readGoalRolloutEventSnapshot,
  receiptMatchesGoalRef,
} from "../rollout_receipt_log.ts";
import {readAgent, readExcludedAgents} from "../todos/read_codec.ts";
import {EffectiveAction} from "./effective_action.generated.ts";
import {admitsAutonomousReplan, heartbeatReceiptFactFromEvent} from "./heartbeat_receipt_identity.ts";

/** One initialization Turn per runtime/Goal/Agent; missing checkpoints cannot rearm it. */
export async function boundVisionBaselineAdmission(params: JsonObject, decision: JsonObject): Promise<JsonObject> {
  const context = params.vision_initialization_context == null ? null
    : requireJsonObject(params.vision_initialization_context, "vision_initialization_context");
  if (!context || decision.replan_decision_allowed !== true || decision.should_run !== true ||
      decision.effective_action !== EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED || context.receipt_bound === true ||
      decision.self_repair_allowed === true || decision.capability_repair_allowed === true ||
      decision.workspace_repair_allowed === true) return decision;
  const agent = requireNonEmptyString(context.agent_id, "agent_id");
  const summary = requireJsonObject(context.todo_summary, "todo_summary");
  const counts = jsonObject(summary.work_counts);
  // Unknown or incomplete projections cannot prove that all real work is blocked.
  if (counts?.complete !== true || counts.agent_id !== agent || counts.advancement !== 0 ||
      summary.monitor_due_count !== 0 || Number(summary.monitor_schedule_gap_count ?? 0) > 0) return decision;
  const rows = context.todo_items;
  if (!Array.isArray(rows)) throw new TypeError("todo_items must be an array");
  const blocked = rows.map(row => requireJsonObject(row, "Todo")).filter(row =>
    row.status === "blocked" && row.done !== true && row.resume_ready !== true &&
    (row.task_class === "advancement_task" || row.task_class === "blocker") &&
    (!readAgent(row.claimed_by) || readAgent(row.claimed_by) === agent) &&
    !readExcludedAgents(row.excluded_agents).includes(agent));
  if (!blocked.length) return decision;
  const obligation = requireJsonObject(context.replan_obligation, "replan_obligation");
  const triggers = obligation.triggers;
  const gaps = context.acceptance_gaps;
  if (!Array.isArray(triggers) || !triggers.length || !Array.isArray(gaps) || !gaps.length ||
      !triggers.every(raw => jsonObject(raw)?.kind === "vision_checkpoint_missing") ||
      !gaps.every(raw => {
        const gap = jsonObject(raw);
        return gap?.kind === "vision_checkpoint_missing" && gap.missing_baseline === true && gap.agent_id === agent;
      })) return decision;
  const root = requireNonEmptyString(context.runtime_root, "runtime_root");
  const goal = requireNonEmptyString(context.goal_id, "goal_id");
  const turn = requireNonEmptyString(context.turn_instance_id, "turn_instance_id");
  const goalRef = context.goal_ref == null ? null : parseWireExactGoalRef(context.goal_ref, "Goal reference");
  const snapshot = await readGoalRolloutEventSnapshot(root, goal);
  const receipts = committedGoalHeartbeatReceiptsFromSnapshot(snapshot, goal, agent) ?? [];
  const previous = receipts.find(receipt =>
    receiptMatchesGoalRef(receipt, goalRef) &&
    receipt.status === EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED &&
    jsonObject(receipt.details)?.vision_baseline_initialization === true &&
    admitsAutonomousReplan(receipt, heartbeatReceiptFactFromEvent(receipt)));
  if (!previous) return {...decision, vision_baseline_initialization: true};
  const admittedTurn = requireNonEmptyString(previous.run_id, "admitted Turn");
  if (admittedTurn === turn) return decision;

  const reason = "Vision baseline initialization was already admitted; the scoped frontier remains blocked";
  const frontier = {schema_version: "agent_scope_frontier_v1", agent_id: agent,
    action: AgentScopeFrontierAction.AGENT_SCOPE_WAIT, blocks_delivery: true,
    quiet_noop_allowed: true, spend_policy: "no quota spend while waiting for runnable work or an owner change",
    reason, recommended_action: "Wait for executable work or an explicit owner/replan change; retain the pending Vision baseline duty.",
    candidate_counts: {current_agent_claimed_advancement_count: 0, unclaimed_advancement_count: 0,
      blocked_wait_count: blocked.length},
    vision_baseline_admission: {
      schema_version: "vision_baseline_admission_v0", goal_id: goal, agent_id: agent,
      turn_instance_id: admittedTurn,
      replan_obligation_id: requireNonEmptyString(jsonObject(previous.details)?.replan_obligation_id, "admitted replan obligation"),
    }};
  return {...decision, normal_delivery_allowed: false, recovery_delivery_allowed: false,
    should_run: false, replan_decision_allowed: false,
    effective_action: AgentScopeFrontierAction.AGENT_SCOPE_WAIT, reason, agent_scope_frontier: frontier};
}
