/** Goal-owned improvement intent; never capability enablement or admission. */
import { createHash } from "node:crypto";
import type { AgentContextProvider } from "../agent_context.ts";
import type { JsonObject } from "../effect_program.ts";
import { jsonObject, requireJsonObject } from "../runtime_decode.ts";
import { ENVELOPED_SHA256_PATTERN } from "../content_digest.ts";

const DEFAULTS: JsonObject = { mode: "off", discovery_budget_minutes: 5, max_trials: 1 };
const FIELDS = Object.keys(DEFAULTS);
const identifier = (value: unknown): value is string => typeof value === "string"
  && /^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$/u.test(value);

export function normalizeImprovementPolicy(value: unknown): JsonObject {
  const input = value == null ? {} : requireJsonObject(value, "capability improvement policy");
  if (Object.keys(input).some(key => !FIELDS.includes(key))) {
    throw new Error("unsupported capability improvement policy field");
  }
  const policy = { ...DEFAULTS, ...input };
  if (typeof policy.mode !== "string" || !["off", "bounded"].includes(policy.mode)) {
    throw new Error("capability improvement mode must be off or bounded");
  }
  for (const [key, min, max] of [["discovery_budget_minutes", 1, 30], ["max_trials", 0, 2]] as const) {
    if (!Number.isInteger(policy[key]) || Number(policy[key]) < min || Number(policy[key]) > max) {
      throw new Error(`capability improvement ${key} must be an integer between ${min} and ${max}`);
    }
  }
  return policy;
}

export function inspectImprovementPolicy(value: unknown): JsonObject {
  try { return normalizeImprovementPolicy(value); }
  catch { return { ...DEFAULTS, configuration_status: "invalid" }; }
}

/** The existing registry transaction owns persistence, previews and CAS. */
export function planImprovementConfiguration(value: unknown): JsonObject {
  const input = requireJsonObject(value, "capability improvement configuration");
  if (input.clear === true && input.patch != null) throw new Error("clear conflicts with improvement settings");
  if (input.clear === true) return { configuration: null };
  const patch = requireJsonObject(input.patch, "capability improvement settings");
  return { configuration: normalizeImprovementPolicy({
    ...normalizeImprovementPolicy(input.current), ...patch,
  }) };
}

/** Bounded caller observations are advice, not proof of execution authority. */
export function planCapabilityImprovement(policyValue: unknown, observationValue: unknown, scopeValue?: unknown): JsonObject {
  const policy = normalizeImprovementPolicy(policyValue);
  const observation = jsonObject(observationValue) ?? {};
  const base: JsonObject = {
    schema_version: "goal_capability_improvement_plan_v0", authority: "advisory_only",
    mode: policy.mode, discovery_budget_minutes: policy.discovery_budget_minutes,
    max_trials: policy.max_trials, execution_authorized: false,
  };
  const none = (reason: string) => ({ ...base, recommendation: "continue_current_work", reason_code: reason });
  if (policy.mode === "off") return none("improvement_off");
  if (!identifier(observation.gap_ref)) return none("no_goal_gap");
  base.gap_ref = observation.gap_ref;
  if (!Array.isArray(observation.candidates) || observation.candidates.length === 0) {
    return { ...base, recommendation: "bounded_discovery", reason_code: "uncovered_goal_gap" };
  }
  if (observation.candidates.length > 8) throw new Error("improvement candidates exceed the bounded input");
  const candidates = observation.candidates.map(item => requireJsonObject(item, "improvement candidate"));
  // Direct use stays under its original owner: this advice never enables it,
  // replaces readiness checks, or requires a portfolio membership record.
  const direct = candidates.find(item => identifier(item.capability_id)
    && identifier(item.configuration_ref) && item.applicable === true && item.enabled === true);
  if (direct) return {
    ...base, recommendation: "inspect_direct_capability", reason_code: "existing_enabled_capability",
    capability_id: direct.capability_id, configuration_ref: direct.configuration_ref,
  };
  if (policy.max_trials === 0) return none("trial_budget_zero");
  const scope = jsonObject(scopeValue) ?? {};
  let reviewed: JsonObject | undefined;
  for (const trial of candidates) {
    if (!(identifier(trial.capability_id) && trial.applicable === true && trial.enabled === false
      && identifier(trial.configuration_ref) && identifier(trial.effect_ref) && identifier(trial.rollback_ref))) continue;
    // This digest identifies declared trial inputs, not authenticated owner truth.
    // Pin the caller and gap as well as the revision and original-owner references.
    if (trial.candidate_revision != null && !identifier(trial.candidate_revision)) {
      throw new Error("capability candidate revision must be a bounded reference");
    }
    const trialBasis = {
      goal_id: scope.goal_id ?? null, agent_id: scope.agent_id ?? null, todo_id: scope.todo_id ?? null,
      gap_ref: observation.gap_ref,
      capability_id: trial.capability_id, candidate_revision: trial.candidate_revision ?? null,
      configuration_ref: trial.configuration_ref, effect_ref: trial.effect_ref, rollback_ref: trial.rollback_ref,
    };
    const trial_basis_digest = `sha256:${createHash("sha256").update(JSON.stringify(trialBasis)).digest("hex")}`;
    const refs = { capability_id: trial.capability_id, configuration_ref: trial.configuration_ref,
      effect_ref: trial.effect_ref, rollback_ref: trial.rollback_ref, trial_basis_digest };
    if (trial.trial_feedback != null) {
      const feedback = requireJsonObject(trial.trial_feedback, "capability trial feedback");
      if (!identifier(feedback.outcome_ref) || typeof feedback.trial_basis_digest !== "string"
        || !ENVELOPED_SHA256_PATTERN.test(feedback.trial_basis_digest)
        || typeof feedback.status !== "string" || !["succeeded", "failed", "no_evidence"].includes(feedback.status)) {
        throw new Error("capability trial feedback requires an outcome reference, basis digest and receipt status");
      }
      const stale = feedback.trial_basis_digest !== trial_basis_digest;
      // A result is not utility. Ask the original owner to review successes and
      // stale feedback; do not propose the unchanged failed trial again.
      reviewed ??= { ...base, ...refs, outcome_ref: feedback.outcome_ref,
        recommendation: stale || feedback.status === "succeeded" ? "inspect_trial_outcome" : "continue_current_work",
        reason_code: stale ? "trial_feedback_stale" : feedback.status === "succeeded"
          ? "trial_result_requires_owner_review" : feedback.status === "failed" ? "prior_trial_failed" : "prior_trial_no_evidence" };
      continue;
    }
    return { ...base, ...refs, recommendation: "propose_reversible_trial",
      reason_code: "trial_has_effect_and_rollback_basis" };
  }
  if (reviewed) return reviewed;
  return none("missing_applicability_or_trial_basis");
}

export const improvementContextProvider: AgentContextProvider = {
  hookId: "goal_capability_organization.before_plan", capabilityId: "goal_capability_organization",
  revision: "v0", phases: ["before_plan"],
  produce(input, config) {
    const policy = normalizeImprovementPolicy(config.policy);
    const observation = jsonObject(input.observations.capability_improvement) ?? {};
    const plan = planCapabilityImprovement(policy, observation, input.scope);
    return {
      guidance: ["Use enabled capabilities directly. Trials need original-owner admission and current effect/rollback refs. Feedback is caller-declared: re-read its owner; success is not utility. Refine failed trials only with changed inputs and fresh feedback. Advice grants no authority; empty/failed advice must not stop useful work."],
      facts: {
        planning_trigger: observation.trigger === "replan" ? "replan" : "before_plan",
        ...Object.fromEntries(Object.entries(plan).filter(([key]) => ![
          "schema_version", "authority", "mode", "execution_authorized",
        ].includes(key))),
      },
      source_refs: ["goal.control_plane.capability_improvement", "loopx/capabilities/goal_capability_organization/README.md"],
    };
  },
};
