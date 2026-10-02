import type { JsonObject } from "../effect_program.ts";
import { requireJsonObject, requireNonEmptyString } from "../runtime_decode.ts";
import { decodeInteractionContract } from "../work_items/interaction_contract.ts";

/** Project diagnostics after selection, guards, settlement and notification overlays.
 * Gate admission metadata is historical context; it cannot select another Todo.
 */
export function projectScopedOverride(params: JsonObject): JsonObject {
  const override = { ...requireJsonObject(params.override, "override") };
  const interaction = decodeInteractionContract(params.interaction_contract);
  delete override.selected_action;
  if (interaction.agent_channel.delivery_allowed && params.selected_todo != null) {
    const selected = requireJsonObject(params.selected_todo, "selected_todo");
    requireNonEmptyString(selected.todo_id, "selected_todo.todo_id");
    override.selected_action = requireNonEmptyString(selected.text, "selected_todo.text");
  }
  return override;
}
