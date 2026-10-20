import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { assertNever, jsonObject } from "../runtime_decode.ts";
import { BARE_SHA256_PATTERN } from "../content_digest.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "./goal_instance_identity.ts";

const SOURCE_SESSION_PROFILE_ID = "source_session_v1";

type WireGoalRef = Readonly<{
  goal_id: string;
  goal_instance_id: string;
}>;

type Operation = "select_state" | "require_current" | "accept_result" | "launch";

type RejectionCode =
  | "goal_not_registered"
  | "goal_authority_unavailable"
  | "goal_instance_id_missing"
  | "goal_stopped"
  | "stale_goal_instance"
  | "stale_goal_authority"
  | "goal_launch_not_allowed"
  | "legacy_host_state"
  | "host_state_goal_instance_mismatch"
  | "host_state_malformed";

export type FirstPartyHostRuntimeDecision =
  | Readonly<{ kind: "legacy" }>
  | Readonly<{ kind: "start_new"; goal_ref: WireGoalRef }>
  | Readonly<{ kind: "resume"; goal_ref: WireGoalRef }>
  | Readonly<{ kind: "accept_result"; goal_ref: WireGoalRef }>
  | Readonly<{ kind: "reject"; code: RejectionCode }>;

type Authority =
  | Readonly<{
    kind: "present";
    goalRef: ExactGoalRef;
    activationState: "active" | "stopped";
  }>
  | Readonly<{ kind: "absent" }>
  | Readonly<{ kind: "unavailable" }>
  | Readonly<{ kind: "missing_instance" }>;

function requiredOperation(value: unknown): Operation {
  if (
    value === "select_state"
    || value === "require_current"
    || value === "accept_result"
    || value === "launch"
  ) {
    return value;
  }
  throw new EffectRuntimeRequestError(
    "first-party host runtime operation is unsupported",
  );
}

function wireGoalRef(value: ExactGoalRef): WireGoalRef {
  return {
    goal_id: value.goalId.value,
    goal_instance_id: value.goalInstanceId.value,
  };
}

function sameGoalRef(left: ExactGoalRef, right: ExactGoalRef): boolean {
  return left.goalId.value === right.goalId.value
    && left.goalInstanceId.value === right.goalInstanceId.value;
}

function authority(value: unknown): Authority {
  const raw = jsonObject(value);
  if (!raw) return { kind: "unavailable" };
  switch (raw.kind) {
    case "absent":
      return { kind: "absent" };
    case "unavailable":
      return { kind: "unavailable" };
    case "present": {
      const parsed = parseExactGoalRef(raw.goal_ref);
      if (parsed.kind === "parsed") {
        const activationState = raw.activation_state ?? "active";
        if (activationState !== "active" && activationState !== "stopped") {
          return { kind: "unavailable" };
        }
        return {
          kind: "present",
          goalRef: parsed.value,
          activationState,
        };
      }
      return parsed.issue === "missing_goal_instance_id"
        ? { kind: "missing_instance" }
        : { kind: "unavailable" };
    }
    default:
      return { kind: "unavailable" };
  }
}

function selectHostState(
  value: unknown,
  plannedGoalRef: ExactGoalRef,
): FirstPartyHostRuntimeDecision {
  const state = jsonObject(value);
  if (!state) {
    return { kind: "reject", code: "host_state_malformed" };
  }
  if (state.kind === "absent") {
    return { kind: "start_new", goal_ref: wireGoalRef(plannedGoalRef) };
  }
  if (state.kind !== "present") {
    return { kind: "reject", code: "host_state_malformed" };
  }
  const parsed = parseExactGoalRef(state.goal_ref);
  if (parsed.kind === "invalid") {
    return {
      kind: "reject",
      code: parsed.issue === "missing_goal_instance_id"
        ? "legacy_host_state"
        : "host_state_malformed",
    };
  }
  if (!sameGoalRef(parsed.value, plannedGoalRef)) {
    return {
      kind: "reject",
      code: "host_state_goal_instance_mismatch",
    };
  }
  return { kind: "resume", goal_ref: wireGoalRef(plannedGoalRef) };
}

function registryWitness(value: unknown): {path: string; sha256: string} | null {
  const witness = jsonObject(value);
  return witness && typeof witness.path === "string" && !!witness.path
    && typeof witness.sha256 === "string" && BARE_SHA256_PATTERN.test(witness.sha256)
    ? {path: witness.path, sha256: witness.sha256} : null;
}

/** A legacy launch rechecks its captured registry; it gains no GoalRef. */
function legacyLaunch(facts: JsonObject): FirstPartyHostRuntimeDecision {
  const current = jsonObject(facts.authority);
  if (current?.kind === "absent") return {kind: "reject", code: "goal_not_registered"};
  if (current?.kind !== "present" || typeof facts.goal_id !== "string" || !facts.goal_id
    || current.goal_id !== facts.goal_id) return {kind: "reject", code: "goal_authority_unavailable"};
  // Legacy registration accepts the active-* variants used by the existing
  // Goal frontier. Source-session registration remains exact-only below.
  const registration = typeof current.registration_status === "string"
    ? current.registration_status.trim().toLowerCase() : "";
  if (registration !== "active" && !registration.startsWith("active-")) {
    return {kind: "reject", code: "goal_not_registered"};
  }
  if (current.activation_state === "stopped") return {kind: "reject", code: "goal_stopped"};
  if (current.activation_state !== "active") return {kind: "reject", code: "goal_authority_unavailable"};
  const planned = registryWitness(facts.planned_registry_witness);
  const observed = registryWitness(facts.registry_witness);
  if (!planned || !observed || planned.path !== facts.registry_path) {
    return {kind: "reject", code: "goal_authority_unavailable"};
  }
  if (planned.path !== observed.path || planned.sha256 !== observed.sha256) {
    return {kind: "reject", code: "stale_goal_authority"};
  }
  const rejected = launchDecisionRejection(facts);
  if (rejected) return rejected;
  return {kind: "legacy"};
}

function launchDecisionRejection(facts: JsonObject): FirstPartyHostRuntimeDecision | null {
  if (!("launch_decision" in facts)) return null;
  const launch = jsonObject(facts.launch_decision);
  const decision = jsonObject(launch?.current);
  // Consume the current quota owner's decision; do not reproduce its quota,
  // health, repair, replan or scheduler policy in the Host adapter.
  // A newly selected repair/replan is not permission to launch the old action.
  // An already-admitted health repair keeps the quota owner's exception.
  return decision?.should_run === true && typeof decision.status_health_ok === "boolean"
    && typeof launch?.planned_effective_action === "string" && !!launch.planned_effective_action
    && decision.effective_action === launch.planned_effective_action
    ? null : {kind: "reject", code: "goal_launch_not_allowed"};
}

/**
 * Decide whether one first-party Host may select state or accept a result.
 *
 * Non-source state I/O retains its historical behavior; Host launch separately
 * requires a current captured registry witness. The source-session
 * profile is exact-only: neither an alias-only plan nor alias-only Host state
 * can be upgraded by inference.
 */
export function decideFirstPartyHostRuntime(
  value: unknown,
): FirstPartyHostRuntimeDecision {
  const facts = jsonObject(value);
  if (!facts) {
    throw new EffectRuntimeRequestError(
      "first-party host runtime facts must be an object",
    );
  }
  if (facts.profile_id !== SOURCE_SESSION_PROFILE_ID) {
    return facts.operation === "launch" ? legacyLaunch(facts) : { kind: "legacy" };
  }

  const operation = requiredOperation(facts.operation);
  const planned = parseExactGoalRef(facts.planned_goal_ref);
  if (planned.kind === "invalid") {
    return { kind: "reject", code: "goal_instance_id_missing" };
  }
  const current = authority(facts.authority);
  switch (current.kind) {
    case "absent":
      return { kind: "reject", code: "goal_not_registered" };
    case "unavailable":
      return { kind: "reject", code: "goal_authority_unavailable" };
    case "missing_instance":
      return { kind: "reject", code: "goal_instance_id_missing" };
    case "present":
      if (!sameGoalRef(planned.value, current.goalRef)) {
        return { kind: "reject", code: "stale_goal_instance" };
      }
      if (current.activationState === "stopped") {
        return { kind: "reject", code: "goal_stopped" };
      }
      break;
    default:
      return assertNever(current, "unsupported Goal authority");
  }

  switch (operation) {
    case "select_state":
      return selectHostState(facts.host_state, planned.value);
    case "require_current":
    case "launch":
      return { kind: "resume", goal_ref: wireGoalRef(planned.value) };
    case "accept_result":
      return { kind: "accept_result", goal_ref: wireGoalRef(planned.value) };
    default:
      return assertNever(operation, "unsupported Host runtime operation");
  }
}
