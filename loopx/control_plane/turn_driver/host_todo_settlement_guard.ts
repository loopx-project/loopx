import {
  SETTLEMENT_IDENTITY_SCHEMA_VERSION,
  settlementIdentityFromPlan,
  settlementIdentityPayload,
  type JsonObject,
} from "../effect_program.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { jsonObject } from "../runtime_decode.ts";

// Private CLI-to-MCP projection of one complete, already decided Turn.
export const HOST_TODO_SETTLEMENT_GUARD_SCHEMA_VERSION = "loopx_host_todo_settlement_guard_v0";

export function projectHostTodoSettlementGuard(decision: JsonObject): JsonObject {
  const schema_version = HOST_TODO_SETTLEMENT_GUARD_SCHEMA_VERSION;
  const selected = jsonObject(decision.selected_todo);
  const reason = typeof decision.reason === "string" && decision.reason ? decision.reason : null;
  const reject = (message: string): JsonObject => ({schema_version, ok: false, reason: message});
  if (decision.ok !== true) return reject(reason ?? "quota guard did not admit Todo settlement");
  if (!selected && decision.effective_action === EffectiveAction.TERMINAL_NO_FOLLOWUP) {
    return {...reject("quota guard has no selected Todo"), guard_state: "terminal_no_selection"};
  }

  const receipt = jsonObject(decision.heartbeat_receipt);
  const observed = jsonObject(receipt?.settlement_identity);
  const parsed = settlementIdentityFromPlan({settlement_plan: {identity: observed}});
  if (!receipt || !observed || observed.schema_version !== SETTLEMENT_IDENTITY_SCHEMA_VERSION ||
      parsed.failure !== null || parsed.value.binding_kind !== "todo") {
    return reject(reason ?? "quota guard has no authoritative Todo settlement identity");
  }
  const identity = settlementIdentityPayload(parsed.value);
  if (Object.entries(identity).some(([key, value]) => observed[key] !== value) ||
      receipt.turn_instance_id !== identity.turn_instance_id ||
      typeof receipt.event_id !== "string" || !receipt.event_id ||
      !["committed", "upgraded", "replayed"].includes(String(receipt.status)) ||
      decision.goal_id !== identity.goal_id ||
      jsonObject(decision.agent_identity)?.agent_id !== identity.agent_id ||
      (selected && selected.todo_id !== identity.todo_id)) {
    return reject("quota guard settlement identity does not match its decision");
  }

  const fresh = decision.should_run === true && decision.normal_delivery_allowed === true &&
    selected?.todo_id === identity.todo_id;
  // A completion retry can have no selected Todo. The original receipt and
  // the quota owner's current-Turn settlement route still bind the same work.
  const recovery = jsonObject(decision.unsettled_host_turn_recovery);
  const retry = receipt.status === "replayed" && (
    decision.effective_action === EffectiveAction.HEARTBEAT_SETTLED_SKIP ||
    (decision.effective_action === EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY &&
      recovery?.scope === "current_turn" && recovery.turn_instance_id === identity.turn_instance_id &&
      recovery.binding_kind === "todo" && recovery.binding_id === identity.todo_id &&
      (recovery.repair === "durable_writeback" || recovery.repair === "quota_spend"))
  );
  return fresh || retry
    ? {schema_version, ok: true, settlement_identity: identity}
    : reject(reason ?? "quota guard did not admit Todo settlement");
}
