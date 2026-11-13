import type { JsonObject } from "../effect_program.ts";
import { visionAuthoringContract } from "../goals/vision_checkpoint.ts";
import { EffectiveAction } from "../quota/effective_action.generated.ts";
import { requireCompleteTurnDecision } from "../quota/turn_envelope.ts";
import { jsonObject } from "../runtime_decode.ts";

/** A transport lens, never a replacement for quota admission or vision rules. */
export function projectMcpInteraction(
  guard: JsonObject, numberTokens?: unknown, controllerOwnsSettlement = false,
): JsonObject {
  if (guard.ok === false && !Object.hasOwn(guard, "schema_version") &&
      !Object.hasOwn(guard, "contract_binding") && !Object.hasOwn(guard, "interaction_contract")) return guard;
  requireCompleteTurnDecision(guard, numberTokens);
  const selected = jsonObject(guard.selected_todo);
  const interaction = jsonObject(guard.interaction_contract);
  const agent = jsonObject(interaction?.agent_channel);
  const cli = jsonObject(interaction?.cli_channel);
  const todoId = selected?.todo_id;
  const ownsDelivery = !controllerOwnsSettlement && guard.ok === true &&
    guard.normal_delivery_allowed === true && agent?.delivery_allowed === true &&
    typeof todoId === "string";
  const bindTodo = guard.ok === true && guard.should_run === true &&
    guard.effective_action === EffectiveAction.NORMAL_RUN &&
    selected?.task_class === "advancement_task" &&
    (cli?.selection_required === true || cli?.turn_binding_required === true);
  let rule = "Use complete_task for verified Todo lifecycle, writeback and accounting. For independent replan, follow the live CLI binding and actions, including any required Todo selection.";
  if (controllerOwnsSettlement) {
    rule = "The outer controller owns Todo admission and settlement.";
  } else if (bindTodo) {
    rule = "Choose an eligible Todo and pass its todo_id to should_run. Read the returned admission before work and reuse that todo_id on retry.";
  }
  // The native MCP channel is separate from the immutable decision.
  return {
    schema_version: "host_mcp_interaction_v0",
    delivery_executor: controllerOwnsSettlement ? null : "complete_task",
    delivery_todo_id: ownsDelivery ? todoId : null,
    rule,
    vision_authoring: visionAuthoringContract(),
    checkpoint_recovery_tool: "review_task_vision",
  };
}
