import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { deflateSync } from "node:zlib";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { isSupportedNodeVersion } from "../../loopx/control_plane/runtime/node_version.ts";

type CleanupContext = {
  after(callback: () => void | Promise<void>): void;
};

const entrypoint = fileURLToPath(
  new URL(
    "../../loopx/control_plane/scheduler/heartbeat_followup_cli.ts",
    import.meta.url,
  ),
);
const goalId = "goal-followup-cli";
const agentId = "agent-followup-cli";
const turnId = "turn-followup-cli";

test("heartbeat follow-up CLI applies minimum Node prerelease precedence", () => {
  assert.equal(isSupportedNodeVersion("22.22.2"), false);
  assert.equal(isSupportedNodeVersion("22.22.3-rc.1"), false);
  assert.equal(isSupportedNodeVersion("22.22.3"), true);
  assert.equal(isSupportedNodeVersion("22.22.4-rc.1"), true);
  assert.equal(isSupportedNodeVersion("22.22.3+build.1"), true);
});

async function tempRuntime(t: CleanupContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-followup-cli-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
}

async function writeReceipt(runtimeRoot: string): Promise<void> {
  const path = join(runtimeRoot, "goals", goalId, "rollout-event-log.jsonl");
  await mkdir(dirname(path), { recursive: true });
  await writeFile(path, `${JSON.stringify({
    schema_version: "loopx_rollout_event_v0",
    event_kind: "quota_should_run",
    goal_id: goalId,
    agent_id: agentId,
    run_id: turnId,
  })}\n`, "utf8");
}

function hintPayload(): Record<string, unknown> {
  return {
    schema_version: "loopx_scheduler_host_followup_hint_v0",
    before: {
      should_run: true,
      normal_delivery_allowed: true,
      recovery_delivery_allowed: false,
      effective_action: "normal_run",
      state: "eligible",
    },
    use_current_hint: true,
    host_facts: {
      schema_version: "loopx_scheduler_heartbeat_host_facts_v0",
      operation: "ack",
      goal_id: goalId,
      agent_id: agentId,
      surface: "codex_app",
      state_key: "scheduler_hint.app_automation.stateful_backoff",
      reset_token: "reset-cli-followup",
      identity_signature: "identity-cli-followup",
      progression_index: 0,
      progression_minutes: [15, 30, 60],
      expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
      applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
      cadence_class: "active_work",
      generated_at: "2026-08-27T06:30:00Z",
      ack_needed: true,
      apply_needed: true,
      source: "quota_scheduler_ack",
      host_match_observed: true,
    },
  };
}

function chunks(value: unknown): string[] {
  const encoded = deflateSync(Buffer.from(JSON.stringify(value), "utf8"))
    .toString("base64")
    .replace(/=+$/, "");
  return encoded.match(/.{1,384}/g) ?? [];
}

function argv(runtimeRoot: string, format = "json"): string[] {
  const result = [
    "--format",
    format,
    "--runtime-root",
    runtimeRoot,
    "quota",
    "scheduler-ack-current",
    "--goal-id",
    goalId,
    "--agent-id",
    agentId,
    "-A",
    "--applied-rrule",
    "FREQ=MINUTELY;INTERVAL=15",
    "--host-match-observed",
  ];
  for (const chunk of chunks(hintPayload())) {
    result.push("--scheduler-host-facts-chunk", chunk);
  }
  result.push("--turn-instance-id", turnId, "--execute");
  return result;
}

function failureArgv(runtimeRoot: string, format = "json"): string[] {
  const payload = hintPayload();
  const facts = payload.host_facts as Record<string, unknown>;
  payload.use_current_hint = false;
  payload.host_facts = {
    ...facts,
    operation: "host_failure",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=3",
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    failure_kind: "timeout",
    source: "quota_scheduler_host_update_failure",
    host_match_observed: false,
  };
  const result = [
    "--format",
    format,
    "--runtime-root",
    runtimeRoot,
    "quota",
    "scheduler-fail-current",
    "--goal-id",
    goalId,
    "--agent-id",
    agentId,
    "--failed-rrule",
    "FREQ=MINUTELY;INTERVAL=15",
    "--codex-app-current-rrule",
    "FREQ=MINUTELY;INTERVAL=3",
    "--failure-kind",
    "timeout",
  ];
  for (const chunk of chunks(payload)) {
    result.push("--scheduler-host-facts-chunk", chunk);
  }
  result.push("--turn-instance-id", turnId, "--execute");
  return result;
}

function runCli(args: string[]) {
  const child = spawnSync(
    process.execPath,
    ["--no-warnings", "--experimental-strip-types", entrypoint, ...args],
    { encoding: "utf8" },
  );
  assert.equal(child.error, undefined, child.error?.message);
  return child;
}

function invalidFactsArgv(parts: string[]): string[] {
  const result = [
    "--format",
    "json",
    "--runtime-root",
    "/tmp/loopx-invalid-followup-facts",
    "quota",
    "scheduler-ack-current",
    "--goal-id",
    goalId,
    "--agent-id",
    agentId,
  ];
  for (const part of parts) {
    result.push("--scheduler-host-facts-chunk", part);
  }
  result.push("--turn-instance-id", turnId, "--execute");
  return result;
}

test("native host-followup CLI decodes bounded argv facts and writes JSON", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await writeReceipt(runtimeRoot);

  const child = runCli(argv(runtimeRoot));

  assert.equal(child.status, 0, child.stderr);
  const result = JSON.parse(child.stdout) as Record<string, unknown>;
  assert.equal(result.schema_version, "loopx_scheduler_host_followup_result_v0");
  assert.equal(result.ok, true);
  assert.equal(result.mode, "scheduler-ack");
  assert.equal(
    (result.scheduler_commit as Record<string, unknown>).status,
    "written",
  );
});

test("native host-followup CLI preserves the scheduler Markdown surface", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await writeReceipt(runtimeRoot);

  const child = runCli(argv(runtimeRoot, "markdown"));

  assert.equal(child.status, 0, child.stderr);
  assert.match(child.stdout, /^# LoopX Quota Scheduler Ack\n/);
  assert.match(child.stdout, /- goal_id: `goal-followup-cli`/);
  assert.match(child.stdout, /- should_run: `True`/);
});

test("native host-failure CLI preserves the legacy Markdown surface", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await writeReceipt(runtimeRoot);

  const child = runCli(failureArgv(runtimeRoot, "markdown"));

  assert.equal(child.status, 0, child.stderr);
  assert.match(child.stdout, /^# LoopX Quota Scheduler Host Update Failure\n/);
  assert.match(child.stdout, /- failed_rrule: `FREQ=MINUTELY;INTERVAL=15`/);
  assert.match(child.stdout, /- observed_host_rrule: `FREQ=MINUTELY;INTERVAL=3`/);
  assert.match(child.stdout, /- failure_kind: `timeout`/);
  assert.match(child.stdout, /- failure_count: `1`/);
});

test("native host-followup CLI returns the legacy receipt failure without writing", async (t) => {
  const runtimeRoot = await tempRuntime(t);

  const child = runCli(argv(runtimeRoot));

  assert.notEqual(child.status, 0);
  const result = JSON.parse(child.stdout) as Record<string, unknown>;
  assert.equal(result.status, "heartbeat_receipt_missing");
  assert.equal(
    result.error_code,
    "SCHEDULER_FOLLOWUP_HEARTBEAT_RECEIPT_MISSING",
  );
  assert.equal(result.scheduler_state_mutated, false);
});

test("native host-followup CLI rejects malformed compressed facts", () => {
  const child = runCli(invalidFactsArgv(["not-valid-compressed-json"]));

  assert.notEqual(child.status, 0);
  const result = JSON.parse(child.stdout) as Record<string, unknown>;
  assert.equal(result.status, "error");
  assert.equal(
    (result.error as Record<string, unknown>).code,
    "invalid_scheduler_host_facts",
  );
});

test("native host-followup transport rejects encoded facts beyond its bound", () => {
  const child = runCli(invalidFactsArgv(["a".repeat(4_097)]));

  assert.notEqual(child.status, 0);
  const result = JSON.parse(child.stdout) as Record<string, unknown>;
  assert.equal(
    (result.error as Record<string, unknown>).code,
    "invalid_scheduler_host_facts",
  );
});

test("native host-followup transport rejects inflated facts beyond its bound", () => {
  const encoded = deflateSync(
    Buffer.from(JSON.stringify({ padding: "x".repeat(16_385) }), "utf8"),
  ).toString("base64url");
  assert.ok(encoded.length < 4_096);

  const child = runCli(invalidFactsArgv([encoded]));

  assert.notEqual(child.status, 0);
  const result = JSON.parse(child.stdout) as Record<string, unknown>;
  assert.equal(
    (result.error as Record<string, unknown>).code,
    "invalid_scheduler_host_facts",
  );
});
