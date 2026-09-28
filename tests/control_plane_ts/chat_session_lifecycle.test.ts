import assert from "node:assert/strict";
import test from "node:test";

import {
  decideChatSessionLifecycle,
} from "../../loopx/control_plane/goals/chat_session_lifecycle.ts";

const goalA = {
  goal_id: "release",
  goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
};
const goalB = {
  goal_id: "release",
  goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
};

function session(
  sessionId: string,
  goalRef: typeof goalA | null,
  updatedAt = "2026-09-26T00:00:00Z",
) {
  return {
    session_id: sessionId,
    goal_id: "release",
    goal_instance_id: goalRef?.goal_instance_id ?? null,
    updated_at: updatedAt,
  };
}

test("legacy profiles preserve the existing path", () => {
  assert.deepEqual(
    decideChatSessionLifecycle({
      operation: "select",
      profile_id: null,
      current_goal_ref: null,
      candidates: [],
    }),
    { kind: "legacy" },
  );
});

test("selection reuses only the newest exact current session", () => {
  assert.deepEqual(
    decideChatSessionLifecycle({
      operation: "select",
      profile_id: "source_session_v1",
      current_goal_ref: goalB,
      candidates: [
        session("legacy", null, "2026-09-26T03:00:00Z"),
        session("stale-a", goalA, "2026-09-26T04:00:00Z"),
        session("current-old", goalB, "2026-09-26T01:00:00Z"),
        session("current-new", goalB, "2026-09-26T02:00:00Z"),
      ],
    }),
    { kind: "reuse", session_id: "current-new", goal_ref: goalB },
  );
});

test("selection creates a new exact session when only stale state exists", () => {
  assert.deepEqual(
    decideChatSessionLifecycle({
      operation: "select",
      profile_id: "source_session_v1",
      current_goal_ref: goalB,
      candidates: [session("stale-a", goalA), session("legacy", null)],
    }),
    { kind: "create", goal_ref: goalB },
  );
});

test("new work requires the session GoalRef to be current", () => {
  const base = {
    profile_id: "source_session_v1",
    current_goal_ref: goalB,
    session: session("session-a", goalA),
    turn: null,
  };
  for (const operation of ["admit", "claim"] as const) {
    assert.deepEqual(
      decideChatSessionLifecycle({ ...base, operation }),
      { kind: "reject", code: "stale_goal_instance" },
    );
  }
  assert.deepEqual(
    decideChatSessionLifecycle({
      ...base,
      operation: "admit",
      session: session("session-b", goalB),
    }),
    { kind: "allow_current", goal_ref: goalB },
  );
});

test("claim replay and completion allow exact historical admitted work", () => {
  const historical = {
    profile_id: "source_session_v1",
    current_goal_ref: goalB,
    session: session("session-a", goalA),
    turn: {
      goal_id: goalA.goal_id,
      goal_instance_id: goalA.goal_instance_id,
      admitted_goal_instance_id: goalA.goal_instance_id,
    },
  };
  for (const operation of ["replay_claim", "complete"] as const) {
    assert.deepEqual(
      decideChatSessionLifecycle({ ...historical, operation }),
      { kind: "allow_historical", goal_ref: goalA },
    );
  }
});

test("historical completion rejects unstamped and mismatched turns", () => {
  const base = {
    operation: "complete",
    profile_id: "source_session_v1",
    current_goal_ref: goalB,
    session: session("session-a", goalA),
  };
  assert.deepEqual(
    decideChatSessionLifecycle({
      ...base,
      turn: {
        goal_id: goalA.goal_id,
        goal_instance_id: goalA.goal_instance_id,
        admitted_goal_instance_id: null,
      },
    }),
    { kind: "reject", code: "turn_not_admitted" },
  );
  assert.deepEqual(
    decideChatSessionLifecycle({
      ...base,
      turn: {
        goal_id: goalA.goal_id,
        goal_instance_id: goalB.goal_instance_id,
        admitted_goal_instance_id: goalB.goal_instance_id,
      },
    }),
    { kind: "reject", code: "turn_goal_instance_mismatch" },
  );
});

test("strict operations reject unstamped sessions and malformed GoalRefs", () => {
  assert.deepEqual(
    decideChatSessionLifecycle({
      operation: "admit",
      profile_id: "source_session_v1",
      current_goal_ref: goalA,
      session: session("legacy", null),
      turn: null,
    }),
    { kind: "reject", code: "goal_instance_id_missing" },
  );
  assert.throws(
    () => decideChatSessionLifecycle({
      operation: "select",
      profile_id: "source_session_v1",
      current_goal_ref: {
        goal_id: "release",
        goal_instance_id: "bad",
      },
      candidates: [],
    }),
    /invalid_goal_instance_id/,
  );
});
