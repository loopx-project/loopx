import assert from "node:assert/strict";
import test from "node:test";

import {
  deriveDshInteractiveReadiness,
  deriveDshManagedReadiness,
} from "./dsh-host-readiness.ts";

const missingRuntime = {
  agent_id: "dsh",
  available: false,
  unavailable_reason: "dsh_runtime_unavailable",
  execution_profile: "deepseek-flash@high",
};

test("managed Turn readiness keeps runtime, credential and profile states distinct", () => {
  assert.deepEqual(deriveDshManagedReadiness(undefined), {
    kind: "not_observed", profile: null, reason: null,
  });
  assert.deepEqual(deriveDshManagedReadiness([missingRuntime]), {
    kind: "needs_runtime", profile: "deepseek-flash@high", reason: "dsh_runtime_unavailable",
  });
  assert.deepEqual(deriveDshManagedReadiness([{
    ...missingRuntime, unavailable_reason: "operator_credential_unconfigured",
  }]), {
    kind: "needs_credential", profile: "deepseek-flash@high",
    reason: "operator_credential_unconfigured",
  });
  assert.deepEqual(deriveDshManagedReadiness([{
    ...missingRuntime, available: true, unavailable_reason: null,
  }]), {
    kind: "ready", profile: "deepseek-flash@high", reason: null,
  });
});

test("an unknown refusal is never presented as a known repair", () => {
  assert.deepEqual(deriveDshManagedReadiness([{
    ...missingRuntime, unavailable_reason: "future_managed_refusal",
  }]), {
    kind: "unknown", profile: "deepseek-flash@high", reason: "future_managed_refusal",
  });
});

test("configuration refusals name the source that can actually repair them", () => {
  assert.deepEqual(deriveDshManagedReadiness([{
    ...missingRuntime,
    unavailable_reason: "invalid_reasoning_effort",
    execution_profile_details: {
      provider: "deepseek-official",
      provider_source: "product_default",
      provider_env_var: "",
      model: "deepseek-flash",
      model_source: "product_default",
      model_env_var: "",
      reasoning_effort: "bad",
      reasoning_effort_source: "env_override",
      reasoning_effort_env_var: "LOOPX_TURN_REASONING_EFFORT",
      reasoning_effort_supported: false,
    },
  }]), {
    kind: "needs_configuration",
    profile: "deepseek-flash@high",
    reason: "invalid_reasoning_effort",
    repair: {
      kind: "environment_variable",
      variable: "LOOPX_TURN_REASONING_EFFORT",
    },
  });
  assert.deepEqual(deriveDshManagedReadiness([{
    ...missingRuntime,
    unavailable_reason: "invalid_output_token_limit",
  }]), {
    kind: "needs_configuration",
    profile: "deepseek-flash@high",
    reason: "invalid_output_token_limit",
    repair: {kind: "service_startup", setting: "max-token"},
  });
});

test("interactive DSH binding counts Goals and never implies liveness", () => {
  assert.deepEqual(deriveDshInteractiveReadiness([
    {goalId: "goal-a", boundHostSurfaces: ["codex-app", "deepseek-harness-native"]},
    {goalId: "goal-b", boundHostSurfaces: ["deepseek-harness-native"]},
    {goalId: "goal-c", boundHostSurfaces: ["codex-app"]},
  ]), {
    kind: "bound", boundGoalCount: 2, boundGoalIds: ["goal-a", "goal-b"],
  });
  assert.deepEqual(deriveDshInteractiveReadiness([
    {goalId: "goal-c", boundHostSurfaces: []},
  ]), {
    kind: "not_bound", boundGoalCount: 0, boundGoalIds: [],
  });
});
