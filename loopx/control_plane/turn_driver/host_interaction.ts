import type { JsonObject } from "../effect_program.ts";
import { visionAuthoringContract } from "../goals/vision_checkpoint.ts";
import {requireCompleteTurnDecision} from "../quota/turn_envelope.ts";

function object(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as JsonObject : null;
}

/** A transport lens, never a replacement for quota admission or vision rules. */
export function projectMcpInteraction(guard: JsonObject, numberTokens?: unknown): JsonObject {
  if (guard.ok === false && !Object.hasOwn(guard, "schema_version") &&
      !Object.hasOwn(guard, "contract_binding") && !Object.hasOwn(guard, "interaction_contract")) return guard;
  requireCompleteTurnDecision(guard, numberTokens);
  const selected = object(guard.selected_todo);
  const ownsDelivery = guard.ok === true && guard.normal_delivery_allowed === true &&
    typeof selected?.todo_id === "string";
  // The native MCP channel is separate from the immutable decision.
  return {
    schema_version: "host_mcp_interaction_v0",
    delivery_executor: "complete_task",
    delivery_todo_id: ownsDelivery ? selected!.todo_id : null,
    rule: "For verified Todo delivery, call complete_task INSTEAD OF manual refresh/spend. It owns lifecycle, writeback and accounting. For independent replan, follow the live CLI binding/actions; do not replay an old completion.",
    vision_authoring: visionAuthoringContract(),
    checkpoint_recovery_tool: "review_task_vision",
  };
}
