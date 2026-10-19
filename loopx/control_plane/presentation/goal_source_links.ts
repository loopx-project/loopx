/** Local organization of existing source Goals, never execution registration. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";

export function projectGoalSourceLinks(input: JsonObject): JsonObject {
  if (!Array.isArray(input.local_goal_ids) || !Array.isArray(input.links) || input.links.length > 32) {
    throw new Error("Use a bounded Goal source catalog");
  }
  const localIds = new Set(input.local_goal_ids.map(id => requireNonEmptyString(id, "local Goal")));
  const seen = new Set<string>();
  const goals = input.links.map(value => {
    const link = requireJsonObject(value, "source link");
    const goalId = requireNonEmptyString(link.goal_id, "source Goal");
    const parent = requireNonEmptyString(link.parent_goal_id, "parent Goal");
    const namespace = requireNonEmptyString(link.namespace, "source namespace");
    const label = requireNonEmptyString(link.label, "source label");
    if (!localIds.has(parent) || localIds.has(goalId) || seen.has(goalId)
      || !/^[a-f0-9]{64}$/.test(namespace)) {
      throw new Error("Source Goals need an existing local parent and an unambiguous original identity");
    }
    seen.add(goalId);
    return {id: goalId, parent_goal_id: parent, display_name: label,
      registry_member: true, activation_state: "active",
      source_access: {namespace, label, read_only: true}};
  });
  return {goals};
}
