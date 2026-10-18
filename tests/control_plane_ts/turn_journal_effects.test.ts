import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";

import { EffectRuntimeConflictError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  acquireFileMutationLock,
  releaseFileMutationLock,
} from "../../loopx/control_plane/effect_runtime_io.ts";
import { commitTurnJournal } from "../../loopx/control_plane/turn_driver/turn_journal_effects.ts";
import { interpretTurnJournal } from "../../loopx/control_plane/turn_driver/turn_journal.ts";

const turnKey = `sha256:${"a".repeat(64)}`;
const todoId = "todo_fixture0001";
const instanceA = "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const instanceB = "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
const phases = [
  "host_execute",
  "typed_result",
  "validation",
  "durable_writeback",
  "quota_spend",
  "scheduler_apply",
  "scheduler_ack",
] as const;

function effectId(agentId = "fixture-agent", key = turnKey): string {
  return `fixture-goal:${agentId}:${todoId}:${key}`;
}

function journal(
  status = "in_progress",
  completedPhases: readonly string[] = [],
  agentId = "fixture-agent",
  key = turnKey,
): Record<string, unknown> {
  return {
    schema_version: "loopx_turn_journal_v0",
    goal_id: "fixture-goal",
    turn_key: key,
    status,
    completed_phases: [...completedPhases],
    plan: {
      turn_envelope: {
        goal_id: "fixture-goal",
        agent_id: agentId,
        action: { selected_todo: { todo_id: todoId } },
      },
      transaction: {
        turn_key: key,
        settlement_plan: {
          schema_version: "quota_settlement_plan_v1",
          identity: {
            schema_version: "quota_settlement_identity_v0",
            effect_id: effectId(agentId, key),
            goal_id: "fixture-goal",
            agent_id: agentId,
            todo_id: todoId,
            turn_instance_id: key,
          },
        },
      },
    },
  };
}

function sourceJournal(
  goalInstanceId: string,
  key: string,
  completedPhases: readonly string[] = [],
): Record<string, unknown> {
  const snapshot = journal("in_progress", completedPhases, "fixture-agent", key);
  const plan = snapshot.plan as Record<string, unknown>;
  const goalRef = {
    goal_id: "fixture-goal",
    goal_instance_id: goalInstanceId,
  };
  plan.goal_ref = goalRef;
  (plan.transaction as Record<string, unknown>).goal_ref = goalRef;
  return snapshot;
}

function sourceGuardPath(registryPath: string, goalId: string): string {
  const alias = createHash("sha256").update(goalId, "utf8").digest("hex");
  return join(
    dirname(registryPath),
    ".loopx",
    "lifecycle",
    "goal-instance",
    "guards",
    `${alias}.guard`,
  );
}

async function commitSource(
  path: string,
  snapshot: Record<string, unknown>,
  plannedInstanceId: string,
  currentInstanceId = plannedInstanceId,
) {
  const registryPath = join(dirname(path), "project", ".loopx", "registry.json");
  const target = sourceGuardPath(registryPath, "fixture-goal");
  const lock = await acquireFileMutationLock(target);
  try {
    return await commitTurnJournal({
      path,
      journal: snapshot,
      expected_effect_id: effectId(
        "fixture-agent",
        String(snapshot.turn_key),
      ),
      source_admission: {
        schema_version: "loopx_turn_journal_source_admission_v0",
        profile_id: "source_session_v1",
        registry_path: registryPath,
        planned_goal_ref: {
          goal_id: "fixture-goal",
          goal_instance_id: plannedInstanceId,
        },
        authority: {
          kind: "present",
          goal_ref: {
            goal_id: "fixture-goal",
            goal_instance_id: currentInstanceId,
          },
        },
        lock: {
          target,
          pid: process.pid,
          token: lock.token,
        },
      },
    });
  } finally {
    await releaseFileMutationLock(target, lock.token, null, true);
  }
}

async function withJournalPath(
  run: (path: string) => Promise<void>,
): Promise<void> {
  const directory = await mkdtemp(join(tmpdir(), "loopx-ts-journal-"));
  try {
    await run(join(directory, "turn.json"));
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
}

async function commit(path: string, snapshot: Record<string, unknown>) {
  return await commitTurnJournal({
    path,
    journal: snapshot,
    expected_effect_id: effectId(),
  });
}

test("journal checkpoint retry is idempotent and operation-scoped", async () => {
  await withJournalPath(async (path) => {
    const snapshot = journal();
    const first = await commit(path, snapshot);
    const replay = await commit(path, snapshot);

    assert.equal(first.appended, true);
    assert.equal(first.replayed, false);
    assert.equal(replay.appended, false);
    assert.equal(replay.replayed, true);
    assert.equal(first.operation_id, replay.operation_id);
  });
});

test("contradictory settlement binding cannot create or replace a durable journal", async () => {
  await withJournalPath(async (path) => {
    const valid = journal();
    const invalid = structuredClone(valid);
    const plan = invalid.plan as Record<string, unknown>;
    const transaction = plan.transaction as Record<string, unknown>;
    const settlement = transaction.settlement_plan as Record<string, unknown>;
    Object.assign(settlement.identity as object, {
      binding_kind: "autonomous_replan", binding_id: "other-work",
    });

    await assert.rejects(commit(path, invalid), /settlement|identity/i);
    await assert.rejects(readFile(path), { code: "ENOENT" });
    await commit(path, valid);
    const before = await readFile(path, "utf8");
    await assert.rejects(commit(path, invalid), /settlement|identity/i);
    assert.equal(await readFile(path, "utf8"), before);
    assert.equal((await commit(path, valid)).replayed, true);
  });
});

test("exact GoalRef journals require source admission", async () => {
  await withJournalPath(async (path) => {
    const snapshot = sourceJournal(instanceA, turnKey);
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
      }),
      /source admission/,
    );
  });
});

test("source admission must be well formed and hold a live guard", async () => {
  await withJournalPath(async (path) => {
    const snapshot = sourceJournal(instanceA, turnKey);
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
        source_admission: {},
      }),
      /source admission is malformed/,
    );

    const registryPath = join(
      dirname(path),
      "project",
      ".loopx",
      "registry.json",
    );
    const target = sourceGuardPath(registryPath, "fixture-goal");
    const lock = await acquireFileMutationLock(target);
    await releaseFileMutationLock(target, lock.token);
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
        source_admission: {
          schema_version: "loopx_turn_journal_source_admission_v0",
          profile_id: "source_session_v1",
          registry_path: registryPath,
          planned_goal_ref: {
            goal_id: "fixture-goal",
            goal_instance_id: instanceA,
          },
          authority: {
            kind: "present",
            goal_ref: {
              goal_id: "fixture-goal",
              goal_instance_id: instanceA,
            },
          },
          lock: {
            target,
            pid: process.pid,
            token: lock.token,
          },
        },
      }),
      (error: unknown) => {
        assert.ok(error instanceof EffectRuntimeConflictError);
        assert.equal(error.code, "journal_source_admission_expired");
        return true;
      },
    );
    await assert.rejects(readFile(path), { code: "ENOENT" });
  });
});

test("source admission rejects stale Goal A without mutating A or B", async () => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-ts-source-journal-"));
  const aKey = `sha256:${"a".repeat(64)}`;
  const bKey = `sha256:${"b".repeat(64)}`;
  const aPath = join(
    directory,
    "runtime",
    "goals",
    "fixture-goal",
    "turns",
    `${aKey.slice("sha256:".length)}.json`,
  );
  const bPath = join(
    directory,
    "runtime",
    "goals",
    "fixture-goal",
    "turns",
    `${bKey.slice("sha256:".length)}.json`,
  );
  try {
    const initialA = sourceJournal(instanceA, aKey);
    const first = await commitSource(aPath, initialA, instanceA);
    const replay = await commitSource(aPath, initialA, instanceA);
    assert.equal(first.appended, true);
    assert.equal(replay.replayed, true);

    const beforeA = await readFile(aPath, "utf8");
    await assert.rejects(
      commitSource(
        aPath,
        sourceJournal(instanceA, aKey, phases.slice(0, 2)),
        instanceA,
        instanceB,
      ),
      (error: unknown) => {
        assert.ok(error instanceof EffectRuntimeConflictError);
        assert.equal(error.code, "stale_goal_instance");
        return true;
      },
    );
    assert.equal(await readFile(aPath, "utf8"), beforeA);

    const currentB = sourceJournal(instanceB, bKey);
    const committedB = await commitSource(bPath, currentB, instanceB);
    assert.equal(committedB.appended, true);
    assert.deepEqual(JSON.parse(await readFile(bPath, "utf8")), currentB);
    assert.equal(await readFile(aPath, "utf8"), beforeA);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("source journal plan GoalRef copies must agree", async () => {
  await withJournalPath(async (path) => {
    const snapshot = sourceJournal(instanceA, turnKey);
    const plan = snapshot.plan as Record<string, unknown>;
    (plan.transaction as Record<string, unknown>).goal_ref = {
      goal_id: "fixture-goal",
      goal_instance_id: instanceB,
    };
    const inspection = interpretTurnJournal({
      schema_version: "loopx_turn_journal_interpretation_request_v0",
      journal: snapshot,
      goal_id: "fixture-goal",
      agent_id: "fixture-agent",
      turn_key: turnKey,
    });
    assert.ok(
      inspection.violations.includes("goal_ref_binding_mismatch"),
    );
    assert.equal(inspection.journal_consistent, false);
    await assert.rejects(
      commitSource(path, snapshot, instanceA),
      /GoalRef/,
    );
  });
});

test("legacy journals reject source admission without changing their wire", async () => {
  await withJournalPath(async (path) => {
    const snapshot = journal();
    await assert.rejects(
      commitTurnJournal({
        path,
        journal: snapshot,
        expected_effect_id: effectId(),
        source_admission: {},
      }),
      /Legacy Turn journals cannot carry source admission/,
    );
    const committed = await commit(path, snapshot);
    assert.equal(committed.appended, true);
    assert.deepEqual(JSON.parse(await readFile(path, "utf8")), snapshot);
  });
});

test("TS journal owner accepts the complete monotonic transaction", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));

    const preparedWriteback = journal("in_progress", phases.slice(0, 3));
    preparedWriteback.effect_attempts = {
      durable_writeback: {
        status: "prepared",
        effect_ref: `${effectId()}#durable_writeback`,
      },
    };
    await commit(path, preparedWriteback);
    await commit(path, journal("in_progress", phases.slice(0, 4)));

    const preparedSpend = journal("in_progress", phases.slice(0, 4));
    preparedSpend.effect_attempts = {
      quota_spend: {
        status: "prepared",
        effect_ref: `${effectId()}#quota_spend`,
      },
    };
    await commit(path, preparedSpend);
    await commit(path, journal("in_progress", phases.slice(0, 5)));
    await commit(path, journal("scheduler_action_required", phases.slice(0, 5)));
    await commit(path, journal("committed", phases));

    assert.equal(JSON.parse(await readFile(path, "utf8")).status, "committed");
  });
});

test("new journals cannot appear after side effects", async () => {
  await withJournalPath(async (path) => {
    await assert.rejects(
      commit(path, journal("in_progress", phases.slice(0, 3))),
      /must begin in progress with no completed phases/,
    );
  });
});

test("completed phases cannot regress", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));
    await assert.rejects(
      commit(path, journal("in_progress", phases.slice(0, 2))),
      /completed phases cannot regress or fork/,
    );
  });
});

test("completed phases cannot skip transaction checkpoints", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await assert.rejects(
      commit(path, journal("committed", phases)),
      /cannot skip transaction checkpoints/,
    );
  });
});

test("failed validation may explicitly rewind for host reinvocation", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    const failed = journal("failed", phases.slice(0, 2));
    failed.receipt = { turn_key: turnKey, failed_phase: "validation" };
    await commit(path, failed);
    await commit(path, journal("in_progress", []));

    assert.deepEqual(JSON.parse(await readFile(path, "utf8")).completed_phases, []);
  });
});

test("terminal journal tombstones are immutable", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));
    await commit(path, journal("in_progress", phases.slice(0, 4)));
    await commit(path, journal("in_progress", phases.slice(0, 5)));
    await commit(path, journal("committed", phases));
    const changed = journal("committed", phases);
    changed.reason = "late mutation";
    await assert.rejects(commit(path, changed), /tombstones are immutable/);
  });
});

test("transaction plans are immutable within one effect", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    const changed = journal("in_progress", phases.slice(0, 2));
    const plan = changed.plan as Record<string, unknown>;
    plan.host = { kind: "changed-after-start" };
    await assert.rejects(commit(path, changed), /transaction plan is immutable/);
  });
});

test("cross-effect overwrite remains fail-closed", async () => {
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await assert.rejects(
      commitTurnJournal({ path, journal: journal("in_progress", [], "other-agent") }),
      /belongs to another settlement effect/,
    );
  });
});

test("prepared effects must use the settlement identity", async () => {
  await withJournalPath(async (path) => {
    const invalid = journal();
    invalid.effect_attempts = {
      durable_writeback: {
        status: "prepared",
        effect_ref: "another-effect#durable_writeback",
      },
    };
    await assert.rejects(
      commit(path, invalid),
      /prepared effect does not match settlement identity/,
    );
  });
});

test("inspector and writer share prepared-intent rejection and valid controls", async () => {
  const prepared = {status: "prepared", effect_ref: `${effectId()}#durable_writeback`};
  const cases = [
    {attempts: {unknown_provider_step: prepared}, code: "prepared_effect_step_unsupported"},
    {attempts: [], code: "prepared_effect_attempts_invalid"},
    {attempts: null, code: "prepared_effect_attempts_invalid"},
    {attempts: {}, code: "prepared_effect_count_invalid"},
    {attempts: {durable_writeback: prepared, quota_spend: prepared}, code: "prepared_effect_count_invalid"},
    {attempts: {durable_writeback: null}, code: "prepared_effect_identity_invalid"},
    {attempts: {durable_writeback: {...prepared, status: "committed"}}, code: "prepared_effect_identity_invalid"},
    {attempts: {durable_writeback: {...prepared, effect_ref: "foreign#durable_writeback"}}, code: "prepared_effect_identity_invalid"},
    {attempts: {quota_spend: {status: "prepared", effect_ref: `${effectId()}#quota_spend`}}, code: "prepared_effect_phase_invalid"},
  ];
  await withJournalPath(async (path) => {
    await commit(path, journal());
    await commit(path, journal("in_progress", phases.slice(0, 2)));
    await commit(path, journal("in_progress", phases.slice(0, 3)));
    const inspect = (snapshot: Record<string, unknown>) => interpretTurnJournal({
      schema_version: "loopx_turn_journal_interpretation_request_v0",
      journal: snapshot, goal_id: "fixture-goal", agent_id: "fixture-agent", turn_key: turnKey,
    });
    const before = await readFile(path, "utf8");
    for (const {attempts, code} of cases) {
      const invalid = {...journal("in_progress", phases.slice(0, 3)), effect_attempts: attempts};
      const result = inspect(invalid);
      assert.ok(result.violations.includes(code), code);
      assert.equal(result.journal_consistent, false, code);
      assert.equal(result.recovery_decision.can_continue, false, code);
      assert.equal(result.recovery_decision.reinvoke_host, false, code);
      assert.equal(result.recorded_effects.host_invoked, true, code);
      assert.equal(result.recorded_effects.state_written, null, code);
      assert.equal(result.recorded_effects.quota_spent, null, code);
      assert.deepEqual(result.effects, []);
      await assert.rejects(commit(path, invalid), /Turn journal/, code);
      assert.equal(await readFile(path, "utf8"), before, code);
    }
    assert.equal(inspect(journal("in_progress", phases.slice(0, 3))).journal_consistent, true);
    const valid = {...journal("in_progress", phases.slice(0, 3)), effect_attempts: {durable_writeback: prepared}};
    assert.equal(inspect(valid).recovery_decision.reason, "resolve_prepared_effect");
    await commit(path, valid);
    const validCloseout = {...journal("in_progress", phases.slice(0, 5)), effect_attempts: {
      terminal_closeout: {status: "prepared", effect_ref: `${effectId()}#terminal_closeout`},
    }};
    assert.equal(inspect(validCloseout).journal_consistent, true);
    const invalidTerminalStatus = {...validCloseout, status: "committed"};
    assert.ok(inspect(invalidTerminalStatus).violations.includes("prepared_effect_status_invalid"));
    await assert.rejects(commit(path, invalidTerminalStatus), /terminal state cannot retain/);
    const terminalIntent = {...journal("committed", phases), effect_attempts: {
      terminal_closeout: {status: "prepared", effect_ref: `${effectId()}#terminal_closeout`},
    }};
    assert.equal(inspect(terminalIntent).journal_consistent, false);
    await assert.rejects(commit(path, terminalIntent), /prepared effect/);
    assert.equal(inspect(journal("committed", phases)).recovery_decision.action, "return_existing");
  });
});

test("failed snapshots must name the next uncompleted phase", async () => {
  await withJournalPath(async (path) => {
    const invalid = journal("failed", phases.slice(0, 4));
    invalid.receipt = { turn_key: turnKey, failed_phase: "validation" };
    await assert.rejects(
      commit(path, invalid),
      /must name the next uncompleted phase/,
    );
  });
});
