import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  requireBoolean, requireJsonObject, requireNonEmptyString, requireStringArray,
  requireStringLiteral,
} from "../runtime_decode.ts";
import { PROGRESS_RESULT_CLASSES } from "./replan_history.ts";

const PROGRESS_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$/;

interface Observation {
  result: typeof PROGRESS_RESULT_CLASSES[number];
  fingerprint: string;
  surface_id?: string;
  hypothesis_id?: string;
  probe_kind?: string;
  blocker_id?: string;
  coverage_scope_id?: string;
  coverage_complete?: boolean;
  evidence: readonly string[];
}

function identifier(source: JsonObject, field: string): string | undefined {
  if (!Object.hasOwn(source, field)) return undefined;
  const identifier = requireNonEmptyString(source[field], field);
  if (!PROGRESS_IDENTIFIER.test(identifier)) {
    throw new EffectRuntimeRequestError(`${field} must be a normalized progress identifier`);
  }
  return identifier;
}

function evidenceIdentifiers(source: JsonObject): string[] {
  if (!Object.hasOwn(source, "evidence_ids")) return [];
  const identifiers = requireStringArray(source.evidence_ids, "evidence_ids");
  if (!identifiers.length || identifiers.length > 12 ||
      identifiers.some(identifier => !PROGRESS_IDENTIFIER.test(identifier))) {
    throw new EffectRuntimeRequestError("evidence_ids must contain 1-12 normalized progress identifiers");
  }
  return identifiers;
}

function decodeObservation(value: unknown): Observation {
  const source = requireJsonObject(value, "progress observation");
  requireStringLiteral(source.schema_version, ["typed_progress_observation_v0"], "progress schema");
  const fingerprint = requireNonEmptyString(source.fingerprint, "fingerprint");
  if (!/^[a-f0-9]{16}$/.test(fingerprint)) {
    throw new EffectRuntimeRequestError("fingerprint must be a normalized progress digest");
  }
  identifier(source, "work_item_id");
  const observation = {
    result: requireStringLiteral(source.result_class, PROGRESS_RESULT_CLASSES, "result_class"),
    fingerprint, surface_id: identifier(source, "surface_id"),
    hypothesis_id: identifier(source, "hypothesis_id"), probe_kind: identifier(source, "probe_kind"),
    blocker_id: identifier(source, "blocker_id"), coverage_scope_id: identifier(source, "coverage_scope_id"),
    coverage_complete: Object.hasOwn(source, "coverage_complete")
      ? requireBoolean(source.coverage_complete, "coverage_complete") : undefined,
    evidence: evidenceIdentifiers(source),
  };
  if (!["work_item_id", "surface_id", "hypothesis_id", "probe_kind", "blocker_id",
    "coverage_scope_id", "evidence_ids", "coverage_complete"].some(field => Object.hasOwn(source, field))) {
    throw new EffectRuntimeRequestError("progress observation requires a stable work, coverage or evidence field");
  }
  return observation;
}

function newTerminalCoverage(current: Observation, prior: Observation | null): boolean {
  if (prior === null || current.result !== prior.result ||
      current.coverage_scope_id !== prior.coverage_scope_id) return true;
  return current.result === "exploration_exhausted" && current.coverage_complete !== prior.coverage_complete;
}

function deltaKinds(current: Observation, prior: Observation | null): string[] {
  if (!current.evidence.length) return [];
  if (current.result === "advanced") {
    const dimensions = [
      ["surface_id", "new_surface"], ["hypothesis_id", "new_hypothesis"], ["probe_kind", "new_probe_family"],
    ] as const;
    return dimensions.filter(([field]) => current[field] && current[field] !== prior?.[field])
      .map(([, outcome]) => outcome);
  }
  if (current.result === "blocked") {
    return current.blocker_id && current.blocker_id !== prior?.blocker_id ? ["new_concrete_blocker"] : [];
  }
  if (!current.coverage_scope_id || !newTerminalCoverage(current, prior)) return [];
  if (current.result === "exploration_exhausted" && current.coverage_complete === true) {
    return ["coverage_backed_exploration_exhausted"];
  }
  return current.result === "no_followup" ? ["coverage_backed_no_followup"] : [];
}

/** Python supplies normalized source facts; only this owner qualifies their delta. */
export function projectProgressDelta(value: unknown): JsonObject {
  if (value === null) return {accepted: false, reason: "typed progress observation missing"};
  const source = requireJsonObject(value, "progress_source");
  const current = decodeObservation(source.current);
  const prior = source.baseline === null ? null : decodeObservation(source.baseline);
  if (!Array.isArray(source.window)) throw new EffectRuntimeRequestError("progress window must be an array");
  const claimed = source.window.map(decodeObservation);
  const known = prior === null ? claimed : [prior, ...claimed];
  const knownEvidence = new Set(known.flatMap(observation => observation.evidence));
  const kinds = deltaKinds(current, prior);
  return {
    schema_version: "replan_semantic_delta_v0", accepted: kinds.length > 0, delta_kinds: kinds,
    evidence_novel: current.evidence.some(evidence => !knownEvidence.has(evidence)),
    observation_repeated: known.some(observation => observation.fingerprint === current.fingerprint),
    blocker_repeated: !!current.blocker_id && known.some(observation =>
      observation.result === "blocked" && observation.blocker_id === current.blocker_id),
    window_size: claimed.length, observation_fingerprint: current.fingerprint,
    baseline_fingerprint: prior?.fingerprint ?? null,
    reason: kinds.length ? "typed observation changes the replan frontier"
      : "typed observation does not change an accepted semantic dimension",
  };
}
