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
// Outcomes that a renamed identifier alone can produce. An external progress
// review found the evaluated identifiers not serving the goal, so for that
// source they discharge only behind evidence ids absent from the whole
// obligation window, and never by replaying a claim already made in it.
const PROGRESS_IDENTITY_OUTCOMES: ReadonlySet<string> = new Set(["new_surface", "new_hypothesis", "new_probe_family"]);
const VISION_TRIGGERS = new Set([
  "vision_acceptance_gap", "vision_checkpoint_missing", "vision_outcome_checkpoint_required",
  "vision_successor_required", "required_agent_vision_missing",
]);
const EXTERNAL_REVIEW_TRIGGERS = new Set(["external_progress_review_drift"]);
const FRESH_PATH_DISPOSITIONS = new Set(["continue", "no_change", "replan"]);
const PROGRESS_CLI_ARGS = "--progress-result-class <advanced|blocked|exploration_exhausted|no_followup> --progress-surface-id <surface-id> --progress-hypothesis-id <hypothesis-id> --progress-probe-kind <probe-kind> --progress-evidence-id <evidence-id>";
const VISION_CLI_ARGS = "--agent-vision-json '<path-to-evidence-linked-goal-vision-replan-contract-v0.json>'";

// Agent guidance only: the typed outcome/authority gates below remain the owner.
const REPLAN_PLANNING_GUIDANCE = [
  "Never shrink requested goals for easier tests. Retain unmet requirements; " +
    "honor user scope, authority, budget and stops.",
  "Claim achieved only with current authoritative evidence for every requirement. " +
    "Empty Todos/replan closure is not proof; unproven/blocked/exhausted/superseded is not achieved.",
];

function object(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? value as JsonObject : {};
}
function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(item => String(item ?? "").trim()).filter(Boolean) : [];
}
function triggerKinds(obligation: JsonObject): string[] {
  const triggers = Array.isArray(obligation.triggers) ? obligation.triggers : [];
  return triggers.map(trigger => String(object(trigger).kind ?? "").trim());
}
function isExternalReview(obligation: JsonObject): boolean {
  return triggerKinds(obligation).some(kind => EXTERNAL_REVIEW_TRIGGERS.has(kind));
}

/** One outcome policy for host projection and write-time discharge. */
export function requiredSemanticOutcomes(obligation: JsonObject): SemanticOutcome[] {
  const kinds = triggerKinds(obligation);
  const acceptanceHold = kinds.some(kind => kind === "goal_acceptance_stale" || kind === "goal_acceptance_unbound");
  const declared = strings(obligation.satisfying_semantic_outcomes);
  if (declared.length) {
    if (declared.some(value => !KNOWN_OUTCOMES.has(value))) {
      throw new EffectRuntimeRequestError("satisfying_semantic_outcomes contains an unknown typed outcome");
    }
    if (acceptanceHold && declared.some(value => !["new_runnable_successor", "new_concrete_blocker"].includes(value))) {
      throw new EffectRuntimeRequestError("acceptance recovery cannot widen its typed outcomes");
    }
    return [...new Set(declared)] as SemanticOutcome[];
  }
  if (acceptanceHold) return ["new_runnable_successor", "new_concrete_blocker"];
  if (kinds.some(kind => VISION_TRIGGERS.has(kind))) return [...VISION_OUTCOMES];
  // Reviewing a long chain may retain existing runnable work. Its projected
  // vision decision must close the checkpoint without manufacturing another
  // successor or progress identifier. Keep previously legal progress exits.
  // An external progress review likewise lets the Agent keep its plan on
  // evidence (a fresh vision path) or pivot with a typed progress delta.
  return kinds.includes("long_todo_chain") || kinds.some(kind => EXTERNAL_REVIEW_TRIGGERS.has(kind))
    ? ["fresh_vision_path_outcome", ...PROGRESS_OUTCOMES] : [...PROGRESS_OUTCOMES];
}

function writebackProjection(required: SemanticOutcome[], externalReview: boolean): JsonObject {
  // This chooses a usable refresh path, not the only legal exit. Successor and
  // terminal alternatives still have their own typed transitions and gates.
  if (externalReview) {
    return {
      cli_semantic_args: PROGRESS_CLI_ARGS,
      writeback_contract: {
        preferred_input: "typed_progress_observation",
        alternative_input: "evidence_linked_vision_path",
        alternative_cli_semantic_args: VISION_CLI_ARGS,
        vision_authoring: visionAuthoringContract(),
        path_outcomes: [...FRESH_PATH_DISPOSITIONS],
        identity_outcomes_require_new_evidence: true,
        rule: "Consecutive evaluated transitions did not serve the goal contract. Discharge with a typed observation whose new surface, hypothesis or probe family cites at least one evidence id absent from every claim in the obligation window, a new concrete blocker, or coverage-backed terminal state; or author an evidence-linked vision path (continue, no_change or replan) from observed evidence when the plan should stand. Renamed identifiers over the window's evidence ids, or a claim already made in the window, do not discharge.",
      },
    };
  }
  if (required.includes("fresh_vision_path_outcome")) {
    return {
      cli_semantic_args: VISION_CLI_ARGS,
      writeback_contract: {
        preferred_input: "evidence_linked_vision_path",
        vision_authoring: visionAuthoringContract(),
        required_fields: ["vision_patch.acceptance_summary", "path_delta.outcome", "path_delta.evidence_refs"],
        path_outcomes: [...FRESH_PATH_DISPOSITIONS],
        rule: "Author the JSON file from observed evidence, then execute the bound refresh and spend. This path requires an acceptance summary and evidence-linked path outcome; an unchanged reason alone is insufficient. Other required_any_of exits remain subject to their typed contracts.",
      },
    };
  }
  return {cli_semantic_args: PROGRESS_CLI_ARGS, writeback_contract: {}};
}

export function projectReplanSemantics(value: unknown): JsonObject {
  const request = requireJsonObject(value, "work_item.replan_semantics params");
  const obligation = requireJsonObject(request.obligation, "obligation");
  const required = requiredSemanticOutcomes(obligation);
  const externalReview = isExternalReview(obligation);
  if (request.operation === "requirements") {
    return {required_any_of: required, planning_guidance: [...REPLAN_PLANNING_GUIDANCE],
      ...writebackProjection(required, externalReview)};
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
  const identityOutcome = outcomes.some(outcome => PROGRESS_IDENTITY_OUTCOMES.has(outcome));
  // A claim already made while the obligation formed is not a new disposition,
  // whatever the delta against the single baseline says.
  const replayed = externalReview && observation.observation_repeated === true;
  const identityWithoutEvidence = externalReview && identityOutcome && !replayed &&
    observation.evidence_novel !== true;
  // A repeated observation contributes no new disposition, regardless of
  // whether the codec named an identity, blocker, successor or terminal exit.
  // Evaluate an independently evidenced Vision path after removing the replay.
  if (replayed) outcomes = [];
  else if (identityWithoutEvidence) outcomes = outcomes.filter(outcome => !PROGRESS_IDENTITY_OUTCOMES.has(outcome));
  const inconsistentTerminal = outcomes.includes("coverage_backed_no_followup") &&
    (vision.state !== "no_followup" || path.outcome !== "stop");
  if (inconsistentTerminal) outcomes = outcomes.filter(outcome => outcome !== "coverage_backed_no_followup");
  if (String(patch.acceptance_summary ?? "").trim() &&
      FRESH_PATH_DISPOSITIONS.has(String(path.outcome ?? "").trim()) &&
      strings(path.evidence_refs).length && !outcomes.includes("fresh_vision_path_outcome")) {
    outcomes.push("fresh_vision_path_outcome");
  }
  const satisfying = inconsistentTerminal ? [] : outcomes.filter(outcome => required.includes(outcome as SemanticOutcome));
  const replayRefused = replayed && !satisfying.length && !inconsistentTerminal;
  const identityRefused = identityWithoutEvidence && !satisfying.length && !inconsistentTerminal;
  return {
    schema_version: "replan_semantic_delta_v0", accepted: satisfying.length > 0,
    outcomes, satisfying_outcomes: satisfying, required_any_of: required,
    observation_fingerprint: observation.observation_fingerprint ?? null,
    reason: satisfying.length ? "writeback changes an outcome accepted by this obligation source"
      : inconsistentTerminal ? "coverage-backed no-follow-up requires agent_vision.state=no_followup and path_delta.outcome=stop"
      : replayRefused ? "external progress review does not accept a typed observation already claimed in the obligation window"
      : identityRefused ? "external progress review accepts a new surface, hypothesis or probe family only with evidence ids absent from the evaluated baseline and every claim in the obligation window"
      : "writeback does not satisfy this obligation's typed outcomes",
    ...(inconsistentTerminal ? {reason_code: "no_followup_vision_path_inconsistent"}
      : replayRefused ? {reason_code: "progress_observation_replayed"}
      : identityRefused ? {reason_code: "progress_identity_without_new_evidence"} : {}),
  };
}
