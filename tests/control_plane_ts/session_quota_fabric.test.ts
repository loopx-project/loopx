import assert from "node:assert/strict";
import test from "node:test";

import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  createEffectRuntimeHandlers,
  dispatchEffectRuntimeMethod,
} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import {
  decodeSessionQuotaWindowObservation,
  decodeSshHostObservation,
  evaluateGovernedSessionHandover,
  GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
  GOVERNED_SESSION_HANDOVER_RESULT_SCHEMA,
  isSafeHostAlias,
  projectSshHostCatalogPayload,
  SAFE_HOST_ALIAS,
  SESSION_FABRIC_SSH_FAILURE_CODES,
  SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
  SESSION_MODES,
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

test("valid OpenSSH aliases and identifiers containing sk- substrings are allowed while real secret prefixes and paths fail closed", () => {
  const validAliases = ["desk-box", "task-worker", "disk-01", "kiosk-2", "flask-api"];
  for (const alias of validAliases) {
    assert.equal(isSafeHostAlias(alias), true);
    assert.equal(SAFE_HOST_ALIAS.test(alias), true);
  }
  const catalog = projectSshHostCatalogPayload(validAliases);
  assert.deepEqual(catalog.hosts, validAliases.map((alias) => ({ alias })));

  const safeWindow = decodeSessionQuotaWindowObservation({
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "task-worker",
    availability: "near_limit",
    used_ratio: 0.91,
    resets_at: null,
    eligible_models: ["desk-box-model", "deepseek-chat", "kiosk-2"],
  });
  assert.equal(safeWindow.profile_id, "task-worker");
  assert.deepEqual(safeWindow.eligible_models, ["desk-box-model", "deepseek-chat", "kiosk-2"]);

  const safeHandover = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "task-worker-goal",
    todo_id: "todo_desk-box",
    agent_id: "kiosk-2-agent",
    from_profile_id: "task-worker",
    to_profile_id: "disk-01",
    from_session_mode: SESSION_MODES[0],
    to_session_mode: SESSION_MODES[0],
    quota_observation: safeWindow,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: false,
  });
  assert.equal(safeHandover.disposition, "ready_for_lease_reacquisition");

  const secretInputs = [
    "sk-" + "12345678",
    "sk-" + "proj-abcdef12",
    "sk-" + "ant-abcdef12",
    "sk-" + "live-abcdef12",
    "sk-" + "test-abcdef12",
    "sk_" + "live_abcdef12",
    "ghp" + "_abcdef12",
    "github_pat" + "_abcdef12",
  ];
  for (const secret of secretInputs) {
    assert.throws(() => projectSshHostCatalogPayload([secret]), EffectRuntimeRequestError);
    assert.throws(() => decodeSessionQuotaWindowObservation({
      schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
      profile_id: secret,
      availability: "near_limit",
      used_ratio: 0.9,
      resets_at: null,
      eligible_models: [],
    }), EffectRuntimeRequestError);
    assert.throws(() => decodeSessionQuotaWindowObservation({
      schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
      profile_id: "profile-a",
      availability: "near_limit",
      used_ratio: 0.9,
      resets_at: null,
      eligible_models: [secret],
    }), EffectRuntimeRequestError);
  }

  for (const unsafeText of [
    "Bearer " + "fixture1234",
    "/" + "Users/fixture/key",
    "/" + "home/fixture/key",
    "~/.ssh" + "/id_ed25519",
  ]) {
    assert.throws(() => decodeSessionQuotaWindowObservation({
      schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
      profile_id: "profile-a",
      availability: "near_limit",
      used_ratio: 0.9,
      resets_at: null,
      eligible_models: [unsafeText],
    }), EffectRuntimeRequestError);
  }
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

test("governed handover enforces observation.handover_eligible and availability counterfactuals", () => {
  // Counterfactual 1: available window with high used_ratio must stay no_handover_needed.
  const availableHighRatio = decodeSessionQuotaWindowObservation({
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "profile-primary",
    availability: "available",
    used_ratio: 0.95,
    resets_at: null,
    eligible_models: ["gpt-codex"],
  });
  assert.equal(availableHighRatio.handover_eligible, false);
  const availableResult = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: availableHighRatio,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: false,
  });
  assert.equal(availableResult.disposition, "no_handover_needed");
  assert.equal(availableResult.ready_for_reacquisition, false);

  // Counterfactual 2: exhausted window with low used_ratio must still enter handover gating/fencing.
  const exhaustedLowRatio = decodeSessionQuotaWindowObservation({
    schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
    profile_id: "profile-primary",
    availability: "exhausted",
    used_ratio: 0.5,
    resets_at: "2026-11-17T06:00:00Z",
    eligible_models: ["gpt-codex"],
  });
  assert.equal(exhaustedLowRatio.handover_eligible, true);
  const exhaustedReady = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: exhaustedLowRatio,
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: false,
  });
  assert.equal(exhaustedReady.disposition, "ready_for_lease_reacquisition");
  assert.equal(exhaustedReady.ready_for_reacquisition, true);

  // Counterfactual 3: near_limit below threshold_ratio must stay no_handover_needed.
  const nearLimitBelow = evaluateGovernedSessionHandover({
    schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
    goal_id: "demo-goal",
    todo_id: "todo_sample01",
    agent_id: "antigravity",
    from_profile_id: "profile-primary",
    to_profile_id: "profile-secondary",
    from_session_mode: "managed_runtime",
    to_session_mode: "managed_runtime",
    quota_observation: {
      schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
      profile_id: "profile-primary",
      availability: "near_limit",
      used_ratio: 0.85,
      resets_at: null,
      eligible_models: ["gpt-codex"],
    },
    threshold_ratio: 0.9,
    current_turn_fenced: true,
    previous_lease_released: true,
    ask_before_switch: false,
  });
  assert.equal(nearLimitBelow.disposition, "no_handover_needed");
  assert.equal(nearLimitBelow.ready_for_reacquisition, false);
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

test("effect_runtime_handlers dispatches registered session_fabric operations", async () => {
  const handlers = createEffectRuntimeHandlers({ fingerprint: "fabric-test", requestShutdown() {} });

  const featureOff = await dispatchEffectRuntimeMethod(
    handlers,
    "collaboration.session_fabric.project_ssh_host_catalog",
    { configured_aliases: ["desk-box", "task-worker"] },
  );
  assert.deepEqual(featureOff, {
    ok: true,
    schema_version: "ssh_host_catalog_v0",
    hosts: [{ alias: "desk-box" }, { alias: "task-worker" }],
  });

  const featureOn = await dispatchEffectRuntimeMethod(
    handlers,
    "collaboration.session_fabric.project_ssh_host_catalog",
    {
      configured_aliases: ["desk-box", "task-worker"],
      observations: [
        {
          schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
          alias: "desk-box",
          connection_state: "degraded",
          failure_code: "daemon_missing",
          checked_at: "2026-11-20T00:00:00Z",
          tunnel_latency_ms: 42,
        },
      ],
    },
  );
  assert.deepEqual(featureOn, {
    ok: true,
    schema_version: "ssh_host_catalog_v0",
    hosts: [
      {
        alias: "desk-box",
        observation: {
          schema_version: SESSION_FABRIC_SSH_OBSERVATION_SCHEMA,
          alias: "desk-box",
          connection_state: "degraded",
          failure_code: "daemon_missing",
          checked_at: "2026-11-20T00:00:00Z",
          tunnel_latency_ms: 42,
        },
      },
      { alias: "task-worker" },
    ],
  });

  const handover = await dispatchEffectRuntimeMethod(
    handlers,
    "collaboration.session_fabric.evaluate_governed_session_handover",
    {
      schema_version: GOVERNED_SESSION_HANDOVER_PROPOSAL_SCHEMA,
      goal_id: "demo-goal",
      todo_id: "todo_sample01",
      agent_id: "antigravity",
      from_profile_id: "profile-a",
      to_profile_id: "profile-b",
      from_session_mode: "attached_host",
      to_session_mode: "attached_host",
      quota_observation: {
        schema_version: SESSION_QUOTA_WINDOW_OBSERVATION_SCHEMA,
        profile_id: "profile-a",
        availability: "exhausted",
        used_ratio: 0.4,
        resets_at: null,
        eligible_models: ["gpt-codex"],
      },
      threshold_ratio: 0.9,
      current_turn_fenced: true,
      previous_lease_released: true,
      ask_before_switch: false,
    },
  );
  assert.equal((handover as Record<string, unknown>).disposition, "ready_for_lease_reacquisition");
});
