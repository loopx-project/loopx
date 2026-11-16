import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { type JsonObject } from "../../loopx/control_plane/effect_program.ts";
import {
  PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
  UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA,
  preflightPriorHostTurnCloseout,
  reduceUnsettledHostTurnRecovery,
} from "../../loopx/control_plane/quota/unsettled_host_turn_recovery.ts";

const GOAL = "goal-a";
const AGENT = "agent-a";

interface Runtime {
  root: string;
  close(): Promise<void>;
}

/** A disposable runtime whose only persisted state is the receipts under test. */
async function runtimeWith(events: JsonObject[]): Promise<Runtime> {
  const root = await mkdtemp(join(tmpdir(), "loopx-closeout-"));
  const goalRoot = join(root, "goals", GOAL);
  await mkdir(goalRoot, {recursive: true});
  await writeFile(
    join(goalRoot, "rollout-event-log.jsonl"),
    events.map((event) => JSON.stringify(event)).join("\n") + "\n",
    "utf8",
  );
  return {root, close: () => rm(root, {recursive: true, force: true})};
}

function receipt(
  turn: string,
  details: JsonObject,
  overrides: JsonObject = {},
): JsonObject {
  return {
    schema_version: "loopx_rollout_event_v0",
    event_id: `event-${turn}`,
    event_kind: "quota_should_run",
    goal_id: GOAL,
    agent_id: AGENT,
    run_id: turn,
    details,
    ...overrides,
  };
}

function closeoutRequired(
  turn: string,
  todoId: string | null,
  replanId: string | null = null,
): JsonObject {
  return {
    ...(todoId ? {todo_id: todoId} : {}),
    ...(replanId ? {replan_obligation_id: replanId} : {}),
    ...(todoId ? {settlement_effect_id: `${GOAL}:${AGENT}:${todoId}:${turn}`} : {}),
    closeout_required: true,
  };
}

function preflight(runtimeRoot: string, exclude: string | null = "current-turn") {
  return preflightPriorHostTurnCloseout({
    schema_version: PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    goal_id: GOAL,
    agent_id: AGENT,
    exclude_turn_instance_id: exclude,
  });
}

function candidateFrom(result: JsonObject): JsonObject {
  assert.equal(result.status, "candidate");
  return result.candidate as JsonObject;
}

function reduce(
  candidate: JsonObject,
  missingReceipts: string[] = [
    "durable_writeback_receipt",
    "quota_spend_receipt",
  ],
  bindingFacts: JsonObject = {status: "not_read"},
) {
  return reduceUnsettledHostTurnRecovery({
    schema_version: UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA,
    goal_id: GOAL,
    agent_id: AGENT,
    candidate,
    missing_receipts: missingReceipts,
    binding_facts: bindingFacts,
  });
}

const READ_TODO = (todo: JsonObject | null, committed: JsonObject | null = null) => ({
  status: "read",
  todo,
  committed_monitor_poll: committed,
});

const ADVANCEMENT_OPEN = {
  task_class: "advancement_task",
  status: "open",
  has_resume_when: false,
  has_successor_todo_ids: false,
  target_key: null,
  cadence: null,
};

test("completed advancement cannot waive a durable writeback's owed spend", async () => {
  const runtime = await runtimeWith([
    receipt("turn-a", closeoutRequired("turn-a", "todo_alpha")),
  ]);
  try {
    const candidate = candidateFrom(await preflight(runtime.root));
    const verdict = reduce(
      candidate,
      ["quota_spend_receipt"],
      READ_TODO({
        ...ADVANCEMENT_OPEN,
        status: "done",
        has_successor_todo_ids: true,
      }),
    );
    assert.equal(verdict.status, "recovery_required");
    assert.equal(verdict.accepted_closeout, undefined);
    assert.deepEqual(
      (verdict.recovery as JsonObject).missing_receipts,
      ["quota_spend_receipt"],
    );
    assert.equal(
      (verdict.recovery as JsonObject).repair,
      "resume_prior_turn",
    );
    assert.equal(
      (verdict.recovery as JsonObject).defer_successor_admission,
      true,
    );
  } finally {
    await runtime.close();
  }
});
