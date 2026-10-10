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
