/** Upgrade-only decoder for retired registry pause values and stored readback aliases.
 * Normal readers and writers require current metadata; this never grants a Resume. */
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject, requireNonEmptyString} from "../runtime_decode.ts";
import {SOURCE_SESSION_PROFILE_ID} from "./source_session_lifetime.ts";

const SCHEMA = "loopx_goal_activation_v1";

function state(value: unknown): "active" | "stopped" {
  if (value === "active" || value === "stopped") return value;
  throw new EffectRuntimeRequestError("Goal activation state must be active or stopped");
}

export function migrateGoalActivationMetadata(params: JsonObject): JsonObject {
  if (!Array.isArray(params.registries)) throw new EffectRuntimeRequestError("registries must be an array");
  const registries = params.registries.map(raw => {
    const row = requireJsonObject(raw, "registry snapshot");
    const path = requireNonEmptyString(row.path, "registry path");
    const payload = requireJsonObject(row.payload, "registry payload");
    if (payload.goals !== undefined && !Array.isArray(payload.goals)) throw new EffectRuntimeRequestError("registry goals must be an array");
    return {path, payload, goals: (payload.goals ?? []) as unknown[]};
  });
  if (new Set(registries.map(row => row.path)).size !== registries.length) throw new EffectRuntimeRequestError("duplicate registry path");
  const nodes = registries.flatMap(registry => registry.goals.map((raw, index) => {
    const goal = requireJsonObject(raw, "registry Goal");
    const activation = goal.activation == null ? null : requireJsonObject(goal.activation, "Goal activation");
    if (activation?.schema_version && activation.schema_version !== SCHEMA) {
      throw new EffectRuntimeRequestError("unsupported Goal activation schema");
    }
    const current = activation === null ? "active" : state(activation.state);
    const hasAlias = Object.hasOwn(goal, "activation_state");
    const alias = goal.activation_state == null ? null : state(goal.activation_state);
    const legacyPause = goal.status === "paused" || goal.status === "stopped";
    return {registry, index, goal, activation, current, legacyPause,
      legacy: legacyPause || hasAlias,
      stopped: legacyPause || alias === "stopped" || current === "stopped"};
  }));
  // Only an exact source route and the same Goal lifetime join a projection to
  // its source. A stale projection cannot stop a recreated Goal of the same name.
  const components = new Map<typeof nodes[number], Set<typeof nodes[number]>>();
  for (const node of nodes) components.set(node, new Set([node]));
  for (const node of nodes) {
    if (!node.goal.source_registry) continue;
    const matches = nodes.filter(candidate => candidate.registry.path === node.goal.source_registry
      && candidate.goal.id === node.goal.id
      && (candidate.goal.goal_instance_id ?? null) === (node.goal.goal_instance_id ?? null));
    if (matches.length > 1) throw new EffectRuntimeRequestError("ambiguous source Goal during activation upgrade");
    const source = matches[0];
    if (!source) continue;
    const merged = new Set([...components.get(node)!, ...components.get(source)!]);
    for (const member of merged) components.set(member, merged);
  }
  const replacements = new Map<typeof nodes[number], JsonObject>();
  for (const node of nodes) {
    const component = [...components.get(node)!];
    if (!component.some(member => member.legacy)) continue;
    // Conflicting historical representations never silently resume execution.
    // Resume remains an explicit owner operation after the backup and upgrade.
    const nextState = component.some(member => member.stopped) ? "stopped" : "active";
    if (nextState === "stopped" && node.registry.payload.profile_id === SOURCE_SESSION_PROFILE_ID
      && (node.legacyPause || node.current !== "stopped")) {
      throw new EffectRuntimeRequestError("Lifecycle-only source_session_v1 cannot migrate an execution pause before its lifecycle owner supports Resume");
    }
    if (!node.legacy && node.current === nextState) continue;
    replacements.set(node, {...node.activation, schema_version: SCHEMA, state: nextState});
  }
  return {registries: registries.map(registry => ({path: registry.path,
    changes: nodes.filter(node => node.registry === registry && replacements.has(node)).map(node => ({
      index: node.index, goal_id: requireNonEmptyString(node.goal.id, "Goal id"),
      activation: replacements.get(node)!,
      ...(node.legacyPause ? {status: "active"} : {}),
    })),
  }))};
}
