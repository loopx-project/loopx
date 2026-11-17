import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import fs, {
  appendFile,
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
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
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

const PYTHON_UNRELATED_APPEND = [
  "import sys",
  "from pathlib import Path",
  "from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path",
  "runtime_root, goal_id = sys.argv[1:]",
  "event = build_rollout_event(goal_id=goal_id, event_kind='todo_complete')",
  "append_rollout_event(rollout_event_log_path(Path(runtime_root), goal_id), event)",
  "print('appended', flush=True)",
].join("\n");

async function runPython(
  t: CleanupContext,
  script: string,
  args: readonly string[],
): Promise<string> {
  const child = spawn(PYTHON, ["-c", script, ...args], {
    cwd: REPOSITORY_ROOT,
    env: { ...process.env, PYTHONPATH: REPOSITORY_ROOT },
    stdio: ["ignore", "pipe", "pipe"],
  });
  t.after(() => {
    if (child.exitCode === null) child.kill();
  });
  let stdout = "";
  let stderr = "";
  child.stdout.setEncoding("utf8").on("data", (chunk: string) => {
    stdout += chunk;
  });
  child.stderr.setEncoding("utf8").on("data", (chunk: string) => {
    stderr += chunk;
  });
  await once(child, "close");
  assert.equal(child.exitCode, 0, `Python writer failed: ${stderr}`);
  return stdout;
}

/** One receipt row whose identity is intact and whose payload may be damaged. */
function receiptRow(turnId: string, summary = `receipt ${turnId}`): string {
  return JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    agent_id: scope.agent_id,
    run_id: turnId,
    summary,
  });
}

function receiptLogPath(runtimeRoot: string): string {
  return join(runtimeRoot, "goals", scope.goal_id, "rollout-event-log.jsonl");
}

test("a committed successor stays superseding after its early field is damaged", async (t) => {
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      const runtimeRoot = await tempRuntime(t);
      await appendReceipt(runtimeRoot, "turn-followup-1");
      const path = receiptLogPath(runtimeRoot);
      // The successor's `details` value meets an undecodable byte, so its
      // identity members come later in the serialized row than the damage.
      await appendFile(path, Buffer.concat([
        Buffer.from(
          `{"agent_id":"${scope.agent_id}","details":{"command":"quota",` +
          `"quota_command":"should-run","todo_id":"`,
        ),
        Buffer.from([0xff]),
        Buffer.from(
          `"},"event_id":"damaged","event_kind":"quota_should_run",` +
          `"goal_id":"${scope.goal_id}","recorded_at":"2026-08-27T06:30:00Z",` +
          `"run_id":"turn-followup-2","schema_version":"loopx_rollout_event_v0",` +
          `"status":"normal_run","summary":"damaged successor"}\n`,
        ),
      ]));
      const statePath = schedulerStatePath(runtimeRoot, {
        goalId: scope.goal_id,
        agentId: scope.agent_id,
        surface: scope.surface,
        stateKey: scope.state_key,
      });
      const before = await readFile(path);
      const result = await evaluateSchedulerHeartbeatFollowup(
        followupRequest(runtimeRoot, operation),
      );
      assert.equal(
        result.error_code,
        "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE",
      );
      assert.equal(result.scheduler_state_mutated, false);
      assert.deepEqual(await readFile(path), before);
      await assert.rejects(readFile(statePath, "utf8"));
    });
  }
});

/** A committed successor row shaped like the writer's sorted-key output. */
function receiptRowWithDetails(turnId: string, agentId = scope.agent_id): string {
  return JSON.stringify({
    agent_id: agentId,
    boundary: { absolute_paths_recorded: false, credential_values_recorded: false },
    details: {
      command: "quota",
      quota_command: "should-run",
      todo_id: "",
      turn_instance_id: turnId,
    },
    event_id: `event-${turnId}`,
    event_kind: "quota_should_run",
    goal_id: scope.goal_id,
    recorded_at: "2026-08-27T06:30:00Z",
    run_id: turnId,
    schema_version: "loopx_rollout_event_v0",
    status: "normal_run",
    summary: `receipt ${turnId}`,
  });
}

function damageLineAt(line: string, index: number): Buffer {
  const bytes = Buffer.from(line, "utf8");
  return Buffer.concat([
    bytes.subarray(0, index),
    Buffer.from([0xff]),
    bytes.subarray(index + 1),
  ]);
}

test("a damaged quote inside details cannot hide a committed successor", async (t) => {
  const cases: readonly {
    name: string;
    agentId?: string;
    stale: boolean;
    at: (line: string) => number;
  }[] = [
    {
      name: "damaged opening quote",
      stale: true,
      at: (line) => line.indexOf('"quota_command":"') + '"quota_command":'.length,
    },
    {
      name: "damaged closing quote",
      stale: true,
      at: (line) => {
        const opener = line.indexOf('"quota_command":"') + '"quota_command":'.length;
        return line.indexOf('"', opener + 1);
      },
    },
    {
      name: "damage inside the text",
      stale: true,
      at: (line) => line.indexOf('"should-run"') + 3,
    },
    {
      // A readable agent identity proves this row belongs to somebody else,
      // so its damaged display field must still be tolerated.
      name: "damaged opening quote in another Agent's row",
      agentId: "other-agent",
      stale: false,
      at: (line) => line.indexOf('"quota_command":"') + '"quota_command":'.length,
    },
  ];
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      for (const { name, agentId, stale, at } of cases) {
        const runtimeRoot = await tempRuntime(t);
        await appendReceipt(runtimeRoot, "turn-followup-1");
        const path = receiptLogPath(runtimeRoot);
        const line = receiptRowWithDetails("turn-followup-2", agentId);
        const index = at(line);
        assert.ok(index > 0, `${name}: fixture lost its damage point`);
        await appendFile(path, Buffer.concat([
          damageLineAt(line, index),
          Buffer.from("\n"),
        ]));
        const before = await readFile(path);
        const statePath = schedulerStatePath(runtimeRoot, {
          goalId: scope.goal_id,
          agentId: scope.agent_id,
          surface: scope.surface,
          stateKey: scope.state_key,
        });
        const result = await evaluateSchedulerHeartbeatFollowup(
          followupRequest(runtimeRoot, operation),
        );
        if (stale) {
          assert.equal(
            result.error_code,
            "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE",
            `${name} let ${operation} write stale state`,
          );
          assert.equal(result.scheduler_state_mutated, false, name);
          assert.deepEqual(await readFile(path), before, name);
          await assert.rejects(readFile(statePath, "utf8"), name);
        } else {
          assert.equal(result.ok, true, name);
          assert.equal(result.scheduler_state_mutated, true, name);
          assert.deepEqual(await readFile(path), before, name);
          const persisted = JSON.parse(await readFile(statePath, "utf8"));
          assert.equal(persisted.heartbeat_commit.operation, operation, name);
        }
      }
    });
  }
});

test("only an ambiguous receipt of this scope fails closed", async (t) => {
  const rows: readonly {
    row: string;
    damaged: boolean;
    stale: boolean;
    why: string;
  }[] = [
    {
      // Two different Turn ids cannot both name what the row admitted.
      row:
        `{"agent_id":"${scope.agent_id}","event_kind":"quota_should_run",` +
        `"goal_id":"${scope.goal_id}","run_id":"turn-followup-2",` +
        `"schema_version":"loopx_rollout_event_v0","run_id":"turn-followup-3"}`,
      damaged: false,
      stale: true,
      why: "same-scope receipt with two Turn ids",
    },
    {
      // A readable Turn id beside an unreadable one is still ambiguous.
      row:
        `{"agent_id":"${scope.agent_id}","event_kind":"quota_should_run",` +
        `"goal_id":"${scope.goal_id}","run_id":"turn-followup-2",` +
        `"schema_version":"loopx_rollout_event_v0","run_id":"turn-`,
      damaged: true,
      stale: true,
      why: "same-scope receipt with a damaged second Turn id",
    },
    {
      // A duplicated Turn in another Agent's row proves nothing here.
      row:
        `{"agent_id":"other-agent","event_kind":"quota_should_run",` +
        `"goal_id":"${scope.goal_id}","run_id":"turn-followup-2",` +
        `"schema_version":"loopx_rollout_event_v0","run_id":"turn-followup-3"}`,
      damaged: false,
      stale: false,
      why: "another Agent's duplicated Turn",
    },
    {
      // A validation diagnostic is not a heartbeat receipt of this scope.
      row:
        `{"agent_id":"${scope.agent_id}","event_kind":"validation",` +
        `"goal_id":"${scope.goal_id}","run_id":"turn-followup-2",` +
        `"schema_version":"loopx_rollout_event_v0","run_id":"turn-followup-3"}`,
      damaged: false,
      stale: false,
      why: "a validation row with a duplicated Turn",
    },
  ];
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      for (const { row, damaged, stale, why } of rows) {
        const runtimeRoot = await tempRuntime(t);
        await appendReceipt(runtimeRoot, "turn-followup-1");
        const path = receiptLogPath(runtimeRoot);
        await appendFile(path, Buffer.concat([
          Buffer.from(row),
          ...(damaged ? [Buffer.from([0xff])] : []),
          Buffer.from('"}\n'),
        ]));
        const before = await readFile(path);
        const statePath = schedulerStatePath(runtimeRoot, {
          goalId: scope.goal_id,
          agentId: scope.agent_id,
          surface: scope.surface,
          stateKey: scope.state_key,
        });
        const result = await evaluateSchedulerHeartbeatFollowup(
          followupRequest(runtimeRoot, operation),
        );
        if (stale) {
          assert.equal(
            result.error_code,
            "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_STALE",
            why,
          );
          assert.equal(result.scheduler_state_mutated, false, why);
          assert.deepEqual(await readFile(path), before, why);
          await assert.rejects(readFile(statePath, "utf8"), why);
        } else {
          assert.equal(result.ok, true, why);
          assert.equal(result.scheduler_state_mutated, true, why);
          assert.deepEqual(await readFile(path), before, why);
          const persisted = JSON.parse(await readFile(statePath, "utf8"));
          assert.equal(persisted.heartbeat_commit.operation, operation, why);
        }
      }
    });
  }
});

test("a damaged audit row keeps its nested diagnostic Turn out of scope", async (t) => {
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      const runtimeRoot = await tempRuntime(t);
      await appendReceipt(runtimeRoot, "turn-followup-1");
      const path = receiptLogPath(runtimeRoot);
      // The audit row is a quota receipt without a top-level Turn.  A damaged
      // display field before `details` must not let recovery read the nested
      // diagnostic `run_id` as a committed successor.
      await appendFile(path, Buffer.concat([
        Buffer.from(
          `{"agent_id":"${scope.agent_id}","note":"`,
        ),
        Buffer.from([0xff]),
        Buffer.from(
          `","details":{"run_id":"turn-diagnostic-nested"},` +
          `"event_kind":"quota_should_run","goal_id":"${scope.goal_id}",` +
          `"status":"normal_run","schema_version":"loopx_rollout_event_v0"}\n`,
        ),
      ]));
      const before = await readFile(path);
      const result = await evaluateSchedulerHeartbeatFollowup(
        followupRequest(runtimeRoot, operation),
      );
      assert.equal(result.ok, true);
      assert.equal(result.scheduler_state_mutated, true);
      assert.deepEqual(await readFile(path), before);
      const statePath = schedulerStatePath(runtimeRoot, {
        goalId: scope.goal_id,
        agentId: scope.agent_id,
        surface: scope.surface,
        stateKey: scope.state_key,
      });
      const persisted = JSON.parse(await readFile(statePath, "utf8"));
      assert.equal(persisted.heartbeat_commit.operation, operation);
    });
  }
});

test("a torn successor stays uncommitted after its append is repaired", async (t) => {
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      const runtimeRoot = await tempRuntime(t);
      await appendReceipt(runtimeRoot, "turn-followup-1");
      const path = receiptLogPath(runtimeRoot);
      // A writer that never finished the row leaves a torn prefix; the next
      // append drops it before writing its own row.
      await appendFile(path, Buffer.from(
        `{"agent_id":"${scope.agent_id}","details":{"turn_instance_id":` +
        `"turn-followup-2"},"event_kind":"quota_should_run",` +
        `"goal_id":"${scope.goal_id}","run_id":"turn-followup-2",` +
        `"schema_version":"loopx_rollout_event_v0"`,
      ));
      await runPython(t, PYTHON_UNRELATED_APPEND, [runtimeRoot, scope.goal_id]);
      const repaired = await readFile(path);
      assert.ok(!repaired.includes("turn-followup-2"), inspected(repaired));

      const result = await evaluateSchedulerHeartbeatFollowup(
        followupRequest(runtimeRoot, operation),
      );
      assert.equal(result.ok, true);
      assert.equal(result.scheduler_state_mutated, true);
      const statePath = schedulerStatePath(runtimeRoot, {
        goalId: scope.goal_id,
        agentId: scope.agent_id,
        surface: scope.surface,
        stateKey: scope.state_key,
      });
      const persisted = JSON.parse(await readFile(statePath, "utf8"));
      assert.equal(persisted.heartbeat_commit.operation, operation);
    });
  }
});

function inspected(bytes: Buffer): string {
  return bytes.toString("hex");
}

test("a completed row missing only its newline stays uncommitted after repair", async (t) => {
  for (const operation of OPERATIONS) {
    await t.test(operation, { timeout: 30_000 }, async (t) => {
      const runtimeRoot = await tempRuntime(t);
      await appendReceipt(runtimeRoot, "turn-followup-1");
      const path = receiptLogPath(runtimeRoot);
      // The row's own bytes are complete JSON; only its terminating newline is
      // missing, so the append owner drops the uncommitted prefix.
      await appendFile(path, Buffer.from(receiptRow("turn-followup-2")));
      await runPython(t, PYTHON_UNRELATED_APPEND, [runtimeRoot, scope.goal_id]);
      const repaired = await readFile(path);
      assert.ok(!repaired.includes("turn-followup-2"), inspected(repaired));

      const result = await evaluateSchedulerHeartbeatFollowup(
        followupRequest(runtimeRoot, operation),
      );
      assert.equal(result.ok, true);
      assert.equal(result.scheduler_state_mutated, true);
      const statePath = schedulerStatePath(runtimeRoot, {
        goalId: scope.goal_id,
        agentId: scope.agent_id,
        surface: scope.surface,
        stateKey: scope.state_key,
      });
      const persisted = JSON.parse(await readFile(statePath, "utf8"));
      assert.equal(persisted.heartbeat_commit.operation, operation);
    });
  }
});

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
