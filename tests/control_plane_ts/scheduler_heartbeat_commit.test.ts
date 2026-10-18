import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import {
  evaluateSchedulerHeartbeatCommit,
  evaluateSchedulerHeartbeatHostFacts,
  schedulerHeartbeatCommitStateDigest,
  SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES,
  SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA,
} from "../../loopx/control_plane/scheduler/heartbeat_commit.ts";
import {
  normalizeSchedulerState,
  schedulerStatePath,
} from "../../loopx/control_plane/scheduler/state_store.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";

const scope = {
  goal_id: "goal-heartbeat",
  agent_id: "agent-heartbeat",
  surface: "codex_app",
  state_key: "scheduler_hint.app_automation.stateful_backoff",
};

function request(
  runtimeRoot: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    schema_version: SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA,
    operation: "ack",
    effect_id: "heartbeat-effect-1",
    runtime_root: runtimeRoot,
    ...scope,
    reset_token: "reset-1",
    identity_signature: "identity-1",
    progression_index: 0,
    progression_minutes: [15, 30, 60],
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    cadence_class: "active_work",
    stale_tolerance_minutes: SCHEDULER_ACK_STALE_HINT_TOLERANCE_MINUTES,
    generated_at: "2026-08-24T08:00:00Z",
    expected_state_digest: null,
    ...extra,
  };
}

async function tempRuntime(t: test.TestContext): Promise<string> {
  const runtimeRoot = await mkdtemp(join(tmpdir(), "loopx-heartbeat-commit-"));
  t.after(() => rm(runtimeRoot, { recursive: true, force: true }));
  return runtimeRoot;
}

function legacyStatePath(runtimeRoot: string): string {
  const stateHash = createHash("sha256")
    .update(scope.state_key, "utf8")
    .digest("hex")
    .slice(0, 16);
  return join(
    runtimeRoot,
    "goals",
    scope.goal_id,
    "scheduler-state",
    scope.agent_id,
    scope.surface,
    `${stateHash}.json`,
  );
}

test("exact ACK commits scheduler state and is replay-safe", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  assert.equal(first.status, "written");
  assert.equal(first.written, true);
  assert.equal(first.replayed, false);
  assert.equal(first.conflict, false);
  assert.equal(first.state?.last_applied_rrule, "FREQ=MINUTELY;INTERVAL=15");

  const replay = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  assert.equal(replay.status, "replayed");
  assert.equal(replay.written, false);
  assert.equal(replay.replayed, true);
  assert.equal(replay.state_digest, first.state_digest);
});

test("host-match ACK requires a matching observation before any state write", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  for (const observed of ["", "FREQ=MINUTELY;INTERVAL=3"]) {
    const refused = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
      host_match_observed: true,
      observed_host_rrule: observed,
    }));
    assert.equal(refused.status, "conflict");
    assert.equal(refused.reason_code, "rrule_mismatch");
    assert.equal(refused.written, false);
    await assert.rejects(readFile(refused.path, "utf8"), { code: "ENOENT" });
  }
  const accepted = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    host_match_observed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
  }));
  assert.equal(accepted.status, "written");
});
test("steady-state ACK is reduced to a typed skipped receipt", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const skipped = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "heartbeat-effect-skipped",
    ack_needed: false,
    apply_needed: false,
    expected_state_digest: first.state_digest,
  }));
  assert.equal(skipped.status, "skipped");
  assert.equal(skipped.already_applied, true);
  assert.equal(skipped.written, false);
  assert.equal(skipped.state_digest, first.state_digest);
});

test("effect identity cannot be reused for a different payload", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const changed = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    applied_rrule: "FREQ=MINUTELY;INTERVAL=30",
  }));
  assert.equal(changed.status, "conflict");
  assert.equal(changed.reason_code, "effect_id_conflict");
});

test("effect identity replays when a response-loss retry sees a different CAS precondition", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const changed = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    expected_state_digest: "sha256:stale-precondition",
  }));
  assert.equal(changed.status, "replayed");
  assert.equal(changed.replayed, true);
  assert.equal(changed.state_digest, first.state_digest);
});

test("host facts own state CAS and stable effect identity", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const hostFacts = {
    schema_version: "loopx_scheduler_heartbeat_host_facts_v0",
    operation: "ack",
    runtime_root: runtimeRoot,
    ...scope,
    reset_token: "reset-1",
    identity_signature: "identity-1",
    progression_index: 0,
    progression_minutes: [15, 30, 60],
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    cadence_class: "active_work",
    generated_at: "2026-08-24T08:00:00Z",
  };
  const first = await evaluateSchedulerHeartbeatHostFacts(hostFacts);
  assert.equal(first.status, "written");
  const replay = await evaluateSchedulerHeartbeatHostFacts({
    ...hostFacts,
    generated_at: "2026-08-24T08:01:00Z",
    execute: true,
    prior_host_update_failures: [{
      schema_version: "scheduler_host_update_failure_v0",
      target_rrule: "FREQ=MINUTELY;INTERVAL=30",
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
      failure_kind: "timeout",
      failure_count: 1,
      failed_at: "2026-08-24T07:59:00Z",
    }],
  });
  assert.equal(replay.status, "replayed");
  assert.equal(replay.effect_id, first.effect_id);
  assert.equal(replay.expected_state_digest, first.state_digest);
  assert.equal(replay.state_digest, first.state_digest);
});

test("host-failure facts advance after the caller observes the failure cache", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const initialFacts = {
    schema_version: "loopx_scheduler_heartbeat_host_facts_v0",
    operation: "host_failure",
    runtime_root: runtimeRoot,
    ...scope,
    reset_token: "reset-1",
    identity_signature: "identity-1",
    progression_index: 0,
    progression_minutes: [15, 30, 60],
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    cadence_class: "active_work",
    generated_at: "2026-08-24T08:00:00Z",
    apply_needed: true,
    failure_kind: "timeout",
  };
  const first = await evaluateSchedulerHeartbeatHostFacts(initialFacts);
  assert.equal(first.status, "written");
  assert.equal(first.failure_count, 1);
  assert.equal(first.state?.source, "quota_scheduler_host_update_failure");

  const retry = await evaluateSchedulerHeartbeatHostFacts({
    ...initialFacts,
    generated_at: "2026-08-24T08:00:30Z",
  });
  assert.equal(retry.status, "replayed");
  assert.equal(retry.effect_id, first.effect_id);

  const second = await evaluateSchedulerHeartbeatHostFacts({
    ...initialFacts,
    generated_at: "2026-08-24T08:01:00Z",
    prior_host_update_failures: first.state?.host_update_failures,
  });
  assert.equal(second.status, "written");
  assert.equal(second.failure_count, 2);
  assert.equal(
    (second.state?.host_update_failures as Array<Record<string, unknown>>)[0]
      .failure_count,
    2,
  );
});

test("CAS rejects a stale writer and leaves the newer state unchanged", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const update = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "heartbeat-effect-2",
    progression_index: 1,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=30",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=30",
    host_match_observed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=30",
    expected_state_digest: first.state_digest,
    generated_at: "2026-08-24T08:01:00Z",
  }));
  assert.equal(update.status, "written");
  assert.equal(update.state?.progression_index, 1);

  const stale = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "heartbeat-effect-stale",
    expected_state_digest: first.state_digest,
  }));
  assert.equal(stale.status, "conflict");
  assert.equal(stale.reason_code, "state_digest_conflict");
  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });
  const persisted = JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
  assert.equal(persisted.last_applied_rrule, "FREQ=MINUTELY;INTERVAL=30");
});

test("same-identity progression cannot skip cadence stages", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });
  const before = await readFile(path, "utf8");
  const skipped = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "heartbeat-effect-skip",
    progression_index: 2,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=60",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=60",
    expected_state_digest: first.state_digest,
    generated_at: "2026-08-24T08:01:00Z",
  }));
  assert.equal(skipped.status, "conflict");
  assert.equal(skipped.reason_code, "progression_skip_conflict");
  assert.equal(skipped.state_digest, first.state_digest);
  assert.equal(await readFile(path, "utf8"), before);
});

test("host failure increments a matching target/host pair", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "failure-1",
    apply_needed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    failure_kind: "timeout",
  }));
  assert.equal(first.status, "written");
  assert.equal(first.failure_count, 1);
  const second = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "failure-2",
    apply_needed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    failure_kind: "timeout",
    expected_state_digest: first.state_digest,
    generated_at: "2026-08-24T08:01:00Z",
  }));
  assert.equal(second.status, "written");
  assert.equal(second.failure_count, 2);
  assert.equal(
    (second.state?.host_update_failures as Array<Record<string, unknown>>)[0]
      .failure_count,
    2,
  );
});

test("host failure rejects missing apply-needed proof and retains TTL/cache rules", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const rejected = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "failure-missing-proof",
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
  }));
  assert.equal(rejected.status, "conflict");
  assert.equal(rejected.reason_code, "host_update_not_needed");

  const oldState = {
    schema_version: "loopx_scheduler_state_v0",
    ...scope,
    reset_token: "reset-1",
    identity_signature: "identity-1",
    progression_index: 0,
    progression_minutes: [15, 30, 60],
    last_applied_rrule: "FREQ=MINUTELY;INTERVAL=3",
    updated_at: "2026-08-20T08:00:00Z",
    host_update_failures: Array.from({ length: 5 }, (_, index) => ({
      schema_version: "scheduler_host_update_failure_v0",
      target_rrule: `FREQ=MINUTELY;INTERVAL=${index + 1}`,
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
      failure_kind: "timeout",
      failure_count: 1,
      failed_at: index === 4
        ? "2026-08-24T07:59:00Z"
        : "2026-08-20T08:00:00Z",
    })),
  };
  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });
  const { mkdir, writeFile } = await import("node:fs/promises");
  await mkdir(join(path, ".."), { recursive: true });
  await writeFile(path, `${JSON.stringify(oldState)}\n`, "utf8");
  const oldDigest = schedulerHeartbeatCommitStateDigest(
    normalizeSchedulerState(oldState, {
      goalId: scope.goal_id,
      agentId: scope.agent_id,
      surface: scope.surface,
      stateKey: scope.state_key,
    }),
  );
  const committed = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "failure-retention",
    apply_needed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    failure_kind: "timeout",
    expected_state_digest: oldDigest,
  }));
  assert.equal(committed.status, "written");
  assert.ok(
    (committed.state?.host_update_failures as unknown[]).length <= 4,
  );
  assert.equal(
    (committed.state?.host_update_failures as Array<Record<string, unknown>>)
      .some((failure) => failure.failed_at === "2026-08-20T08:00:00Z"),
    false,
  );
});

test("preview retains compact failure facts when no state is persisted", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "preview-failure-1",
    execute: false,
    apply_needed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    failure_kind: "timeout",
  }));
  const prior = first.state?.host_update_failures;
  assert.ok(Array.isArray(prior));
  const second = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "preview-failure-2",
    execute: false,
    reset_token: "reset-2",
    identity_signature: "identity-2",
    progression_index: 0,
    progression_minutes: [15, 16, 30],
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=3",
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    apply_needed: true,
    failure_kind: "timeout",
    prior_host_update_failures: prior,
  }));
  assert.equal(second.status, "preview");
  assert.equal((second.state?.host_update_failures as unknown[]).length, 1);
  assert.equal(
    (second.state?.host_update_failures as Array<Record<string, unknown>>)[0]
      .failure_count,
    2,
  );
});

test("missing state rejects nonzero initial progression in execute and preview", async (t) => {
  const executeRoot = await tempRuntime(t);
  const execute = await evaluateSchedulerHeartbeatCommit(request(executeRoot, {
    effect_id: "missing-initial-nonzero-execute",
    progression_index: 2,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=60",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=60",
  }));
  assert.equal(execute.status, "conflict");
  assert.equal(execute.reason_code, "initial_progression_index_conflict");
  assert.equal(execute.state, null);
  await assert.rejects(
    readFile(
      schedulerStatePath(executeRoot, {
        goalId: scope.goal_id,
        agentId: scope.agent_id,
        surface: scope.surface,
        stateKey: scope.state_key,
      }),
      "utf8",
    ),
    { code: "ENOENT" },
  );

  const previewRoot = await tempRuntime(t);
  const preview = await evaluateSchedulerHeartbeatCommit(request(previewRoot, {
    effect_id: "missing-initial-nonzero-preview",
    execute: false,
    progression_index: 1,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=30",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=30",
  }));
  assert.equal(preview.status, "conflict");
  assert.equal(preview.reason_code, "initial_progression_index_conflict");
  assert.equal(preview.state, null);
});

test("preview reads legacy state without migrating or deleting it", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const legacyPath = legacyStatePath(runtimeRoot);
  await mkdir(join(legacyPath, ".."), { recursive: true });
  const legacyState = {
    schema_version: "loopx_scheduler_state_v0",
    ...scope,
    reset_token: "legacy-reset",
    identity_signature: "legacy-identity",
    progression_index: 0,
    progression_minutes: [15, 30, 60],
    last_applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    updated_at: "2026-08-24T08:00:00Z",
  } as JsonObject;
  await writeFile(legacyPath, `${JSON.stringify(legacyState)}\n`, "utf8");
  const preview = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "legacy-preview",
    execute: false,
    reset_token: "legacy-reset",
    identity_signature: "legacy-identity",
    expected_state_digest: schedulerHeartbeatCommitStateDigest(legacyState),
  }));
  assert.equal(preview.status, "preview");
  assert.equal(preview.state?.last_applied_rrule, "FREQ=MINUTELY;INTERVAL=15");
  assert.equal(await readFile(legacyPath, "utf8"), `${JSON.stringify(legacyState)}\n`);
  await assert.rejects(
    readFile(schedulerStatePath(runtimeRoot, {
      goalId: scope.goal_id,
      agentId: scope.agent_id,
      surface: scope.surface,
      stateKey: scope.state_key,
    }), "utf8"),
    { code: "ENOENT" },
  );
});

test(
  "host-match ACK replaces a migrated stale identity at the initial cadence",
  async (t) => {
    const runtimeRoot = await tempRuntime(t);
    const legacyPath = legacyStatePath(runtimeRoot);
    await mkdir(join(legacyPath, ".."), { recursive: true });
    const legacyState = {
      schema_version: "loopx_scheduler_state_v0",
      ...scope,
      reset_token: "legacy-reset",
      identity_signature: "legacy-identity",
      progression_index: 0,
      progression_minutes: [15, 30, 60],
      last_applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
      updated_at: "2026-08-24T07:59:00Z",
    } satisfies JsonObject;
    await writeFile(legacyPath, `${JSON.stringify(legacyState)}\n`, "utf8");

    const committed = await evaluateSchedulerHeartbeatHostFacts({
      schema_version: "loopx_scheduler_heartbeat_host_facts_v0",
      operation: "ack",
      runtime_root: runtimeRoot,
      ...scope,
      reset_token: "reset-2",
      identity_signature: "identity-2",
      progression_index: 0,
      progression_minutes: [15, 30, 60],
      expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
      applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
      cadence_class: "active_work",
      ack_needed: true,
      apply_needed: false,
      host_match_observed: true,
      generated_at: "2026-08-24T08:00:00Z",
      observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
      execute: true,
    });

    assert.equal(committed.status, "written");
    assert.equal(
      committed.expected_state_digest,
      schedulerHeartbeatCommitStateDigest(legacyState),
    );
    assert.equal(committed.state?.reset_token, "reset-2");
    assert.equal(committed.state?.identity_signature, "identity-2");
    assert.equal(committed.state?.progression_index, 0);
    await assert.rejects(readFile(legacyPath, "utf8"), { code: "ENOENT" });
    const canonicalPath = schedulerStatePath(runtimeRoot, {
      goalId: scope.goal_id,
      agentId: scope.agent_id,
      surface: scope.surface,
      stateKey: scope.state_key,
    });
    const persisted = JSON.parse(
      await readFile(canonicalPath, "utf8"),
    ) as JsonObject;
    assert.equal(persisted.reset_token, "reset-2");
    assert.equal(persisted.identity_signature, "identity-2");
  },
);

test("identity reset starts a new progression without losing CAS protection", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const reset = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "identity-reset-failure",
    reset_token: "reset-2",
    identity_signature: "identity-2",
    progression_index: 0,
    progression_minutes: [15, 30, 60],
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
    apply_needed: true,
    failure_kind: "timeout",
    expected_state_digest: first.state_digest,
  }));
  assert.equal(reset.status, "written");
  assert.equal(reset.state?.reset_token, "reset-2");
  assert.equal(reset.state?.progression_index, 0);
});

test("exact host-match ACK binds a fresh reset identity", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const reset = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "identity-reset-host-match",
    reset_token: "reset-2",
    identity_signature: "identity-2",
    progression_index: 0,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    host_match_observed: true,
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
    expected_state_digest: first.state_digest,
  }));

  assert.equal(reset.status, "written");
  assert.equal(reset.state?.reset_token, "reset-2");
  assert.equal(reset.state?.identity_signature, "identity-2");
  assert.equal(reset.state?.progression_index, 0);
});

test("host-match host failure cannot reset scheduler identity", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const reset = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    operation: "host_failure",
    effect_id: "host-match-identity-reset-failure",
    reset_token: "reset-2",
    identity_signature: "identity-2",
    progression_index: 0,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=15",
    observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
    apply_needed: true,
    host_match_observed: true,
    failure_kind: "timeout",
    expected_state_digest: first.state_digest,
  }));
  assert.equal(reset.status, "conflict");
  assert.equal(reset.reason_code, "identity_conflict");
  assert.equal(reset.state_digest, first.state_digest);

  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });
  const persisted = JSON.parse(await readFile(path, "utf8")) as JsonObject;
  assert.equal(persisted.reset_token, "reset-1");
  assert.equal(persisted.identity_signature, "identity-1");
});

test("identity reset rejects nonzero progression and leaves state unchanged", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const reset = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "identity-reset-nonzero",
    reset_token: "reset-2",
    identity_signature: "identity-2",
    progression_index: 1,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=30",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=30",
    expected_state_digest: first.state_digest,
  }));
  assert.equal(reset.status, "conflict");
  assert.equal(reset.reason_code, "initial_progression_index_conflict");
  assert.equal(reset.state_digest, first.state_digest);
  const path = schedulerStatePath(runtimeRoot, {
    goalId: scope.goal_id,
    agentId: scope.agent_id,
    surface: scope.surface,
    stateKey: scope.state_key,
  });
  const persisted = JSON.parse(await readFile(path, "utf8")) as JsonObject;
  assert.equal(persisted.reset_token, "reset-1");
  assert.equal(persisted.identity_signature, "identity-1");
  assert.equal(persisted.progression_index, 0);
});

test("monitor stale ACK requires matching identity proof", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const initial = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "monitor-initial",
    cadence_class: "monitor_wait",
    progression_minutes: [15, 16, 30],
  }));
  assert.equal(initial.status, "written");
  const stale = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "monitor-stale",
    cadence_class: "monitor_wait",
    progression_minutes: [15, 16, 30],
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=16",
    expected_state_digest: initial.state_digest,
    generated_at: "2026-08-24T08:01:00Z",
  }));
  assert.equal(stale.status, "written");

  const wrongIdentity = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "monitor-wrong-identity",
    cadence_class: "monitor_wait",
    progression_minutes: [15, 16, 30],
    expected_rrule: "FREQ=MINUTELY;INTERVAL=15",
    applied_rrule: "FREQ=MINUTELY;INTERVAL=16",
    reset_token: "other-reset",
    expected_state_digest: stale.state_digest,
  }));
  assert.equal(wrongIdentity.status, "conflict");
  assert.equal(wrongIdentity.reason_code, "identity_conflict");
});

test("malformed and unsupported commit requests fail closed", async () => {
  await assert.rejects(
    evaluateSchedulerHeartbeatCommit({ schema_version: "unsupported" }),
    /request schema mismatch/,
  );
  await assert.rejects(
    evaluateSchedulerHeartbeatCommit({
      schema_version: SCHEDULER_HEARTBEAT_COMMIT_REQUEST_SCHEMA,
      operation: "unsupported",
    }),
    /unsupported/,
  );
  await assert.rejects(
    evaluateSchedulerHeartbeatCommit(request("/tmp/runtime", {
      progression_index: -1,
    })),
    /progression_index/,
  );
  await assert.rejects(
    evaluateSchedulerHeartbeatCommit(request("/tmp/runtime", {
      prior_host_update_failures: [{ failure_kind: "timeout" }],
    })),
    /prior_host_update_failures\[0\] is malformed/,
  );
});


test("a tighter progression requires a fresh identity and preserves CAS and replay", async (t) => {
  const runtimeRoot = await tempRuntime(t);
  const first = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot));
  const advanced = await evaluateSchedulerHeartbeatCommit(request(runtimeRoot, {
    effect_id: "backoff-30", progression_index: 1,
    expected_rrule: "FREQ=MINUTELY;INTERVAL=30", applied_rrule: "FREQ=MINUTELY;INTERVAL=30",
    expected_state_digest: first.state_digest,
  }));
  assert.equal(advanced.status, "written");
  const clamp = request(runtimeRoot, {
    effect_id: "deadline-15", progression_minutes: [15], progression_index: 0,
    host_match_observed: true, observed_host_rrule: "FREQ=MINUTELY;INTERVAL=15",
    expected_state_digest: advanced.state_digest,
  });
  const regression = await evaluateSchedulerHeartbeatCommit(clamp);
  assert.equal(regression.reason_code, "progression_regression");
  assert.equal(regression.state_digest, advanced.state_digest);

  const stale = await evaluateSchedulerHeartbeatCommit({
    ...clamp, reset_token: "deadline-bucket-reset", expected_state_digest: first.state_digest,
  });
  assert.equal(stale.reason_code, "state_digest_conflict");
  const resetRequest = { ...clamp, reset_token: "deadline-bucket-reset" };
  const reset = await evaluateSchedulerHeartbeatCommit(resetRequest);
  assert.equal(reset.status, "written");
  assert.equal(reset.state?.progression_index, 0);
  assert.deepEqual(reset.state?.progression_minutes, [15]);
  assert.equal(reset.state?.last_applied_rrule, "FREQ=MINUTELY;INTERVAL=15");
  const replay = await evaluateSchedulerHeartbeatCommit(resetRequest);
  assert.equal(replay.status, "replayed");
  assert.equal(replay.state_digest, reset.state_digest);
  const old = await evaluateSchedulerHeartbeatCommit({ ...clamp, effect_id: "late-old-policy" });
  assert.equal(old.reason_code, "state_digest_conflict");
  assert.equal(old.state_digest, reset.state_digest);
});
