import assert from "node:assert/strict";
import { test } from "node:test";
import { formatMonitorDate, monitorScheduleReadback, monitorTodoReadback, receiptRetryReadback } from "./monitor-readback.ts";

test("receipt wait displays its original clocks and Turn without creating a monitor", () => {
  const retry = { source: "turn_settlement", todo_id: "todo_retry", agent_id: "agent-a",
    observed_at: "2030-10-01T10:58:16Z", due_at: "2030-10-01T11:03:16Z", turn_instance_id: "original-turn" };
  const todo = { todo_id: "todo_retry", claimed_by: "agent-a", resume_condition: { blocked_retry: retry } };
  assert.deepEqual(receiptRetryReadback(todo), { observedAt: retry.observed_at, dueAt: retry.due_at, turnId: retry.turn_instance_id });
  for (const bad of [null, [], {}, { ...retry, todo_id: "todo_foreign" },
    { ...retry, agent_id: "agent-b" }, { ...retry, source: "todo" }, { ...retry, turn_instance_id: "" }]) {
    assert.equal(receiptRetryReadback({ ...todo, resume_condition: { blocked_retry: bad } }), null);
  }
  assert.equal(receiptRetryReadback({ ...todo, resume_condition: {} }), null);
});

test("canonical monitor facts survive Todo and schedule projections", () => {
  const source = { todo_id: "monitor-test", done: false, text: "Synthetic monitor",
    cadence: "6h", next_due_at: "2030-10-02T18:58:16.886000+08:00",
    expires_at: "2030-10-05T10:58:16.886000+00:00",
    last_checked_at: "2030-10-01T10:58:16Z", claimed_by: "monitor-owner", watch_only: "true",
    target_key: "synthetic-subject", resume_when: "source_changes:synthetic-source" };
  const todo = { ...monitorTodoReadback(source), claimedBy: source.claimed_by, resumeWhen: source.resume_when };
  assert.deepEqual(monitorScheduleReadback(todo), {
    agentId: "monitor-owner", expiresAt: source.expires_at, nextRunAt: source.next_due_at,
    previousRunAt: source.last_checked_at, schedule: "6h", stopCondition: source.resume_when,
    timezone: "UTC", watchOnly: true,
  });
  assert.match(formatMonitorDate(source.next_due_at, "en"), /10:58:16.*UTC/);
  assert.match(formatMonitorDate(source.expires_at, "zh-CN"), /2030\/10\/05.*10:58:16.*UTC/);
});

test("unknown readback does not fabricate owner, execution, expiry or permission", () => {
  const fields = monitorScheduleReadback(monitorTodoReadback({ done: false, text: "Legacy monitor" }));
  for (const field of ["agentId", "expiresAt", "nextRunAt", "previousRunAt", "schedule", "stopCondition", "watchOnly"]) {
    assert.equal(fields[field], undefined);
  }
  assert.equal(monitorTodoReadback({ watch_only: "unknown" }).watchOnly, null);
  assert.equal(monitorTodoReadback({ watch_only: false }).watchOnly, false);
  assert.equal(monitorTodoReadback({ watch_only: "false" }).watchOnly, false);
});

test("offset-less and invalid dates stay unknown, never browser-local guessed dates", () => {
  for (const value of [undefined, "", "not-a-date", "2030-10-02", "2030-10-02T10:58:16", "invalidZ", "2030-02-31T10:58:16Z"]) {
    assert.equal(formatMonitorDate(value, "en"), null);
  }
  assert.equal(formatMonitorDate("2030-10-02T18:58:16+08:00", "en"), formatMonitorDate("2030-10-02T10:58:16Z", "en"));
});
