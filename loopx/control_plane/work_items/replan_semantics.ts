import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { requireJsonObject } from "../runtime_decode.ts";
import { visionAuthoringContract } from "../goals/vision_checkpoint.ts";

const PROGRESS_OUTCOMES = [
  "new_surface", "new_hypothesis", "new_probe_family", "new_runnable_successor",
  "coverage_backed_exploration_exhausted", "new_concrete_blocker", "coverage_backed_no_followup",
] as const;
const VISION_OUTCOMES = [
  "fresh_vision_path_outcome", "new_runnable_successor", "new_concrete_blocker",
  "coverage_backed_exploration_exhausted", "coverage_backed_no_followup",
] as const;
type SemanticOutcome = typeof PROGRESS_OUTCOMES[number] | typeof VISION_OUTCOMES[number];
const KNOWN_OUTCOMES: ReadonlySet<string> = new Set([...PROGRESS_OUTCOMES, ...VISION_OUTCOMES]);
const VISION_TRIGGERS = new Set([
  "vision_acceptance_gap", "vision_checkpoint_missing", "vision_outcome_checkpoint_required",
  "vision_successor_required", "required_agent_vision_missing",
]);
const FRESH_PATH_DISPOSITIONS = new Set(["continue", "no_change", "replan"]);

function object(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => String(item ?? "").trim()).filter(Boolean) : [];
}

/** One outcome policy for host projection and write-time discharge. */
export function requiredSemanticOutcomes(obligation: JsonObject): SemanticOutcome[] {
  const declared = strings(obligation.satisfying_semantic_outcomes);
  if (declared.length) {
    if (declared.some(value => !KNOWN_OUTCOMES.has(value))) {
      throw new EffectRuntimeRequestError("satisfying_semantic_outcomes contains an unknown typed outcome");
    }
    return [...new Set(declared)] as SemanticOutcome[];
  }
  const triggers = Array.isArray(obligation.triggers) ? obligation.triggers : [];
  const kinds = triggers.map(trigger => String(object(trigger).kind ?? "").trim());
  if (kinds.some(kind => VISION_TRIGGERS.has(kind))) return [...VISION_OUTCOMES];
  // Reviewing a long chain may retain existing runnable work. Its projected
  // vision decision must close the checkpoint without manufacturing another
  // successor or progress identifier. Keep previously legal progress exits.
  return kinds.includes("long_todo_chain")
    ? ["fresh_vision_path_outcome", ...PROGRESS_OUTCOMES] : [...PROGRESS_OUTCOMES];
}

function writebackProjection(required: SemanticOutcome[]): JsonObject {
  // This chooses a usable refresh path, not the only legal exit. Successor and
  // terminal alternatives still have their own typed transitions and gates.
  if (required.includes("fresh_vision_path_outcome")) {
    return {
      cli_semantic_args: "--agent-vision-json '<path-to-evidence-linked-goal-vision-replan-contract-v0.json>'",
      writeback_contract: {
        preferred_input: "evidence_linked_vision_path",
        vision_authoring: visionAuthoringContract(),
        required_fields: ["vision_patch.acceptance_summary", "path_delta.outcome", "path_delta.evidence_refs"],
        path_outcomes: [...FRESH_PATH_DISPOSITIONS],
        rule: "Author the JSON file from observed evidence, then execute the bound refresh and spend. This path requires an acceptance summary and evidence-linked path outcome; an unchanged reason alone is insufficient. Other required_any_of exits remain subject to their typed contracts.",
      },
    };
  }
  return {
    cli_semantic_args: "--progress-result-class <advanced|blocked|exploration_exhausted|no_followup> --progress-surface-id <surface-id> --progress-hypothesis-id <hypothesis-id> --progress-probe-kind <probe-kind> --progress-evidence-id <evidence-id>",
    writeback_contract: {},
  };
}

export function projectReplanSemantics(value: unknown): JsonObject {
  const request = requireJsonObject(value, "work_item.replan_semantics params");
  const obligation = requireJsonObject(request.obligation, "obligation");
  const required = requiredSemanticOutcomes(obligation);
  if (request.operation === "requirements") {
    return {required_any_of: required, ...writebackProjection(required)};
  }
  if (request.operation !== "qualify") {
    throw new EffectRuntimeRequestError("replan semantics operation must be requirements or qualify");
  }
  // The progress codec computes evidence novelty; this boundary decides which
  // outcomes discharge this obligation. Vision has already passed prepare.
  const observation = object(request.observation_delta);
  const vision = object(request.agent_vision);
  const patch = object(vision.vision_patch);
  const path = object(vision.path_delta);
  let outcomes = strings(observation.delta_kinds);
  if (outcomes.some(outcome => !KNOWN_OUTCOMES.has(outcome))) {
    throw new EffectRuntimeRequestError("observation_delta contains an unknown typed outcome");
  }
  const inconsistentTerminal = outcomes.includes("coverage_backed_no_followup") &&
    (vision.state !== "no_followup" || path.outcome !== "stop");
  if (inconsistentTerminal) outcomes = outcomes.filter(outcome => outcome !== "coverage_backed_no_followup");
  if (String(patch.acceptance_summary ?? "").trim() &&
      FRESH_PATH_DISPOSITIONS.has(String(path.outcome ?? "").trim()) &&
      strings(path.evidence_refs).length && !outcomes.includes("fresh_vision_path_outcome")) {
    outcomes.push("fresh_vision_path_outcome");
  }
  const satisfying = inconsistentTerminal ? [] : outcomes.filter(outcome => required.includes(outcome as SemanticOutcome));
  return {
    schema_version: "replan_semantic_delta_v0", accepted: satisfying.length > 0,
    outcomes, satisfying_outcomes: satisfying, required_any_of: required,
    observation_fingerprint: observation.observation_fingerprint ?? null,
    reason: satisfying.length ? "writeback changes an outcome accepted by this obligation source"
      : inconsistentTerminal ? "coverage-backed no-follow-up requires agent_vision.state=no_followup and path_delta.outcome=stop"
      : "writeback does not satisfy this obligation's typed outcomes",
    ...(inconsistentTerminal ? {reason_code: "no_followup_vision_path_inconsistent"} : {}),
  };
}
