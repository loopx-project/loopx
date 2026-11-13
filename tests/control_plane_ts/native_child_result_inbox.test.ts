import assert from "node:assert/strict";
import {readFile, rm, writeFile} from "node:fs/promises";
import {mkdtempSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {nativeChildResultInbox} from "../../loopx/control_plane/capabilities/native_child_result_inbox.ts";
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
  return {runtime_root, call, inbox};
}

test("a completed host result survives replay and enters the next Turn unread", async t => {
  const f = fixture(t), input = {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Independent review passed.", observed_at: at};
  const created = await f.call("ingest", input), replay = await f.call("ingest", input);
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
  const created = await f.call("ingest", input);
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
  await f.call("ingest", input);
  await assert.rejects(f.call("ingest", {...input, text: "Replacement"}), /conflicting content/);
  const [result] = (await f.call("list")).items as JsonObject[];
  assert.equal(result.text, "Original");
});

test("large result text stays in a private file while the inbox keeps a verified reference", async t => {
  const f = fixture(t), text = "结果".repeat(40_000);
  await f.call("ingest", {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text, observed_at: at});
  const [result] = (await f.call("list")).items as JsonObject[];
  assert.equal(result.text, undefined);
  assert.equal(await readFile(String(result.content_file), "utf8"), text);
  assert.match(String(result.content_sha256), /^sha256:[0-9a-f]{64}$/u);
});

test("corrupt content stays visibly unavailable and cannot starve later results", async t => {
  const f = fixture(t), text = "a".repeat(40_000);
  const first = await f.call("ingest", {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text, observed_at: at});
  for (let index = 2; index <= 22; index += 1) {
    await f.call("ingest", {turn_instance_id: `turn-${index}`, operation_id: `operation-${index}`,
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
  await f.call("ingest", {turn_instance_id: "turn-1", operation_id: "operation-1",
    outcome: "completed", text: "Scoped", observed_at: at});
  assert.deepEqual((await nativeChildResultInbox({operation: "list", runtime_root: f.runtime_root,
    ...base, agent_id: "another"})).items, []);
  assert.deepEqual((await nativeChildResultInbox({operation: "list", runtime_root: f.runtime_root,
    ...base, goal_ref: {...goalRef, goal_instance_id: "ginst_" + "b".repeat(32)}})).items, []);
});
