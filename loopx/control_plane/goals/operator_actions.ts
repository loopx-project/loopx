import { EffectRuntimeRequestError } from "../effect_runtime_errors.ts";
import {
  optionalNonEmptyString,
  requireJsonObject,
  requireNonEmptyString,
} from "../runtime_decode.ts";

import type { JsonObject } from "../effect_program.ts";

export const GOAL_ACTION_PROJECTION_REQUEST_SCHEMA_VERSION =
  "loopx_goal_action_projection_request_v2";
export const GOAL_ACTION_CATALOG_SCHEMA_VERSION =
  "loopx_goal_action_catalog_v1";
export const GOAL_ACTION_SCHEMA_VERSION = "loopx_goal_action_v1";

const OPAQUE_ID = /^[A-Za-z0-9._:-]{1,200}$/;
const SHA256 = /^[a-f0-9]{64}$/;

function requireOpaqueId(value: unknown, label: string): string {
  const token = requireNonEmptyString(value, label);
  if (!OPAQUE_ID.test(token)) {
    throw new EffectRuntimeRequestError(`${label} must be a compact opaque id`);
  }
  return token;
}

function requireFingerprint(value: unknown): string {
  const fingerprint = requireNonEmptyString(value, "goal_action_request.state_fingerprint");
  if (!SHA256.test(fingerprint)) {
    throw new EffectRuntimeRequestError(
      "goal_action_request.state_fingerprint must be a SHA-256 digest",
    );
  }
  return fingerprint;
}

function lifecycleAction(
  goalId: string,
  activationState: "active" | "stopped",
  fingerprint: string,
  registryLocator: string,
  runtimeRootLocator: string | null,
): JsonObject {
  const stopping = activationState === "active";
  const operation = stopping ? "stop" : "resume";
  const argv = ["loopx", "--registry", registryLocator];
  if (runtimeRootLocator) {
    argv.push("--runtime-root", runtimeRootLocator);
  }
  argv.push("--format", "json");
  argv.push(
    "goal-lifecycle",
    "--goal-id",
    goalId,
    "--operation",
    operation,
    "--actor-kind",
    "owner",
    "--expected-state-fingerprint",
    fingerprint,
    "--execute",
  );
  return {
    schema_version: GOAL_ACTION_SCHEMA_VERSION,
    action_id: `goal.${operation}`,
    action_kind: "goal_lifecycle",
    label: stopping ? "Pause Goal" : "Resume Goal",
    goal_id: goalId,
    requires_confirmation: true,
    target_activation_state: stopping ? "stopped" : "active",
    target_operator_state: stopping ? "quiet" : "active",
    execution: {
      expected_state_fingerprint: fingerprint,
      argv,
    },
  };
}

/**
 * Project the complete bounded owner action set for one Goal snapshot.
 *
 * Python supplies only current source facts. This typed reducer owns which
 * lifecycle transition is legal and the exact, authority-bound execution argv
 * exposed to UIs. Operator-gate decisions stay on their existing command path
 * until that path has an equivalent freshness envelope.
 */
export function projectGoalOperatorActions(value: unknown): JsonObject {
  const request = requireJsonObject(value, "goal_action_request");
  if (request.schema_version !== GOAL_ACTION_PROJECTION_REQUEST_SCHEMA_VERSION) {
    throw new EffectRuntimeRequestError(
      `goal_action_request.schema_version must be ${GOAL_ACTION_PROJECTION_REQUEST_SCHEMA_VERSION}`,
    );
  }
  const goalId = requireOpaqueId(request.goal_id, "goal_action_request.goal_id");
  const registryLocator = requireNonEmptyString(
    request.registry_locator,
    "goal_action_request.registry_locator",
  );
  const runtimeRootLocator = optionalNonEmptyString(
    request.runtime_root_locator,
    "goal_action_request.runtime_root_locator",
  );
  const activationState = requireNonEmptyString(
    request.activation_state,
    "goal_action_request.activation_state",
  );
  if (activationState !== "active" && activationState !== "stopped") {
    throw new EffectRuntimeRequestError(
      "goal_action_request.activation_state must be active or stopped",
    );
  }
  const stateFingerprint = requireFingerprint(request.state_fingerprint);
  const actions: JsonObject[] = [];
  actions.push(
    lifecycleAction(
      goalId,
      activationState,
      stateFingerprint,
      registryLocator,
      runtimeRootLocator,
    ),
  );
  return {
    ok: true,
    schema_version: GOAL_ACTION_CATALOG_SCHEMA_VERSION,
    authority_owner: "typescript_control_plane",
    goal_id: goalId,
    activation_state: activationState,
    state_fingerprint: stateFingerprint,
    actions,
  };
}
