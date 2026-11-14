import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {appendFile, mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import test from "node:test";

import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {nativeChildResultInbox} from "../../loopx/control_plane/capabilities/native_child_result_inbox.ts";
import {goalRolloutEventLogPath, ROLLOUT_EVENT_SCHEMA_VERSION} from "../../loopx/control_plane/rollout_receipt_log.ts";
import {readCollaborationInbox} from "../../loopx/control_plane/collaboration/inbox_read.ts";

const goalRef = {goal_id: "research", goal_instance_id: "ginst_" + "a".repeat(32)};
const base = {goal_id: "research", agent_id: "coordinator", goal_ref: goalRef};
const at = "2026-11-13T00:00:00.000000+00:00";

function fixture(t: test.TestContext) {
  const runtime_root = mkdtempSync(join(tmpdir(), "loopx-native-result-"));
  t.after(() => rm(runtime_root, {recursive: true, force: true}));
  const call = (operation: string, extra: JsonObject = {}) => nativeChildResultInbox({
    operation, runtime_root, ...base, ...extra,
  });
  const inbox = (operation: string, extra: JsonObject = {}) => readCollaborationInbox({
    operation, runtime_root, ...base, observed_at: at,
    read_command: "loopx manager-inbox read",
    scope: {profile_id: "source_session_v1", caller_goal_ref: goalRef, current_goal_ref: goalRef},
    ...extra,
  });
  const log = goalRolloutEventLogPath(runtime_root, base.goal_id);
  const terminalId = (input: JsonObject) => createHash("sha256")
    .update(JSON.stringify([input.turn_instance_id, input.operation_id, input.outcome])).digest("hex").slice(0, 16);
  const commit = async (input: JsonObject, eventId = terminalId(input)) => {
    await mkdir(dirname(log), {recursive: true});
    await appendFile(log, JSON.stringify({schema_version: ROLLOUT_EVENT_SCHEMA_VERSION,
      ...base, event_id: eventId, event_kind: "native_child_result",
      run_id: input.turn_instance_id, case_id: input.operation_id, status: input.outcome,
      details: {outcome: input.outcome, observation_source: "host_observed"}}) + "\n");
  };
  const stage = (input: JsonObject) => call("ingest", {...input, terminal_event_id: terminalId(input)});
  const ingest = async (input: JsonObject) => {
    const row = await stage(input);
    await commit(input);
    return row;
  };
  return {runtime_root, call, inbox, stage, commit, ingest};
}

test("a completed host result survives replay and enters the next Turn unread", async t => {
  const f = fixture(t), input = {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Independent review passed.", observed_at: at};
  const created = await f.ingest(input), replay = await f.stage(input);
  assert.equal(created.created, true);
  assert.equal(replay.replay, true);
  assert.equal(replay.result_id, created.result_id);

  const turn = await f.inbox("turn_start"), page = turn.result as JsonObject;
  const [result] = (page.native_child_results as JsonObject).items as JsonObject[];
  assert.equal(result.result_id, created.result_id);
  assert.equal(result.text, input.text);
  assert.equal(result.delivery, null);

  const delivered = await f.inbox("receiver_read");
  assert.equal(delivered.completed, true);
  const next = await f.call("list"), [again] = next.items as JsonObject[];
  assert.equal((again.delivery as JsonObject).delivery_count, 1);
  assert.equal(again.result_id, created.result_id);
});

test("acknowledgement follows delivery and restart replay does not duplicate the effect", async t => {
  const f = fixture(t), input = {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Use this once.", observed_at: at};
  const created = await f.ingest(input);
  await assert.rejects(f.call("acknowledge", {result_id: created.result_id,
    turn_instance_id: "turn-1", disposition: "accepted", observed_at: at}), /delivered before acknowledgement/);
  await f.inbox("receiver_read");
  const first = await f.call("acknowledge", {result_id: created.result_id,
    turn_instance_id: "turn-1", disposition: "accepted", observed_at: at});
  const replay = await f.call("acknowledge", {result_id: created.result_id,
    turn_instance_id: "turn-1", disposition: "accepted", observed_at: "2026-11-13T00:01:00.000000+00:00"});
  assert.equal(first.replay, false);
  assert.equal(replay.replay, true);
  assert.deepEqual((await f.call("list")).items, []);
  await assert.rejects(f.call("acknowledge", {result_id: created.result_id,
    turn_instance_id: "turn-1", disposition: "rejected", observed_at: at}), /conflicting acknowledgement/);
});

test("conflicting content cannot replace an operation result", async t => {
  const f = fixture(t), input = {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Original", observed_at: at};
  await f.ingest(input);
  await assert.rejects(f.stage({...input, text: "Replacement"}), /conflicting content/);
  const [result] = (await f.call("list")).items as JsonObject[];
  assert.equal(result.text, "Original");
});

test("large result text stays in a private file while the inbox keeps a verified reference", async t => {
  const f = fixture(t), text = "结果".repeat(40_000);
  await f.ingest({turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text, observed_at: at});
  const [result] = (await f.call("list")).items as JsonObject[];
  assert.equal(result.text, undefined);
  assert.equal(await readFile(String(result.content_file), "utf8"), text);
  assert.match(String(result.content_sha256), /^sha256:[0-9a-f]{64}$/u);
});

test("corrupt content stays visibly unavailable and cannot starve later results", async t => {
  const f = fixture(t), text = "a".repeat(40_000);
  const first = await f.ingest({turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text, observed_at: at});
  for (let index = 2; index <= 22; index += 1) {
    await f.ingest({turn_instance_id: `turn-${index}`, operation_id: `operation-${index}`,
      outcome: "completed", text: `result-${index}`, observed_at: at});
  }
  const listed = await f.call("list"), large = (listed.items as JsonObject[])
    .find(item => item.result_id === first.result_id);
  assert.ok(large?.content_file);
  await writeFile(String(large.content_file), "b".repeat(40_000), "utf8");
  const after = await f.call("list");
  assert.equal(after.unavailable_count, 1);
  assert.equal((after.items as JsonObject[]).length, 20);
  assert.equal(after.has_more, true);
  const turn = await f.inbox("turn_start"), page = turn.result as JsonObject;
  assert.equal((page.native_child_results as JsonObject).unavailable_count, 1);
  await rm(String(large.content_file));
  const missing = await f.call("list");
  assert.equal(missing.unavailable_count, 1);
  assert.equal((missing.items as JsonObject[]).length, 20);
});

test("Goal lifetime and Agent scope isolate private native results", async t => {
  const f = fixture(t);
  await f.ingest({turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Scoped", observed_at: at});
  assert.deepEqual((await nativeChildResultInbox({operation: "list", runtime_root: f.runtime_root,
    ...base, agent_id: "another"})).items, []);
  assert.deepEqual((await nativeChildResultInbox({operation: "list", runtime_root: f.runtime_root,
    ...base, goal_ref: {...goalRef, goal_instance_id: "ginst_" + "b".repeat(32)}})).items, []);
});


test("only the exact committed host receipt makes staged content deliverable", async t => {
  const f = fixture(t), input = {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Provisional text", observed_at: at};
  const staged = await f.stage(input);
  await f.commit(input, "a".repeat(16));
  const before = await f.call("list");
  assert.deepEqual(before.items, []);
  assert.equal(before.unavailable_count, 1);
  await assert.rejects(f.call("mark_delivered", {result_ids: [staged.result_id], observed_at: at}),
    /requires a committed host report/);
  await f.commit(input);
  const [result] = (await f.call("list")).items as JsonObject[];
  assert.equal(result.result_id, staged.result_id);
  assert.equal(result.text, input.text);
});

test("manager Inbox can continue across a bounded page of acknowledged results", async t => {
  const f = fixture(t);
  const sample = {turn_instance_id: "seed-turn", operation_id: "seed-operation",
    outcome: "completed", text: "seed".repeat(10_000), observed_at: at};
  const created = await f.ingest(sample);
  const [listed] = (await f.call("list")).items as JsonObject[];
  const folder = dirname(String(listed.content_file));
  const stored = JSON.parse(await readFile(join(folder, `${created.result_id}.json`), "utf8"));
  const target = {turn_instance_id: "new-turn", operation_id: "new-operation",
    outcome: "completed", text: "new result after historical acknowledgements", observed_at: at};
  const last = await f.ingest(target);

  // Materialize historical acknowledgements in the shipped file format.
  // Compute fixture identities directly, independently of the list projection.
  const suffix = (turn: string, operation: string) => createHash("sha256").update(JSON.stringify({
    agent_id: base.agent_id, goal_id: null, goal_ref: {goal_id: goalRef.goal_id,
      goal_instance_id: goalRef.goal_instance_id}, operation_id: operation, turn_instance_id: turn,
  })).digest("hex").slice(0, 32);
  const writes: Array<() => Promise<void>> = [];
  for (let index = 0; writes.length < 2_000; index += 1) {
    const turn = `old-turn-${index}`, operation = `old-operation-${index}`;
    const id = `ncr_${suffix(turn, operation)}`;
    if (id >= String(last.result_id)) continue;
    writes.push(() => writeFile(join(folder, `${id}.json`), JSON.stringify({...stored,
      result_id: id, turn_instance_id: turn, operation_id: operation,
      delivery: {first_delivered_at: at, last_delivered_at: at, delivery_count: 1},
      acknowledgement: {acknowledged_at: at, disposition: "accepted"},
    })));
  }
  for (let index = 0; index < writes.length; index += 32) {
    await Promise.all(writes.slice(index, index + 32).map(write => write()));
  }
  const first = (await f.inbox("receiver_read")).result as JsonObject;
  const page = first.native_child_results as JsonObject;
  assert.equal(page.scan_complete, false);
  assert.equal(page.has_more, true);
  assert.ok(typeof page.next_cursor === "string");
  assert.ok(!(page.items as JsonObject[]).some(row => row.result_id === last.result_id));
  await assert.rejects(f.inbox("receiver_read", {cursor: page.next_cursor, agent_id: "another"}),
    /cursor or scope mismatch/);
  const second = (await f.inbox("receiver_read", {cursor: page.next_cursor})).result as JsonObject;
  const native = second.native_child_results as JsonObject;
  assert.equal(native.scan_complete, true);
  assert.equal(native.next_cursor, null);
  const found = (native.items as JsonObject[]).find(row => row.result_id === last.result_id);
  assert.ok(found);
  assert.equal(found.text, target.text);
  assert.equal((await f.call("acknowledge", {result_id: last.result_id, turn_instance_id: "new-turn",
    disposition: "accepted", observed_at: at})).replay, false);
  assert.equal((await f.call("acknowledge", {result_id: last.result_id, turn_instance_id: "new-turn",
    disposition: "accepted", observed_at: at})).replay, true);
});
