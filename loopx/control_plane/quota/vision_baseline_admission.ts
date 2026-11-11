import {createHash} from "node:crypto";
import {readFile} from "node:fs/promises";
import {join} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {durableWriteJson, withFileMutationLock} from "../effect_runtime_io.ts";
import {jsonObject, requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {AgentScopeFrontierAction} from "../agents/agent_scope_frontier.generated.ts";
import {readAgent, readExcludedAgents} from "../todos/read_codec.ts";

const BASELINE_ADMISSION_SCHEMA = "vision_baseline_admission_v0";

interface VisionBaselineAdmission extends JsonObject {
  schema_version: typeof BASELINE_ADMISSION_SCHEMA;
  goal_id: string;
  agent_id: string;
  turn_instance_id: string;
  replan_obligation_id: string | null;
}

/** One initialization Turn per runtime/Goal/Agent; missing checkpoints cannot rearm it. */
export async function boundVisionBaselineAdmission(params: JsonObject, decision: JsonObject): Promise<JsonObject> {
  const context = params.vision_initialization_context == null ? null
    : requireJsonObject(params.vision_initialization_context, "vision_initialization_context");
  if (!context || decision.replan_decision_allowed !== true || context.receipt_bound === true ||
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
  const scope = {goal_id: requireNonEmptyString(context.goal_id, "goal_id"), agent_id: agent};
  const turn = requireNonEmptyString(context.turn_instance_id, "turn_instance_id");
  const path = join(root, "vision-baseline-admissions",
    `${createHash("sha256").update(JSON.stringify(scope)).digest("hex")}.json`);
  return withFileMutationLock(path, async () => {
    let previous: JsonObject | null = null;
    try { previous = requireJsonObject(JSON.parse(await readFile(path, "utf8")), "baseline admission"); }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
    if (previous && (previous.schema_version !== BASELINE_ADMISSION_SCHEMA ||
        previous.goal_id !== scope.goal_id || previous.agent_id !== agent)) {
      throw new TypeError("Vision baseline admission scope/schema mismatch");
    }
    if (!previous) {
      const admission: VisionBaselineAdmission = {schema_version: BASELINE_ADMISSION_SCHEMA, ...scope,
        turn_instance_id: turn, replan_obligation_id: requireNonEmptyString(obligation.obligation_id, "obligation_id")};
      await durableWriteJson(path, admission);
      return decision;
    }
    // Reentry of the admitted Turn must retain its original work and settlement identity.
    if (requireNonEmptyString(previous.turn_instance_id, "admitted Turn") === turn) return decision;
    const reason = "Vision baseline initialization was already admitted; the scoped frontier remains blocked";
    const frontier = {schema_version: "agent_scope_frontier_v1", agent_id: agent,
      action: AgentScopeFrontierAction.AGENT_SCOPE_WAIT, blocks_delivery: true,
      quiet_noop_allowed: true, spend_policy: "no quota spend while waiting for runnable work or an owner change",
      reason, recommended_action: "Wait for executable work or an explicit owner/replan change; retain the pending Vision baseline duty.",
      candidate_counts: {current_agent_claimed_advancement_count: 0, unclaimed_advancement_count: 0,
        blocked_wait_count: blocked.length},
      vision_baseline_admission: previous};
    return {...decision, normal_delivery_allowed: false, recovery_delivery_allowed: false,
      should_run: false, replan_decision_allowed: false,
      effective_action: AgentScopeFrontierAction.AGENT_SCOPE_WAIT, reason, agent_scope_frontier: frontier};
  });
}
