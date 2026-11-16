import assert from "node:assert/strict";
import test from "node:test";

import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  decodeSessionQuotaWindowObservation,
  decodeSshHostObservation,
  evaluateGovernedSessionHandover,
  GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
  GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA,
  projectSshHostCatalogPayload,
  SESSION_FABRIC_SSH_FAILURE_CODES,
  SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
  SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
} from "../../loopx/control_plane/collaboration/session_quota_fabric.ts";

test("ssh_host_catalog_v0 preserves byte-identical shape when L0 observations are absent", () => {
  const payload = projectSshHostCatalogPayload(["build-box", "gpu-1", "build-box"]);
  assert.deepEqual(payload, {
    ok: true,
    schema_version: "ssh_host_catalog_v0",
    hosts: [{ alias: "build-box" }, { alias: "gpu-1" }],
  });
  assert.deepEqual(Object.keys((payload.hosts as Record<string, unknown>[])[0]), ["alias"]);
});

test("SSH observations accept all seven typed failure codes and fail closed on contradictions or unknown aliases", () => {
  for (const code of SESSION_FABRIC_SSH_FAILURE_CODES) {
    const decoded = decodeSshHostObservation({
      schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
      alias: "gpu-1",
      connection_state: "disconnected",
      failure_code: code,
      checked_at: "2026-11-17T03:00:00Z",
      tunnel_latency_ms: null,
    });
    assert.equal(decoded.failure_code, code);
  }

  const projected = projectSshHostCatalogPayload(["gpu-1", "idle-box"], [
    {
      schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
      alias: "gpu-1",
      connection_state: "connected",
      failure_code: null,
      checked_at: "2026-11-17T03:00:00Z",
      tunnel_latency_ms: 14,
    },
  ]);
  const hosts = projected.hosts as Record<string, unknown>[];
  assert.equal((hosts[0].observation as Record<string, unknown>).tunnel_latency_ms, 14);
  assert.deepEqual(hosts[1], { alias: "idle-box" });

  assert.throws(() => decodeSshHostObservation({
    schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
    alias: "gpu-1",
    connection_state: "connected",
    failure_code: "unreachable",
    checked_at: null,
    tunnel_latency_ms: null,
  }), EffectRuntimeRequestError);

  assert.throws(() => projectSshHostCatalogPayload(["gpu-1"], [
    {
      schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
      alias: "phantom-host",
      connection_state: "disconnected",
      failure_code: "unreachable",
      checked_at: null,
      tunnel_latency_ms: null,
    },
  ]), /absent from ssh_host_catalog_v0/);

  assert.throws(() => projectSshHostCatalogPayload(["../bad"]), EffectRuntimeRequestError);
});

test("quota window observations require opaque profile ids and unknown usage never triggers handover", () => {
  assert.throws(() => decodeSessionQuotaWindowObservation({
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "user@example.com",
    availability: "near_limit",
    used_ratio: 0.9,
    resets_at: null,
    eligible_models: [],
  }), EffectRuntimeRequestError);

  assert.throws(() => decodeSessionQuotaWindowObservation({
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "profile-a",
    availability: "unknown",
    used_ratio: 0.95,
    resets_at: null,
    eligible_models: [],
  }), EffectRuntimeRequestError);

  const unknownWindow = decodeSessionQuotaWindowObservation({
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "profile-a",
    availability: "unknown",
    used_ratio: null,
    resets_at: null,
    eligible_models: ["model-a", "model-a"],
  });
  assert.equal(unknownWindow.handover_eligible, false);
  assert.deepEqual(unknownWindow.eligible_models, ["model-a"]);

  const belowOrUnknown = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-a",
    to_profile_id: "profile-b",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: unknownWindow,
    threshold_ratio: 0.85,
    current_turn_fenced: false,
    previous_lease_released: false,
    ask_before_switch: false,
  });
  assert.equal(belowOrUnknown.disposition, "no_handover_needed");
  assert.equal(belowOrUnknown.ready_for_reacquisition, false);
});

test("governed handover enforces session mode preservation, shared Gate, and turn/lease fencing", () => {
  const nearLimit = {
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "profile-primary",
    availability: "near_limit",
    used_ratio: 0.92,
    resets_at: "2026-11-17T06:00:00Z",
    eligible_models: ["gpt-codex"],
  };

  assert.throws(() => evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "attached_host",
    to_session_mode: "managed_runtime",
    quota_observation: nearLimit,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: false,
  }), /implicitly change execution mode/);

  const gated = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: nearLimit,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: true,
  });
  assert.equal(gated.schema_version, GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA);
  assert.equal(gated.disposition, "user_gate_required");
  assert.equal(gated.gate_required, true);
  assert.equal(gated.ready_for_reacquisition, false);

  const unfenced = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: nearLimit,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: false,
    ask_before_switch: false,
  });
  assert.equal(unfenced.disposition, "await_turn_fence");
  assert.equal(unfenced.turn_fence_satisfied, false);

  const ready = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: nearLimit,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: false,
  });
  assert.equal(ready.disposition, "ready_for_lease_reacquisition");
  assert.equal(ready.turn_fence_satisfied, true);
  assert.equal(ready.ready_for_reacquisition, true);
});
