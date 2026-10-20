import assert from "node:assert/strict";
import test from "node:test";

import { decideFirstPartyHostRuntime } from "../../loopx/control_plane/goals/first_party_host_runtime.ts";

const INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const GOAL_A = { goal_id: "release", goal_instance_id: INSTANCE_A };
const GOAL_B = { goal_id: "release", goal_instance_id: INSTANCE_B };

function facts(
  operation: "select_state" | "require_current" | "launch" | "accept_result",
  overrides: Record<string, unknown> = {},
) {
  return {
    profile_id: "source_session_v1",
    operation,
    planned_goal_ref: GOAL_A,
    authority: {
      kind: "present",
      goal_ref: GOAL_A,
      activation_state: "active",
    },
    ...(operation === "select_state"
      ? { host_state: { kind: "absent" } }
      : {}),
    ...overrides,
  };
}

test("non-source profiles preserve legacy behavior without decoding strict facts", () => {
  assert.deepEqual(
    decideFirstPartyHostRuntime({
      profile_id: "legacy",
      operation: "unsupported",
      planned_goal_ref: null,
      authority: null,
      host_state: "malformed",
    }),
    { kind: "legacy" },
  );
});

test("legacy Host launch requires the original live registry witness without inventing a GoalRef", () => {
  const witness = {path: "/fixture/registry.json", sha256: "a".repeat(64)};
  const launch = {
    profile_id: null, operation: "launch", goal_id: "release", registry_path: witness.path,
    planned_registry_witness: witness, registry_witness: witness,
    authority: {kind: "present", goal_id: "release", registration_status: "active", activation_state: "active"},
  };
  assert.deepEqual(decideFirstPartyHostRuntime(launch), {kind: "legacy"});
  for (const status of ["active-read-only", " ACTIVE-READ-ONLY ", "ACTIVE", " ACTIVE-research "]) {
    const authority = {...launch.authority, registration_status: status};
    assert.deepEqual(decideFirstPartyHostRuntime({...launch, authority}), {kind: "legacy"});
    assert.deepEqual(decideFirstPartyHostRuntime({
      ...launch, authority: {...authority, activation_state: "stopped"},
    }), {kind: "reject", code: "goal_stopped"});
    assert.deepEqual(decideFirstPartyHostRuntime({
      ...launch, authority, registry_witness: {...witness, sha256: "b".repeat(64)},
    }), {kind: "reject", code: "stale_goal_authority"});
  }
  for (const status of ["activeish", "inactive", "retired", null, 1]) {
    assert.deepEqual(decideFirstPartyHostRuntime({
      ...launch, authority: {...launch.authority, registration_status: status},
    }), {kind: "reject", code: "goal_not_registered"});
  }
  for (const [authority, code] of [
    [{kind: "absent"}, "goal_not_registered"],
    [{kind: "unavailable"}, "goal_authority_unavailable"],
    [{...launch.authority, activation_state: "stopped"}, "goal_stopped"],
    [{...launch.authority, activation_state: "invalid"}, "goal_authority_unavailable"],
    [{...launch.authority, registration_status: "retired"}, "goal_not_registered"],
    [{...launch.authority, goal_id: "other"}, "goal_authority_unavailable"],
  ] as const) {
    assert.deepEqual(decideFirstPartyHostRuntime({...launch, authority}), {kind: "reject", code});
  }
  for (const changed of [{...witness, sha256: "b".repeat(64)}, {...witness, path: "/other/registry.json"}]) {
    assert.deepEqual(decideFirstPartyHostRuntime({...launch, registry_witness: changed}),
      {kind: "reject", code: "stale_goal_authority"});
  }
  for (const missing of [null, {}, {...witness, sha256: "not-a-digest"}]) {
    assert.deepEqual(decideFirstPartyHostRuntime({...launch, planned_registry_witness: missing}),
      {kind: "reject", code: "goal_authority_unavailable"});
  }
});

test("legacy native launch consumes the current quota owner without weakening source identity", () => {
  const witness = {path: "/fixture/registry.json", sha256: "a".repeat(64)};
  const legacy = {profile_id: null, operation: "launch", goal_id: "release",
    registry_path: witness.path, planned_registry_witness: witness, registry_witness: witness,
    authority: {kind: "present", goal_id: "release", registration_status: "active", activation_state: "active"}};
  const exact = {...facts("require_current"), operation: "launch"};
  const admitted = {planned_effective_action: "normal_run", current: {
    should_run: true, status_health_ok: true, effective_action: "normal_run"}};
  for (const launch_decision of [null, {}, {...admitted, current: {...admitted.current, should_run: false}},
    {...admitted, current: {...admitted.current, effective_action: "control_plane_health_repair"}},
    {...admitted, current: {...admitted.current, should_run: "true"}}]) {
    assert.deepEqual(decideFirstPartyHostRuntime({...legacy, launch_decision}),
      {kind: "reject", code: "goal_launch_not_allowed"});
  }
  assert.notEqual(decideFirstPartyHostRuntime({...legacy, launch_decision: admitted}).kind, "reject");
  assert.notEqual(decideFirstPartyHostRuntime({...legacy, launch_decision: {
    planned_effective_action: "control_plane_health_repair",
    current: {should_run: true, status_health_ok: false, effective_action: "control_plane_health_repair"},
  }}).kind, "reject");
  assert.deepEqual(decideFirstPartyHostRuntime({...exact,
    planned_goal_ref: {goal_id: "release"}, launch_decision: admitted}),
  {kind: "reject", code: "goal_instance_id_missing"});
});

test("current exact GoalRef admits each host runtime checkpoint", () => {
  assert.deepEqual(decideFirstPartyHostRuntime(facts("select_state")), {
    kind: "start_new",
    goal_ref: GOAL_A,
  });
  assert.deepEqual(
    decideFirstPartyHostRuntime(
      facts("select_state", {
        host_state: { kind: "present", goal_ref: GOAL_A },
      }),
    ),
    { kind: "resume", goal_ref: GOAL_A },
  );
  assert.deepEqual(decideFirstPartyHostRuntime(facts("require_current")), {
    kind: "resume",
    goal_ref: GOAL_A,
  });
  assert.deepEqual(decideFirstPartyHostRuntime(facts("launch")), {
    kind: "resume",
    goal_ref: GOAL_A,
  });
  assert.deepEqual(decideFirstPartyHostRuntime(facts("accept_result")), {
    kind: "accept_result",
    goal_ref: GOAL_A,
  });
});

test("Goal recreation rejects a stale plan at every checkpoint", () => {
  for (const operation of [
    "select_state",
    "require_current",
    "launch",
    "accept_result",
  ] as const) {
    assert.deepEqual(
      decideFirstPartyHostRuntime(
        facts(operation, {
          authority: { kind: "present", goal_ref: GOAL_B },
        }),
      ),
      { kind: "reject", code: "stale_goal_instance" },
      operation,
    );
  }
});

test("a stopped exact Goal rejects every host runtime checkpoint", () => {
  for (const operation of [
    "select_state",
    "require_current",
    "launch",
    "accept_result",
  ] as const) {
    assert.deepEqual(
      decideFirstPartyHostRuntime(
        facts(operation, {
          authority: {
            kind: "present",
            goal_ref: GOAL_A,
            activation_state: "stopped",
          },
        }),
      ),
      { kind: "reject", code: "goal_stopped" },
      operation,
    );
  }
});

test("strict mode rejects missing and unavailable Goal authority", () => {
  const cases = [
    {
      authority: { kind: "absent" },
      code: "goal_not_registered",
    },
    {
      authority: { kind: "unavailable", reason: "registry_missing" },
      code: "goal_authority_unavailable",
    },
    {
      authority: { kind: "unavailable", reason: "registry_unreadable" },
      code: "goal_authority_unavailable",
    },
    {
      authority: { kind: "present", goal_ref: { goal_id: "release" } },
      code: "goal_instance_id_missing",
    },
  ];

  for (const fixture of cases) {
    assert.deepEqual(
      decideFirstPartyHostRuntime(
        facts("require_current", { authority: fixture.authority }),
      ),
      { kind: "reject", code: fixture.code },
    );
  }
});

test("strict mode never infers an exact binding from legacy host state", () => {
  assert.deepEqual(
    decideFirstPartyHostRuntime(
      facts("select_state", {
        host_state: {
          kind: "present",
          goal_ref: { goal_id: "release" },
        },
      }),
    ),
    { kind: "reject", code: "legacy_host_state" },
  );
});

test("strict mode distinguishes stale and malformed host state", () => {
  assert.deepEqual(
    decideFirstPartyHostRuntime(
      facts("select_state", {
        host_state: { kind: "present", goal_ref: GOAL_B },
      }),
    ),
    {
      kind: "reject",
      code: "host_state_goal_instance_mismatch",
    },
  );
  for (const hostState of [
    null,
    {},
    { kind: "unknown" },
    {
      kind: "present",
      goal_ref: {
        goal_id: "release",
        goal_instance_id: "ginst_invalid",
      },
    },
  ]) {
    assert.deepEqual(
      decideFirstPartyHostRuntime(
        facts("select_state", { host_state: hostState }),
      ),
      { kind: "reject", code: "host_state_malformed" },
    );
  }
});

test("source mode rejects an unstamped plan before considering authority", () => {
  assert.deepEqual(
    decideFirstPartyHostRuntime(
      facts("require_current", {
        planned_goal_ref: { goal_id: "release" },
        authority: { kind: "absent" },
      }),
    ),
    { kind: "reject", code: "goal_instance_id_missing" },
  );
});
