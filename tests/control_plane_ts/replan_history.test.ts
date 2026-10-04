import assert from "node:assert/strict";
import test from "node:test";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import { projectReplanHistory } from "../../loopx/control_plane/work_items/replan_history.ts";

const neutral = ["quota_slot_spent", "quota_slot_voided", "delivery_completion_spend_accounted_v0"];
function run(id: number, patch: JsonObject = {}): JsonObject {
  return {
    agent_id: "worker-a", public_agent_id: "worker-a", monitor_agent_id: "worker-a",
    classification: "bounded_delivery", generated_at: `row-${id}`, observed_at: id,
    turn_id: `turn-${id}`, accepted_ack: false, progress: null,
    monitor: { target_id: null, mode: null, frontier: null, todo_id: null, target_key: null }, ...patch,
  };
}
const observation = {
  schema_version: "typed_progress_observation_v0", result_class: "unchanged",
  fingerprint: "same-surface", surface_id: "fixture", evidence_ids: ["evidence-fixture"],
};
function poll(id: number, patch: JsonObject = {}): JsonObject {
  return run(id, { classification: "quota_monitor_poll", monitor: {
    target_id: "watch", mode: "due_monitor_observed_without_material_transition",
    material_change: false, frontier: null, todo_id: "todo_watch", target_key: null, ...patch,
  } });
}
function request(runs: JsonObject[], patch: JsonObject = {}): JsonObject {
  return {
    schema_version: "replan_history_request_v0", operation: "all", runs,
    agent_id: "worker-a", monitor_agent_id: "worker-a", neutral_classifications: neutral,
    stall_threshold: 2, periodic_threshold: 20, monitor_threshold: 6, streak_threshold: 5,
    monitor_schema: "dead_monitor_repeat_v0", todos: { monitors: [], advancements: [], resume: null }, ...patch,
  };
}
function trigger(runs: JsonObject[], patch: JsonObject = {}): JsonObject | null {
  return projectReplanHistory(request(runs, patch)).trigger as JsonObject | null;
}
const many = (count: number, build = run): JsonObject[] => Array.from({ length: count }, (_, i) => build(count - i));

test("periodic work counts distinct logical turns and keeps the existing public trigger", () => {
  assert.equal(trigger(many(30, () => run(1))), null);
  assert.deepEqual(trigger(many(20)), {
    kind: "periodic_review_due", section: "run_history",
    text: "latest 20 durable public run records since last autonomous replan reached periodic review threshold 20",
    run_count: 20, threshold: 20, latest_generated_at: "row-20", oldest_counted_generated_at: "row-1",
    agent_id: "worker-a",
  });
  assert.equal(trigger(many(19)), null);
});

test("periodic review counts material work, not quiet monitor receipts", () => {
  const quiet = many(20, n => poll(n, { mode: n > 18
    ? "due_monitor_observed_without_material_transition" : "monitor_quiet_until_material_transition" }));
  assert.equal(trigger(quiet, { operation: "periodic" }), null);
  assert.equal(trigger([...quiet, ...many(19)], { operation: "periodic" }), null);
  const materialPoll = poll(21, { mode: "due_monitor_material_transition", material_change: true });
  assert.equal(trigger([materialPoll, ...quiet, ...many(19)], { operation: "periodic" })?.kind,
    "periodic_review_due");
});

test("ACK is a lane-scoped cutoff, including an ACK sharing the newest turn id", () => {
  for (const operation of ["all", "progress", "periodic"]) {
    const material = many(25, n => run(n, { progress: observation }));
    assert.equal(trigger([{ ...material[0], accepted_ack: true }, ...material], { operation }), null);
    assert.notEqual(trigger([{ ...run(30), accepted_ack: true, agent_id: "peer" }, ...material], { operation }), null);
  }
  assert.equal(trigger([run(25, { progress: observation }), run(24, { accepted_ack: true }),
    run(23, { progress: observation })]), null);
});

test("neutral rows are transparent even when they carry the material turn id", () => {
  for (const classification of neutral) {
    const rows = [run(2, { classification }), run(2, { progress: observation }),
      run(1, { classification }), run(1, { progress: observation })];
    assert.equal(trigger(rows)?.kind, "typed_progress_repeat");
  }
});

test("unknown work breaks a started progress streak; leading untyped history remains compatible", () => {
  const p = (n: number) => run(n, { progress: observation });
  assert.equal(trigger([p(3), run(2), p(1)]), null);
  assert.equal(trigger([run(3), p(2), p(1)])?.kind, "typed_progress_repeat");
  assert.equal(trigger([run(2), p(2), p(1)])?.kind, "typed_progress_repeat",
    "an untyped record does not consume the progress evidence identity");
  assert.equal(trigger([p(3), run(2, { classification: "quota_slot_voided_suffix" }), p(1)]), null);
});

test("progress fingerprints and outcomes govern repetition, never prose", () => {
  for (const result_class of ["advanced", "exploration_exhausted", "no_followup"]) {
    assert.equal(trigger(many(2, n => run(n, { progress: { ...observation, result_class } }))), null);
  }
  assert.equal(trigger([run(2, { progress: observation }), run(1, {
    progress: { ...observation, fingerprint: "different-surface" },
  })]), null);
  assert.equal(trigger(many(2, n => run(n, { progress: { ...observation, result_class: "blocked" },
    ignored_text: `advanced ${n}` })))?.kind, "typed_progress_repeat");
});

test("one retried monitor poll never becomes six observations", () => {
  assert.equal(trigger(many(30, () => poll(1))), null);
  assert.equal(trigger(many(6, poll))?.kind, "dead_monitor_repeat");
  assert.equal(trigger(many(5, poll)), null);
  assert.equal(trigger([poll(6, { target_id: "other" }), ...many(5, poll)]), null);
  assert.equal(trigger(many(6, n => poll(n, { todo_id: null }))), null);
});

test("legacy missing attribution retains turn identity within a scoped lane", () => {
  const attributed = run(1, { progress: observation });
  const goalLevel = { ...attributed, agent_id: null, public_agent_id: null, monitor_agent_id: null };
  assert.equal(trigger([attributed, goalLevel]), null);
  assert.equal(trigger(many(20, n => ({ ...attributed, ...(n % 2 ? goalLevel : {}) })),
    { operation: "periodic" }), null);
});

test("blocked successor compares typed target and frontier; ordinary work ends the monitor prefix", () => {
  const blocked = (n: number, frontier = "frontier-a") => poll(n, {
    mode: "blocked_successor_wait_without_material_transition", frontier,
  });
  assert.equal(trigger([blocked(2), blocked(1)])?.kind, "blocked_successor_no_progress_repeat");
  assert.equal(trigger([blocked(2), blocked(1, "frontier-b")]), null);
  assert.equal(trigger([blocked(3), run(2), blocked(1)]), null);
});

test("each historical trigger has deterministic precedence", () => {
  const all = many(20, n => ({ ...poll(n), progress: observation }));
  assert.equal(trigger(all)?.kind, "typed_progress_repeat");
  assert.equal(trigger(many(20, poll))?.kind, "dead_monitor_repeat");
  assert.equal(trigger(many(20))?.kind, "periodic_review_due");
});

test("multi-agent fixture retains a complete lane through peer ACKs, retries, and accounting", () => {
  const rows = many(20).flatMap(row => [
    { ...row, classification: neutral[0] }, row, { ...row },
    { ...row, agent_id: "peer", accepted_ack: true },
    ...many(10, n => run(n, { agent_id: `peer-${n}` })),
  ]);
  assert.equal(rows.length, 280);
  const before = structuredClone(rows);
  assert.equal(trigger(rows)?.run_count, 20);
  assert.equal(trigger(rows.slice(0, -14)), null);
  assert.deepEqual(rows, before, "the reducer may not reorder or mutate source history");
  assert.equal(trigger(many(2, n => run(n, { turn_id: null, progress: observation })))?.kind,
    "typed_progress_repeat", "untrustworthy legacy ids must not be deduplicated");
});

test("inferred lane follows the newest non-neutral attributed work", () => {
  const rows = [run(30, { agent_id: "peer", classification: neutral[0] }), ...many(20)];
  assert.equal(trigger(rows, { agent_id: null })?.agent_id, "worker-a");
  assert.equal(trigger([...many(20, n => run(n, { agent_id: null, public_agent_id: null }))],
    { agent_id: null })?.agent_id, null);
});

function monitorTodo(patch: JsonObject = {}): JsonObject {
  return { id: "todo_watch", claim: "worker-a", public_claim: "worker-a", target: "watch",
    no_change_count: 5, due_at: null, expires_at: null, ...patch };
}
test("persisted monitor streak retains stable tie order and advancement ownership", () => {
  const todos = { monitors: [monitorTodo(), monitorTodo({ target: "second" })], advancements: [], resume: null };
  assert.equal(trigger([], { operation: "monitor_streak", todos })?.monitor_target_id, "watch");
  for (const claim of [null, "worker-a", "peer"]) {
    const result = trigger([], { operation: "monitor_streak", todos: { ...todos,
      advancements: [{ status: "open", task_class: "advancement_task", claim }],
    } });
    assert.equal(result === null, claim !== "peer");
  }
  assert.equal(trigger([], { operation: "monitor_streak", todos: { ...todos,
    monitors: [monitorTodo({ no_change_count: 4 })],
  } }), null);
});

for (const [key, bad, message] of [
  ["schema_version", "future_schema", /schema/], ["operation", "dispatch", /operation/],
  ["runs", {}, /runs/], ["stall_threshold", 1, /stall_threshold/],
  ["periodic_threshold", 0, /periodic_threshold/], ["monitor_threshold", 1.5, /monitor_threshold/],
  ["streak_threshold", Number.MAX_SAFE_INTEGER + 1, /streak_threshold/],
  ["neutral_classifications", [true], /neutral_classifications/],
] as const) {
  test(`reject malformed boundary field ${key}`, () => {
    assert.throws(() => projectReplanHistory(request([], { [key]: bad })), message);
  });
}
test("malformed typed facts fail visibly rather than falling back to Python policy", () => {
  for (const patch of [
    { accepted_ack: "true" }, { observed_at: NaN }, { progress: { ...observation, result_class: "success" } },
    { progress: { ...observation, fingerprint: "" } }, { monitor: [] },
    { monitor: { ...poll(1).monitor as JsonObject, material_change: "true" } },
  ]) assert.throws(() => projectReplanHistory(request([run(1, patch)])));
});

// Transport parity covers representative policy cases without another reducer.
// Large histories remain complete; snapshot validation precedes policy evaluation.
import { createHash } from "node:crypto";
import { chmod, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { projectReplanHistorySnapshot } from "../../loopx/control_plane/work_items/replan_history_snapshot.ts";

async function withSnapshot(payload: unknown, body: (ref: JsonObject) => Promise<void>) {
  const directory = await mkdtemp(join(tmpdir(), "replan-history-test-"));
  const path = join(directory, "request.json");
  const bytes = Buffer.from(JSON.stringify(payload));
  try {
    await writeFile(path, bytes, { mode: 0o600 });
    await body({ schema_version: "replan_history_snapshot_v0", path,
      byte_count: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") });
  } finally { await rm(directory, { recursive: true, force: true }); }
}

test("snapshot transport preserves scope, ACK, retries, precedence and all operations", async () => {
  const cases = [many(20), many(6, poll), many(2, n => run(n, { progress: observation })),
    [run(3, { accepted_ack: true }), ...many(20)],
    [run(3, { agent_id: "peer", accepted_ack: true }), ...many(20)],
    [run(3), ...many(2, n => run(n, { progress: observation }))],
    many(30, () => run(1)), many(20, n => run(n, { agent_id: null }))];
  for (const operation of ["all", "progress", "periodic", "monitor_streak"]) {
    for (const rows of cases) {
      const payload = request(rows, { operation });
      await withSnapshot(payload, async ref => {
        assert.deepEqual(await projectReplanHistorySnapshot(ref), projectReplanHistory(payload));
      });
    }
  }
});

test("snapshot rejects changed, missing, malformed and unsafe references", async () => {
  await withSnapshot(request(many(20)), async ref => {
    for (const patch of [
      { byte_count: 1 }, { sha256: "0".repeat(64) }, { byte_count: -1 },
      { path: "relative.json" }, { schema_version: "future" },
    ]) await assert.rejects(projectReplanHistorySnapshot({ ...ref, ...patch }));
    const path = ref.path as string;
    if (process.platform !== "win32") {
      const link = `${path}.link`;
      await symlink(path, link);
      await assert.rejects(projectReplanHistorySnapshot({ ...ref, path: link }));
      await chmod(path, 0o644);
      await assert.rejects(projectReplanHistorySnapshot(ref));
      await chmod(path, 0o600);
    }
    const malformed = "x".repeat(ref.byte_count as number);
    await writeFile(path, malformed);
    await assert.rejects(projectReplanHistorySnapshot(ref), /snapshot unavailable or invalid/);
    await assert.rejects(projectReplanHistorySnapshot({ ...ref,
      sha256: createHash("sha256").update(malformed).digest("hex"),
    }), /snapshot unavailable or invalid/);
    await rm(path);
    await assert.rejects(projectReplanHistorySnapshot(ref), /snapshot unavailable or invalid/);
  });
  await withSnapshot({ schema_version: "invalid" }, async ref => {
    await assert.rejects(projectReplanHistorySnapshot(ref), /replan history schema/);
  });
});
