import assert from "node:assert/strict";
import test from "node:test";
import { planMonitorMetadata, TODO_MONITOR_METADATA_REQUEST_SCHEMA as SCHEMA } from "../../loopx/control_plane/todos/monitor_metadata.ts";
import { planTodoFieldUpdate, TODO_FIELD_UPDATE_REQUEST_SCHEMA } from "../../loopx/control_plane/todos/field_update.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { productionScaleCoordinationFixture } from "./production_scale_coordination_fixture.ts";
import { parseTodoTimestampMicros } from "../../loopx/control_plane/runtime_timestamp.ts";

const existing = {todo_id: "todo_monitor", task_class: "continuous_monitor", status: "open",
  target_key: "fixture", cadence: "1h", watch_only: "true", result_hash: "before",
  material_change_generation: "7", consecutive_no_change: "3"};
function request(extra: JsonObject = {}): JsonObject {
  return {schema_version: SCHEMA, existing, role: "agent", task_class: "continuous_monitor",
    generated_at: "2030-01-01T00:00:00Z", enforce_boundedness: true, ...extra};
}
function observation(extra: JsonObject = {}): JsonObject {
  return {generated_at: "2030-01-01T01:00:00Z", result_hash: "after", material_change: true,
    monitor_effect_id: "effect-a", ...extra};
}

test("generation and no-change count follow result semantics, not mere poll count", () => {
  for (const [material, hash, generation, count] of [
    [true, "after", 8, 0], [true, "before", 7, 0],
    [false, "after", 7, 0], [false, "before", 7, 4],
  ] as const) {
    const result = planMonitorMetadata(request({observation: observation({material_change: material, result_hash: hash})}));
    assert.equal(result.metadata.material_change_generation, String(generation));
    assert.equal(result.metadata.consecutive_no_change, String(count));
    assert.equal(result.transition?.material_change_applied, generation === 8);
    assert.equal(result.metadata.next_due_at, "2030-01-01T02:00:00Z");
  }
});

test("exact retry reuses counters; different effect intent cannot reuse its ID", () => {
  const original = structuredClone(existing);
  const first = planMonitorMetadata(request({observation: observation()}));
  assert.deepEqual(existing, original);
  const persisted = {...existing, ...first.metadata};
  const replay = planMonitorMetadata(request({existing: persisted, observation: observation()}));
  assert.equal(replay.transition?.provider_replayed, true);
  assert.equal(replay.transition?.material_change_applied, false);
  assert.deepEqual(replay.metadata, Object.fromEntries(Object.entries(persisted).filter(([key]) =>
    !["todo_id", "task_class", "status"].includes(key))));
  for (const conflicting of [{result_hash: "other"}, {material_change: false}, {cadence: "2h"},
    {generated_at: "2030-01-01T02:00:00Z"}]) {
    assert.throws(() => planMonitorMetadata(request({existing: persisted, observation: observation(conflicting)})), /different observation/);
  }
});

test("out-of-order rejection does not depend on effect IDs", () => {
  for (const previousId of [null, "old-effect"]) for (const nextId of [null, "next-effect"]) {
    assert.throws(() => planMonitorMetadata(request({
      existing: {...existing, last_checked_at: "2030-01-01T02:00:00Z", monitor_effect_id: previousId},
      observation: observation({monitor_effect_id: nextId}),
    })), /older/);
  }
  assert.doesNotThrow(() => planMonitorMetadata(request({
    existing: {...existing, last_checked_at: "2030-01-01T01:00:00Z"},
    observation: observation({monitor_effect_id: null}),
  })));
});

test("create/edit scope, boundedness and explicit clearing share one owner", () => {
  assert.deepEqual(planMonitorMetadata(request({existing: {}, metadata: {cadence: "30m", watch_only: "true"}})).metadata,
    {cadence: "30m", watch_only: "true", next_due_at: "2030-01-01T00:30:00Z"});
  assert.throws(() => planMonitorMetadata(request({metadata: {watch_only: null}})), /requires one of/);
  assert.doesNotThrow(() => planMonitorMetadata(request({metadata: {watch_only: null}, resume_when: "todo_done:todo_dependency"})));
  assert.doesNotThrow(() => planMonitorMetadata(request({metadata: {watch_only: null}, enforce_boundedness: false})));
  assert.throws(() => planMonitorMetadata(request({role: "user", metadata: {cadence: "1h"}})), /schedule metadata/);
  assert.throws(() => planMonitorMetadata(request({role: "user", metadata: {target_key: "fixture"}})), /target_key/);
  assert.throws(() => planMonitorMetadata(request({metadata: {cadence: "never"}})), /cadence/);
  for (const field of ["consecutive_no_change", "material_change_generation"]) {
    for (const value of ["-1", "9007199254740993", "1.5"]) {
      assert.throws(() => planMonitorMetadata(request({metadata: {[field]: value}})), /integer/);
    }
  }
});

test("public update field plan composes the observation once and keeps completion independent", () => {
  const result = planTodoFieldUpdate({schema_version: TODO_FIELD_UPDATE_REQUEST_SCHEMA,
    todo: existing, updated_at: "2030-01-01T01:00:00Z", intent: {note: "Checked"},
    monitor_context: {role: "agent", task_class: "continuous_monitor", enforce_boundedness: true,
      observation: observation()},
  });
  assert.equal(result.metadata_updates.material_change_generation, "8");
  assert.equal(result.metadata_updates.note, "Checked");
  assert.equal(result.target_status, "open");
  assert.equal((result.monitor_poll_transition as JsonObject).provider_replayed, false);
  assert.equal(result.metadata_updates.claimed_by, undefined);
});

test("poll rejects invalid observation shapes without mutating its input", () => {
  for (const invalid of [{material_change: "false"}, {generated_at: "invalid", next_due_at: "2030-01-01T03:00:00Z"},
    {result_hash: ""}, {target_key: "different"}]) {
    const source = request({observation: observation(invalid)});
    const before = structuredClone(source);
    assert.throws(() => planMonitorMetadata(source));
    assert.deepEqual(source, before);
  }
});

test("timestamp codec rejects rollover dates and ordering retains microseconds", () => {
  for (const invalid of ["2030-02-30T00:00:00Z", "2030"]) {
    assert.throws(() => planMonitorMetadata(request({metadata: {expires_at: invalid}})), /timestamp/);
  }
  assert.throws(() => planMonitorMetadata(request({
    metadata: {expires_at: "2030-01-01T24:00:00Z"},
  })), /timestamp/);
  assert.throws(() => planMonitorMetadata(request({
    existing: {...existing, last_checked_at: "2030-01-01T01:00:00.000002Z"},
    observation: observation({generated_at: "2030-01-01T01:00:00.000001Z", monitor_effect_id: null}),
  })), /older/);
  assert.doesNotThrow(() => planMonitorMetadata(request({
    existing: {...existing, last_checked_at: "invalid historical date"}, observation: observation(),
  })));
});

test("timestamp timezone letters are suffixes, not date separators", () => {
  for (const date of ["1970-01-01", "19700101", "1970-W01-4"]) {
    for (const letter of ["Z", "z"]) {
      assert.equal(parseTodoTimestampMicros(`${date}${letter}00:00`), null);
      assert.equal(parseTodoTimestampMicros(`${date}T00:00${letter}`), 0n);
    }
  }
  for (const time of ["00", "0000", "00:00", "000000", "00:00:00"]) {
    assert.equal(parseTodoTimestampMicros(`1970-01-01T${time}Z`), 0n);
  }
});

test("production-scale snapshot remains immutable while each monitor gets an isolated plan", () => {
  const fixture = productionScaleCoordinationFixture("goal-fixture");
  const before = structuredClone(fixture);
  const todos = fixture.projection.todos as JsonObject[];
  const monitors = todos.filter(todo => todo.task_class === "continuous_monitor");
  assert.equal(todos.length, 464);
  assert.equal((fixture.projection.leases as unknown[]).length, 64);
  assert.equal(monitors.length, 63);
  for (const todo of monitors) {
    // A pure plan does not grant permission to mutate archived/completed or
    // lease-bearing work. Preserve the complete fixture and test the rule only.
    const result = planMonitorMetadata(request({existing: todo, enforce_boundedness: false,
      observation: observation({target_key: null, cadence: "1h"}),
    }));
    assert.equal(result.metadata.material_change_generation, String(Number(todo.material_change_generation ?? 0) + 1));
    assert.equal(result.metadata.consecutive_no_change, "0");
    assert.equal(result.metadata.claimed_by, undefined);
    assert.equal(result.metadata.status, undefined);
  }
  assert.deepEqual(fixture, before);
});

test("Todo timestamp codec retains Python ISO compatibility, timezone seconds and exact microseconds", () => {
  for (const value of ["1970-01-01", "19700101", "1970-W01-4", "1970W014",
    "19700101T00", "1970-01-01X0000", "1970-01-01 00:00:00", "1970-01-01T00:00z"]) {
    assert.equal(parseTodoTimestampMicros(value), 0n, value);
  }
  for (const value of ["1970-01-01T00:00:00.000001Z", "19700101T000000,000001",
    "1970-01-01T01:00:00+00:59:59.999999"]) {
    assert.equal(parseTodoTimestampMicros(value), 1n, value);
  }
  for (const value of ["1970-01-01T00.1", "1970-01-01T00:00.1"]) {
    assert.equal(parseTodoTimestampMicros(value), 100000n, value);
  }
  assert.equal(parseTodoTimestampMicros("1970-01-01T00:00:00+01.1"), -3600100000n);
  for (const value of ["1970-01-01T00:00:00+00.1", "1970-01-01T00:00:00-00.1",
    "1970-01-01T00:00:00+00:00:00.1"]) {
    assert.equal(parseTodoTimestampMicros(value), 0n, value);
  }
  for (const value of ["1970-01-01T00.1+00.5", "1970-01-01T00:00:00.1+00:00:00.5"]) {
    assert.equal(parseTodoTimestampMicros(value), 100000n, value);
  }
  for (const value of ["1970-02-30", "2021-W53", "1970-01-01T01:00+24:00",
    "1970-01-01T24:00:00", "1970-01-01T24:00:01",
    "9999-12-31T24:00:00", "9999-12-31T24:00:00Z", "9999-12-31T24:00:00.000000",
    "1970-01-01T00:0000", "1970-01-01T0000:00", "tomorrow", "2030"]) {
    assert.equal(parseTodoTimestampMicros(value), null, value);
  }
});
