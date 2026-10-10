import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs, {
  mkdir,
  mkdtemp,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { syncBuiltinESMExports } from "node:module";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { createInterface } from "node:readline";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";

import { resolveTestPython } from "../../scripts/test-python.mjs";
import {
  evaluateSchedulerHeartbeatFollowup,
  SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
} from "../../loopx/control_plane/scheduler/heartbeat_followup.ts";
import { schedulerStatePath } from "../../loopx/control_plane/scheduler/state_store.ts";

type CleanupContext = {
  after(callback: () => void | Promise<void>): void;
};

const OPERATIONS = ["ack", "host_failure"] as const;
type Operation = (typeof OPERATIONS)[number];
const PYTHON = resolveTestPython();
const REPOSITORY_ROOT = join(import.meta.dirname, "..", "..");
const PYTHON_RECEIPT_WRITER = [
  "import sys",
  "from pathlib import Path",
  "from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path",
  "runtime_root, goal_id, agent_id, turn_id = sys.argv[1:]",
  "event = build_rollout_event(goal_id=goal_id, event_kind='quota_should_run', agent_id=agent_id, run_id=turn_id)",
  "print('started', flush=True)",
  "append_rollout_event(rollout_event_log_path(Path(runtime_root), goal_id), event)",
  "print('appended', flush=True)",
].join("\n");

const scope = {
  goal_id: "goal-followup-fence",
  agent_id: "agent-followup-fence",
  surface: "codex_app",
  state_key: "scheduler_hint.app_automation.stateful_backoff",
};

const before = {
  should_run: true,
  normal_delivery_allowed: true,
  recovery_delivery_allowed: false,
  effective_action: "normal_run",
  self_repair_allowed: false,
  capability_repair_allowed: false,
  workspace_repair_allowed: false,
  state: "eligible",
  safe_bypass_allowed: false,
  safe_bypass_kind: null,
  blocked_action_scope: null,
  compute: 1,
  window_hours: 4,
  slot_minutes: 15,
  spent_slots: 0,
  allowed_slots: 16,
};

async function tempRuntime(t: CleanupContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-receipt-fence-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
}

async function appendReceipt(
  runtimeRoot: string,
  turnId: string,
): Promise<void> {
  const path = join(
    runtimeRoot,
    "goals",
    scope.goal_id,
    "rollout-event-log.jsonl",
  );
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: scope.agent_id,
    run_id: turnId,
  })}\n`, { encoding: "utf8", flag: "a" });
}

function followupRequest(
  runtimeRoot: string,
  operation: Operation,
  turnInstanceId = "turn-followup-1",
  operationId?: string,
): Record<string, unknown> {
  const operationIdentity = operationId === undefined
    ? {}
    : { operation_id: operationId };
  const operationFacts = operation === "ack"
    ? {
      operation,
      applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
      source: "quota_scheduler_ack",
    }
    : {
      operation,
      applied_rrule: "FREQ=MINUTELY;INTERVAL=3",
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
      failure_kind: "timeout",
      source: "quota_scheduler_host_update_failure",
    };
  return {
    schema_version: SCHEDULER_HEARTBEAT_FOLLOWUP_REQUEST_SCHEMA,
    runtime_root: runtimeRoot,
    turn_instance_id: turnInstanceId,
    require_heartbeat_receipt: true,
    before,
    use_current_hint: true,
    host_facts: {
      schema_version: "loopx_scheduler_heartbeat_host_facts_v0",
      ...scope,
      ...operationFacts,
      reset_token: "reset-followup",
      identity_signature: "identity-followup",
      progression_index: 0,
      progression_minutes: [15, 30, 60],
      expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
      cadence_class: "active_work",
      generated_at: "2026-08-27T06:30:00Z",
      execute: true,
      ack_needed: true,
      apply_needed: true,
      host_match_observed: true,
      ...operationIdentity,
    },
  };
}

function startPythonReceiptWriter({
  t,
  runtimeRoot,
  turnId,
}: {
  t: CleanupContext;
  runtimeRoot: string;
  turnId: string;
}) {
  const child = spawn(
    PYTHON,
    [
      "-c",
      PYTHON_RECEIPT_WRITER,
      runtimeRoot,
      scope.goal_id,
      scope.agent_id,
      turnId,
    ],
    {
      cwd: REPOSITORY_ROOT,
      env: { ...process.env, PYTHONPATH: REPOSITORY_ROOT },
      stdio: ["ignore", "pipe", "pipe"],
    },
  );
  t.after(() => {
    if (child.exitCode === null) child.kill();
  });
  let stderr = "";
  child.stderr.setEncoding("utf8").on("data", (chunk: string) => {
    stderr += chunk;
  });
  const lines = createInterface({ input: child.stdout, crlfDelay: Infinity });
  const iterator = lines[Symbol.asyncIterator]();
  const readMarker = async (expected: string): Promise<void> => {
    const line = await iterator.next();
    assert.equal(line.done, false, `missing Python marker ${expected}: ${stderr}`);
    assert.equal(line.value, expected);
  };
  const started = readMarker("started");
  const appended = started.then(() => readMarker("appended"));
  const done = once(child, "close").then(([code]) => {
    assert.equal(code, 0, `Python receipt writer failed: ${stderr}`);
  });
  return { started, appended, done };
}

test("receipt append cannot split freshness from ACK or host-failure persistence", async (t) => {
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      const runtimeRoot = await tempRuntime(t);
      await appendReceipt(runtimeRoot, "turn-followup-1");
      const statePath = schedulerStatePath(runtimeRoot, {
        goalId: scope.goal_id,
        agentId: scope.agent_id,
        surface: scope.surface,
        stateKey: scope.state_key,
      });
      const rename = fs.rename;
      let enteredRename!: () => void;
      let releaseRename!: () => void;
      const renameEntered = new Promise<void>((resolve) => {
        enteredRename = resolve;
      });
      const renameGate = new Promise<void>((resolve) => {
        releaseRename = resolve;
      });
      const hook = t.mock.method(
        fs,
        "rename",
        async (...args: Parameters<typeof rename>) => {
          if (String(args[1]) === statePath) {
            enteredRename();
            await renameGate;
          }
          await rename(...args);
        },
      );
      syncBuiltinESMExports();
      t.after(() => {
        hook.mock.restore();
        syncBuiltinESMExports();
      });

      const inFlight = evaluateSchedulerHeartbeatFollowup(
        followupRequest(runtimeRoot, operation),
      );
      await renameEntered;
      const writer = startPythonReceiptWriter({
        t,
        runtimeRoot,
        turnId: "turn-followup-2",
      });
      await writer.started;
      const appended = writer.appended.then(
        (): "appended" => "appended",
      );
      const blocked = delay(250).then(
        (): "blocked" => "blocked",
      );
      const appendDisposition = await Promise.race([
        appended,
        blocked,
      ]);
      releaseRename();
      const committed = await inFlight;
      await writer.done;

      assert.equal(
        appendDisposition,
        "blocked",
        "a newer receipt must not append inside the scheduler commit fence",
      );
      assert.equal(committed.ok, true);
      assert.equal(committed.scheduler_state_mutated, true);
      const persisted = JSON.parse(await readFile(statePath, "utf8"));
      assert.equal(persisted.heartbeat_commit.operation, operation);

      const stale = await evaluateSchedulerHeartbeatFollowup(
        followupRequest(runtimeRoot, operation),
      );
      assert.equal(
        stale.error_code,
        "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE",
      );
      assert.equal(stale.scheduler_state_mutated, false);

      const latest = await evaluateSchedulerHeartbeatFollowup(
        followupRequest(
          runtimeRoot,
          operation,
          "turn-followup-2",
          `latest-${operation}`,
        ),
      );
      assert.equal(latest.ok, true);
    });
  }
});
