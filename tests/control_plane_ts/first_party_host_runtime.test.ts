import assert from "node:assert/strict";
import test from "node:test";

import { decideFirstPartyHostRuntime } from "../../loopx/control_plane/goals/first_party_host_runtime.ts";

const INSTANCE_A = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const INSTANCE_B = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const GOAL_A = { goal_id: "release", goal_instance_id: INSTANCE_A };
const GOAL_B = { goal_id: "release", goal_instance_id: INSTANCE_B };

function facts(
  operation: "select_state" | "require_current" | "accept_result",
  overrides: Record<string, unknown> = {},
) {
  return {
    profile_id: "source_session_v1",
    operation,
    planned_goal_ref: GOAL_A,
    authority: { kind: "present", goal_ref: GOAL_A },
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
  assert.deepEqual(decideFirstPartyHostRuntime(facts("accept_result")), {
    kind: "accept_result",
    goal_ref: GOAL_A,
  });
});

test("Goal recreation rejects a stale plan at every checkpoint", () => {
  for (const operation of [
    "select_state",
    "require_current",
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
