import assert from "node:assert/strict";
import test from "node:test";

import { EffectRuntimeRequestError } from "../../loopx/control_plane/effect_runtime_errors.ts";
import {
  TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
  TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
  TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
  evaluateTodoResumeConditions,
  diagnoseTodoResumeCondition,
  normalizeTodoResumeWhen,
  planTodoExternalWaitTransition,
} from "../../loopx/control_plane/todos/resume_condition.ts";

function todo(
  todoId: string,
  status: string,
  taskClass = "advancement_task",
  extra: Record<string, unknown> = {},
) {
  return {
    todo_id: todoId,
    role: "agent",
    status,
    task_class: taskClass,
    ...extra,
  };
}

test("resume syntax is normalized by the typed Todo boundary", () => {
  assert.equal(normalizeTodoResumeWhen({
    schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
    resume_when: " Monitor_Changed:TODO_WATCH001 ",
  }), "monitor_changed:todo_watch001");
  assert.equal(normalizeTodoResumeWhen({
    schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
    resume_when: "note_contains:approved",
  }), null);
  assert.equal(normalizeTodoResumeWhen({
    schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
    resume_when: "resume_at:2026-09-14T09:30:00+08:00",
  }), "resume_at:2026-09-14T01:30:00Z");
  for (const resume_when of [
    "resume_at:2026-09-14T09:30:00",
    "resume_at:2026-02-30T09:30:00+08:00",
    "resume_at:2026-09-14T09:30:00+08:60",
    "resume_at:2026-09-14T09:30:00+15:00",
    "resume_at:1000-01-01T00:00:00+14:00",
    "resume_at:9999-12-31T23:59:59-14:00",
  ]) assert.equal(normalizeTodoResumeWhen({
    schema_version: TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
    resume_when,
  }), null, resume_when);
});

test("resume_at emits one stable due receipt across repeated ticks and restart", () => {
  const item = todo("todo_scheduled", "deferred", "advancement_task", {
    resume_when: "resume_at:2026-09-14T09:30:00+08:00",
  });
  const evaluate = (evaluated_at: string) => {
    const result = evaluateTodoResumeConditions({
      schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
      items: [item],
      source_items: [],
      rollout_events: [],
      evaluated_at,
    });
    return (result.conditions as Array<{ condition: Record<string, unknown> }>)[0].condition;
  };

  const pending = evaluate("2026-09-14T01:29:59Z");
  assert.equal(pending.satisfied, false);
  assert.equal(pending.material_change_generation, 0);
  assert.equal(pending.resume_receipt, null);

  const firstDue = evaluate("2026-09-14T01:30:00Z");
  const replayed = evaluate("2026-09-15T00:00:00Z");
  assert.equal(firstDue.satisfied, true);
  assert.equal(firstDue.material_change_generation, 1);
  assert.equal(firstDue.availability_reason, "resume_condition_satisfied");
  assert.deepEqual(firstDue.resume_receipt, replayed.resume_receipt);
  assert.match(
    String((firstDue.resume_receipt as Record<string, unknown>).receipt_id),
    /^resume_at_[a-f0-9]{24}$/,
  );
  assert.throws(() => evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [item],
    source_items: [],
    rollout_events: [],
  }), /evaluated_at must be a timezone-aware RFC3339 timestamp/);
});

test("live monitor completion is invalid, but historical completion remains satisfied", () => {
  for (const status of ["open", "blocked", "deferred", "done"]) {
    const result = evaluateTodoResumeConditions({
      schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
      items: [todo("todo_waiting", "open", "advancement_task", { resume_when: "todo_done:todo_monitor" })],
      source_items: [todo("todo_monitor", status, "continuous_monitor", { archive_state: "archived" })],
    });
    const condition = (result.conditions as Array<{ condition: Record<string, unknown> }>)[0].condition;
    assert.equal(condition.satisfied, status === "done");
    assert.equal(condition.availability_reason, status === "done" ? "resume_condition_satisfied" : "resume_condition_invalid");
    assert.equal(condition.invalid_state, status === "done" ? undefined : "monitor_completion_requires_replan");
  }
});

test("self-dependency is not satisfied even when a stale completed row says done", () => {
  for (const kind of ["todo_done", "monitor_changed"]) {
    const result = evaluateTodoResumeConditions({
      schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
      items: [todo("todo_waiting", "done", "continuous_monitor", {
        resume_when: `${kind}:todo_waiting`, resume_monitor_generation: 1, material_change_generation: 2,
      })], source_items: [],
    });
    const condition = (result.conditions as Array<{ condition: Record<string, unknown> }>)[0].condition;
    assert.equal(condition.satisfied, false);
    assert.equal(condition.invalid_state, "dependency_self_reference");
  }
});

test("missing completion target stays pending rather than claiming proof of invalidity", () => {
  const result = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [todo("todo_waiting", "open", "advancement_task", { resume_when: "todo_done:todo_missing" })],
    source_items: [],
  });
  const condition = (result.conditions as Array<{ condition: Record<string, unknown> }>)[0].condition;
  assert.equal(condition.availability_reason, "resume_condition_pending");
  assert.equal(condition.invalid_state, undefined);
});

test("legacy compact diagnosis infers only typed resume syntax, not prose or another kind", () => {
  const legacy = { resume_when: "todo_done:todo_monitor", satisfied: false,
    target_status: "open", target_task_class: "continuous_monitor" };
  assert.deepEqual(diagnoseTodoResumeCondition(legacy), {
    kind: "todo_done", state: "invalid", reason: "monitor_completion_requires_replan",
  });
  for (const kind of ["monitor_changed", "capacity_available", "pr_merged"]) {
    assert.equal(diagnoseTodoResumeCondition({ ...legacy, kind }).state, "pending");
  }
});

test("one reducer evaluates Todo, PR, capacity, monitor, and date resume conditions", () => {
  const result = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [
      todo("todo_wait_done", "open", "advancement_task", {
        resume_when: "todo_done:todo_dependency",
      }),
      todo("todo_wait_pr", "deferred", "advancement_task", {
        resume_when: "pr_merged:#42",
        task_repository: "git:github.com/example/loopx",
      }),
      todo("todo_wait_capacity", "deferred", "advancement_task", {
        resume_when: "capacity_available:shell",
      }),
      todo("todo_wait_monitor", "open", "advancement_task", {
        resume_when: "monitor_changed:todo_watch001",
        resume_monitor_generation: 3,
      }),
      todo("todo_wait_date", "deferred", "advancement_task", {
        resume_when: "resume_at:2026-09-14T09:30:00+08:00",
      }),
    ],
    source_items: [
      todo("todo_dependency", "done"),
      todo("todo_watch001", "open", "continuous_monitor", {
        material_change_generation: 4,
      }),
    ],
    rollout_events: [{
      event_id: "event-merge-42",
      event_kind: "pr_merge",
      code_refs: { pr_ref: "example/loopx#42" },
      recorded_at: "2026-08-25T00:00:00Z",
    }],
    available_capabilities: ["shell"],
    evaluated_at: "2026-09-14T01:30:00Z",
  });
  const conditions = Object.fromEntries(
    (result.conditions as Array<Record<string, unknown>>).map((row) => [
      row.todo_id,
      row.condition,
    ]),
  ) as Record<string, Record<string, unknown>>;
  assert.equal(conditions.todo_wait_done.satisfied, true);
  assert.equal(
    conditions.todo_wait_done.availability_reason,
    "resume_condition_satisfied",
  );
  assert.equal(conditions.todo_wait_pr.matched_pr_ref, "example/loopx#42");
  assert.equal(conditions.todo_wait_capacity.provider_required, false);
  assert.equal(conditions.todo_wait_capacity.satisfied, true);
  assert.equal(conditions.todo_wait_monitor.baseline_generation, 3);
  assert.equal(conditions.todo_wait_monitor.material_change_generation, 4);
  assert.equal(conditions.todo_wait_monitor.satisfied, true);
  assert.equal(
    conditions.todo_wait_monitor.availability_reason,
    "resume_condition_satisfied",
  );
  assert.equal(conditions.todo_wait_date.satisfied, true);
  assert.equal(conditions.todo_wait_date.material_change_generation, 1);
});

test("PR merge source aliases bridge a repository redirect without weakening repository binding", () => {
  const request = {
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [
      todo("todo_old_repo", "deferred", "advancement_task", {
        resume_when: "pr_merged:#4344",
        task_repository: "git:github.com/huangruiteng/loopx",
      }),
      todo("todo_unrelated_repo", "deferred", "advancement_task", {
        resume_when: "pr_merged:#4344",
        task_repository: "git:github.com/example/loopx",
      }),
    ],
    source_items: [],
    rollout_events: [{
      event_id: "event-redirected-merge-4344",
      event_kind: "pr_merge",
      pr_ref: "loopx-project/loopx#4344",
      source_refs: [
        {kind: "pull_request", ref: "huangruiteng/loopx#4344"},
        {kind: "pull_request", ref: "example/loopx#9999"},
      ],
      recorded_at: "2026-09-13T13:45:34Z",
    }],
    available_capabilities: [],
    evaluated_at: "2026-09-20T00:00:00Z",
  };
  const result = evaluateTodoResumeConditions(request);
  const conditions = Object.fromEntries(
    (result.conditions as Array<Record<string, unknown>>).map((row) => [
      row.todo_id,
      row.condition,
    ]),
  ) as Record<string, Record<string, unknown>>;
  assert.equal(conditions.todo_old_repo.satisfied, true);
  assert.equal(
    conditions.todo_old_repo.matched_pr_ref,
    "huangruiteng/loopx#4344",
  );
  assert.equal(conditions.todo_unrelated_repo.satisfied, false);
});

test("monitor resume is generation-fenced and fail-closed without a baseline", () => {
  const result = evaluateTodoResumeConditions({
    schema_version: TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
    items: [
      todo("todo_same_generation", "open", "advancement_task", {
        resume_when: "monitor_changed:todo_watch001",
        resume_monitor_generation: 7,
      }),
      todo("todo_missing_baseline", "open", "advancement_task", {
        resume_when: "monitor_changed:todo_watch001",
      }),
    ],
    source_items: [
      todo("todo_watch001", "open", "continuous_monitor", {
        material_change_generation: 7,
      }),
    ],
    rollout_events: [],
  });
  const conditions = (result.conditions as Array<Record<string, unknown>>).map(
    (row) => row.condition as Record<string, unknown>,
  );
  assert.equal(conditions[0].satisfied, false);
  assert.equal(conditions[0].availability_reason, "resume_condition_pending");
  assert.equal(conditions[1].satisfied, false);
  assert.equal(conditions[1].invalid_state, "baseline_generation_missing");
  assert.equal(conditions[1].availability_reason, "resume_condition_invalid");
});

test("external wait atomically binds a monitor baseline and runnable successor", () => {
  const result = planTodoExternalWaitTransition({
    schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
    todo_id: "todo_waiting001",
    resume_when: "monitor_changed:todo_watch001",
    successor_todo_ids: ["todo_fallback001"],
    items: [
      todo("todo_waiting001", "open"),
      todo("todo_watch001", "open", "continuous_monitor", {
        material_change_generation: 9,
      }),
      todo("todo_fallback001", "open"),
    ],
  });
  assert.equal(result.state, "waiting");
  assert.equal(result.baseline_generation, 9);
  assert.deepEqual(result.metadata_updates, {
    resume_when: "monitor_changed:todo_watch001",
    resume_monitor_generation: 9,
  });
  assert.deepEqual(result.successor_todo_ids, ["todo_fallback001"]);
});

test("external wait rejects monitor completion and non-runnable fallbacks", () => {
  const items = [
    todo("todo_waiting001", "open"),
    todo("todo_watch001", "open", "continuous_monitor"),
    todo("todo_fallback001", "blocked"),
  ];
  assert.throws(() => planTodoExternalWaitTransition({
    schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
    todo_id: "todo_waiting001",
    resume_when: "todo_done:todo_watch001",
    successor_todo_ids: ["todo_fallback001"],
    items,
  }), /use monitor_changed/);
  assert.throws(() => planTodoExternalWaitTransition({
    schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
    todo_id: "todo_waiting001",
    resume_when: "monitor_changed:todo_watch001",
    successor_todo_ids: ["todo_fallback001"],
    items,
  }), /successor must be an open advancement_task/);
});

test("external wait rejection codes identify status and successor faults", () => {
  const commonItems = [
    todo("todo_waiting001", "blocked"),
    todo("todo_watch001", "open", "continuous_monitor"),
    todo("todo_fallback001", "open"),
  ];
  assert.throws(
    () => planTodoExternalWaitTransition({
      schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
      todo_id: "todo_waiting001",
      resume_when: "monitor_changed:todo_watch001",
      successor_todo_ids: ["todo_fallback001"],
      items: commonItems,
    }),
    (error: unknown) =>
      error instanceof EffectRuntimeRequestError &&
      error.code === "external_wait_todo_status_must_remain_open" &&
      /must remain status=open/.test(error.message),
  );
  assert.throws(
    () => planTodoExternalWaitTransition({
      schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
      todo_id: "todo_waiting001",
      resume_when: "monitor_changed:todo_watch001",
      successor_todo_ids: [],
      items: [
        todo("todo_waiting001", "open"),
        todo("todo_watch001", "open", "continuous_monitor"),
      ],
    }),
    (error: unknown) =>
      error instanceof EffectRuntimeRequestError &&
      error.code === "external_wait_successor_required",
  );
});

test("a satisfied monitor fence must be cleared before it can be re-armed", () => {
  assert.throws(() => planTodoExternalWaitTransition({
    schema_version: TODO_EXTERNAL_WAIT_REQUEST_SCHEMA_VERSION,
    todo_id: "todo_waiting001",
    resume_when: "monitor_changed:todo_watch001",
    successor_todo_ids: ["todo_fallback001"],
    items: [
      todo("todo_waiting001", "open", "advancement_task", {
        resume_when: "monitor_changed:todo_watch001",
        resume_monitor_generation: 2,
      }),
      todo("todo_watch001", "open", "continuous_monitor", {
        material_change_generation: 3,
      }),
      todo("todo_fallback001", "open"),
    ],
  }), /clear the satisfied resume_when/);
});
