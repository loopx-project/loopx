/** Explore Harness product modes; legacy storage fields remain replayable. */
import type {JsonObject} from "../effect_program.ts";
import {requireJsonObject} from "../runtime_decode.ts";
import {EffectRuntimeRequestError} from "../effect_runtime_errors.ts";

export const EXPLORE_MODES = ["off", "evidence", "planning"] as const;
export type ExploreMode = typeof EXPLORE_MODES[number];

function mode(value: unknown): ExploreMode {
  if (!EXPLORE_MODES.includes(value as ExploreMode)) {
    throw new EffectRuntimeRequestError("explore_mode must be off, evidence or planning");
  }
  return value as ExploreMode;
}

export function resolveExploreConfiguration(params: JsonObject): JsonObject {
  const planning = params.planning_enabled === true;
  const evidence = params.evidence_enabled === true || planning;
  return {mode: planning ? "planning" : evidence ? "evidence" : "off",
    evidence_enabled: evidence, planning_enabled: planning};
}

/** Resolve one mode for both live runtime policy and configuration readback.
 * Legacy enabled fields are explicit Goal choices, including false. A profile
 * or composition setting alone does not pin the mode. No authority is added.
 */
export function resolveExploreGoalConfiguration(params: JsonObject): JsonObject {
  const graph = requireJsonObject(params.graph ?? {}, "Explore graph configuration");
  const harness = requireJsonObject(params.harness ?? {}, "Explore harness configuration");
  const explicit = Object.hasOwn(graph, "enabled") || Object.hasOwn(harness, "enabled");
  const machine = params.machine_mode == null ? null : mode(params.machine_mode);
  const selected = explicit
    ? resolveExploreConfiguration({evidence_enabled: graph.enabled, planning_enabled: harness.enabled})
    : planExploreConfiguration({current: {}, changes: {mode: machine ?? "off"}});
  const override: JsonObject = {};
  if (explicit) override.mode = selected.mode;
  for (const key of ["profile", "composition_mode", "composition_scope_id"]) {
    if (Object.hasOwn(harness, key)) override[key] = harness[key];
  }
  return {
    ...selected,
    mode_source: explicit ? "goal_override" : machine == null ? "capability_default" : "machine_default",
    goal_override: Object.keys(override).length ? override : null,
    configuration: {profile: "generic", composition_mode: "disabled", composition_scope_id: null,
      ...override, mode: selected.mode},
  };
}

export function planExploreConfiguration(params: JsonObject): JsonObject {
  const current = requireJsonObject(params.current, "Explore current configuration");
  const changes = requireJsonObject(params.changes, "Explore configuration changes");
  const desired = changes.mode;
  const graph = changes.evidence_enabled;
  const harness = changes.planning_enabled;
  if (desired != null && (graph != null || harness != null)) {
    throw new EffectRuntimeRequestError("Use --explore-mode or legacy Explore flags, not both");
  }
  if (desired != null) {
    const selected = mode(desired);
    return {mode: selected, evidence_enabled: selected !== "off", planning_enabled: selected === "planning"};
  }
  for (const value of [graph, harness]) {
    if (value != null && typeof value !== "boolean") {
      throw new EffectRuntimeRequestError("Explore enable flags must be boolean");
    }
  }
  const planning = harness ?? current.planning_enabled === true;
  if (graph === false && planning) {
    throw new EffectRuntimeRequestError("Explore planning requires its evidence graph; use --explore-mode off to disable both, or --explore-mode evidence to keep evidence only");
  }
  return resolveExploreConfiguration({planning_enabled: planning,
    evidence_enabled: graph ?? current.evidence_enabled === true});
}
