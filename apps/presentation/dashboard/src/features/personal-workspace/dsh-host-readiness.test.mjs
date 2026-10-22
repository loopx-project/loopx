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
