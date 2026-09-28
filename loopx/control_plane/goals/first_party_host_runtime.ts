import type { JsonObject } from "../effect_program.ts";
import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import { assertNever, jsonObject } from "../runtime_decode.ts";
import {
  parseExactGoalRef,
  type ExactGoalRef,
} from "./goal_instance_identity.ts";

const SOURCE_SESSION_PROFILE_ID = "source_session_v1";

type WireGoalRef = Readonly<{
  goal_id: string;
  goal_instance_id: string;
}>;

type Operation = "select_state" | "require_current" | "accept_result";

type RejectionCode =
  | "goal_not_registered"
  | "goal_authority_unavailable"
  | "goal_instance_id_missing"
  | "stale_goal_instance"
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
  | Readonly<{ kind: "present"; goalRef: ExactGoalRef }>
  | Readonly<{ kind: "absent" }>
  | Readonly<{ kind: "unavailable" }>
  | Readonly<{ kind: "missing_instance" }>;

function requiredOperation(value: unknown): Operation {
  if (
    value === "select_state"
    || value === "require_current"
    || value === "accept_result"
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
        return { kind: "present", goalRef: parsed.value };
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

/**
 * Decide whether one first-party Host may select state or accept a result.
 *
 * Non-source profiles retain their historical behavior. The source-session
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
    return { kind: "legacy" };
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
      break;
    default:
      return assertNever(current, "unsupported Goal authority");
  }

  switch (operation) {
    case "select_state":
      return selectHostState(facts.host_state, planned.value);
    case "require_current":
      return { kind: "resume", goal_ref: wireGoalRef(planned.value) };
    case "accept_result":
      return { kind: "accept_result", goal_ref: wireGoalRef(planned.value) };
    default:
      return assertNever(operation, "unsupported Host runtime operation");
  }
}
