import assert from "node:assert/strict";
import test from "node:test";
import {mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync} from "node:fs";
import {join} from "node:path";
import {tmpdir} from "node:os";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {readCollaborationInbox} from "../../loopx/control_plane/collaboration/inbox_read.ts";

const ref = {goal_id: "research", goal_instance_id: "ginst_" + "a".repeat(32)};
// Addresses captured from the shipped Python storage codec, including its spaces.
const targets = {
  legacy: "433a92de158f221e8f912110b3736ae8aca7f605fc96be50019e32311407e69b",
  exact: "f1f1d6040c09e2d54ce7ba2d6ec93659c1bfdf365a2b072bf40d38da0a8e52d8",
};
function fixture(t: test.TestContext, profile: keyof typeof targets = "exact") {
  const root = mkdtempSync(join(tmpdir(), "loopx-inbox-来源-"));
  t.after(() => rmSync(root, {recursive: true, force: true}));
  const base = join(root, ".local", "manager-context");
  const folder = join(base, "entries", targets[profile]);
  mkdirSync(folder, {recursive: true});
  const scope = {profile_id: profile === "exact" ? "source_session_v1" : null,
    caller_goal_ref: profile === "exact" ? ref : null, current_goal_ref: profile === "exact" ? ref : null};
  const row = (index: number, extra: JsonObject = {}) => ({
    schema_version: profile === "exact" ? "loopx_manager_context_entry_v2" : "loopx_manager_context_entry_v1",
    goal_id: "research", agent_id: "worker", request_id: index.toString(16).padStart(64, "0"),
    ...(profile === "exact" ? {goal_ref: ref} : {}), source_id: "source:" + index,
    message: "Inspect the current artifact. " + "x".repeat(300) + "PRIVATE_LONG_SOURCE",
    ...extra,
  });
  const publish = (index: number, extra: JsonObject = {}) => {
    const request = row(index, extra);
    writeFileSync(join(folder, request.request_id + ".json"), JSON.stringify(request));
    return request;
  };
  const call = (operation: string, extra: JsonObject = {}) => readCollaborationInbox({runtime_root: root,
    scope, goal_id: "research", agent_id: "worker", operation,
    observed_at: "2026-10-19T00:00:00.000000+00:00", read_command: "loopx manager-inbox read", ...extra});
  return {root, base, folder, row, publish, call, scope};
}
function sourceBytes(folder: string): [string, string][] {
  return readdirSync(folder).sort().map(name => [name, readFileSync(join(folder, name)).toString("hex")]);
}

for (const profile of ["legacy", "exact"] as const) {
  test(`${profile}: Turn observation retains full counts without writing or exposing long source`, async t => {
    const f = fixture(t, profile);
    for (let n = 1; n <= 128; n++) f.publish(n);
    const before = sourceBytes(f.folder), observed = await f.call("turn_start");
    const page = observed.result as JsonObject;
    assert.equal((page.items as unknown[]).length, 20);
    assert.equal(page.has_more, true);
    const context = (observed.context as JsonObject).unfinished_delegations as JsonObject;
    assert.equal(context.incoming_count, 128);
    assert.equal(context.omitted_count, 108);
    assert.equal(context.scan_complete, true);
    assert.equal(JSON.stringify(context).includes("PRIVATE_LONG_SOURCE"), false);
    assert.deepEqual(sourceBytes(f.folder), before);
    assert.deepEqual(readdirSync(f.base).sort(), ["entries"]);
  });

  test(`${profile}: a completed receiver page records only its supplied requests and replays without adoption`, async t => {
    const f = fixture(t, profile);
    for (let n = 1; n <= 26; n++) f.publish(n);
    const source = sourceBytes(f.folder), first = await f.call("receiver_read");
    assert.equal(first.completed, true);
    assert.equal((first.followthrough as JsonObject[])[0].step, "assess_request");
    const reads = join(f.base, "reads"), before = sourceBytes(reads).filter(([name]) => name.endsWith(".json"));
    assert.equal(before.length, 20);
    for (const [name] of before) assert.equal(JSON.parse(readFileSync(join(reads, name), "utf8")).kind, "receiver_cli_read");
    const repeated = await f.call("receiver_read");
    assert.equal((repeated.result as JsonObject).has_more, true);
    assert.deepEqual(sourceBytes(reads).filter(([name]) => name.endsWith(".json")), before);
    assert.deepEqual(sourceBytes(f.folder), source);
    assert.equal((repeated.context as JsonObject).read_complete, false);
  });
}

test("a later damaged entry cannot block the complete current page", async t => {
  const f = fixture(t);
  for (let n = 1; n <= 21; n++) f.publish(n);
  writeFileSync(join(f.folder, "f".repeat(64) + ".json"), "{broken");
  const first = (await f.call("pending")).result as JsonObject;
  assert.equal((first.items as unknown[]).length, 20);
  await assert.rejects(f.call("pending", {cursor: first.next_cursor}), /invalid manager context record/);
  const context = (await f.call("turn_context")).unfinished_delegations as JsonObject;
  assert.equal(context.incoming_count, 21);
  assert.equal(context.unavailable_count, 1);
  assert.equal(context.scan_complete, false);
});

test("named reads never fall back to another page or record unrelated requests", async t => {
  const f = fixture(t), original = f.publish(1), other = f.publish(2);
  const result = await f.call("receiver_read", {request_id: original.request_id});
  assert.equal((result.result as JsonObject).request_id, original.request_id);
  assert.equal((result.result as JsonObject).peer_returns, undefined);
  assert.equal(result.context, undefined);
  assert.deepEqual(readdirSync(join(f.base, "reads")).filter(name => name.endsWith(".json")), [original.request_id + ".json"]);
  await assert.rejects(f.call("receiver_read", {request_id: other.request_id, cursor: "invalid"}), /cannot use inbox cursors/);
  await assert.rejects(f.call("receiver_read", {request_id: "f".repeat(64)}), /not found/);
  assert.deepEqual(readdirSync(join(f.base, "reads")).filter(name => name.endsWith(".json")), [original.request_id + ".json"]);
});

test("a stale Goal instance cannot acknowledge an old request", async t => {
  const f = fixture(t), request = f.publish(1), before = sourceBytes(f.folder);
  await assert.rejects(f.call("record_read", {items: [request], deliveries: [],
    scope: {...f.scope, current_goal_ref: {...ref, goal_instance_id: "ginst_" + "b".repeat(32)}}}), /lifecycle rejected/);
  assert.deepEqual(sourceBytes(f.folder), before);
  assert.deepEqual(readdirSync(f.base), ["entries"]);
});

for (const field of ["goal_id", "agent_id"]) {
  test(`a supplied request cannot change its ${field} while reusing an existing id`, async t => {
    const f = fixture(t, "legacy"), row = f.publish(1);
    await assert.rejects(f.call("record_read", {items: [{...row, [field]: "other"}], deliveries: []}), /scope mismatch/);
    assert.deepEqual(readdirSync(f.base), ["entries"]);
  });
}

test("input-provider work defers receipts until finalization", async t => {
  const f = fixture(t), request = f.publish(1, {brief: {inputs: [{ref: "artifact.txt"}]}});
  const prepared = await f.call("receiver_read");
  assert.equal(prepared.completed, undefined);
  assert.deepEqual(readdirSync(f.base), ["entries"]);
  const final = await f.call("record_read", {items: [request], deliveries: prepared.deliveries,
    work_observations: [{kind: "pending", recorded_decision: null, linked_todos: [], evidence_refs: [], evidence_unavailable: false}],
    include_context: true});
  assert.equal((final.followthrough as JsonObject[])[0].step, "assess_request");
  assert.equal(((final.context as JsonObject).unfinished_delegations as JsonObject).incoming_count, 1);
  assert.equal(JSON.parse(readFileSync(join(f.base, "reads", request.request_id + ".json"), "utf8")).kind, "receiver_cli_read");
});

for (const bytes of [Buffer.from("\ufeff{}"), Buffer.from([0xff]), Buffer.alloc(128_001, 32)]) {
  test(`unreadable record bytes remain visible as incomplete evidence (${bytes.length})`, async t => {
    const f = fixture(t), request = f.publish(1);
    writeFileSync(join(f.folder, request.request_id + ".json"), bytes);
    await assert.rejects(f.call("receiver_read"), /manager context record/);
    const context = (await f.call("turn_context")).unfinished_delegations as JsonObject;
    assert.equal(context.unavailable_count, 1);
    assert.equal(context.scan_complete, false);
    assert.deepEqual(readdirSync(f.base), ["entries"]);
  });
}

test("a valid record at the byte limit retains its complete content", async t => {
  const f = fixture(t), request = f.publish(1);
  const path = join(f.folder, request.request_id + ".json");
  const original = readFileSync(path);
  writeFileSync(path, Buffer.concat([original, Buffer.alloc(128_000 - original.length, 32)]));
  const result = (await f.call("pending", {request_id: request.request_id})).result as JsonObject;
  assert.equal((result.items as JsonObject[])[0].message, request.message);
});

test("malformed receipts retain the request and recovery advice", async t => {
  const f = fixture(t), request = f.publish(1);
  mkdirSync(join(f.base, "decisions"));
  writeFileSync(join(f.base, "decisions", request.request_id + ".json"), "{broken");
  const response = await f.call("receiver_read"), item = ((response.result as JsonObject).items as JsonObject[])[0];
  assert.equal(item.inbox_state, "receipt_unavailable");
  assert.equal(item.recorded_decision, null);
  assert.equal((response.followthrough as JsonObject[])[0].step, "recover_evidence");
  assert.equal(readFileSync(join(f.base, "decisions", request.request_id + ".json"), "utf8"), "{broken");
});

test("unreadable source content is incomplete evidence rather than coerced prose", async t => {
  const f = fixture(t);
  f.publish(1, {message: {text: "unreadable"}});
  await assert.rejects(f.call("receiver_read"), /invalid Inbox source content/);
  const context = (await f.call("turn_context")).unfinished_delegations as JsonObject;
  assert.equal(context.scan_complete, false);
  assert.equal(context.unavailable_count, 1);
  assert.deepEqual(context.items, []);
  assert.deepEqual(readdirSync(f.base), ["entries"]);
});

for (const status of [null, ["queued"]]) {
test(`malformed delivery status retains its recovery distinction (${JSON.stringify(status)})`, async t => {
  const f = fixture(t), request = f.publish(1), folder = join(f.base, "replies", request.request_id);
  mkdirSync(folder, {recursive: true});
  mkdirSync(join(f.base, "decisions"));
  const identity = Object.fromEntries(["request_id", "goal_id", "agent_id", "source_id", "goal_ref"].map(key => [key, request[key as keyof typeof request]]));
  writeFileSync(join(f.base, "decisions", request.request_id + ".json"), JSON.stringify({...identity, decision: "adopt"}));
  writeFileSync(join(folder, "conclusion.json"), JSON.stringify({...identity, phase: "conclusion", decision: "adopt", text: "Findings"}));
  writeFileSync(join(folder, "conclusion.delivery.json"), JSON.stringify({status}));
  const context = (await f.call("turn_context")).unfinished_delegations as JsonObject;
  const agenda = context.items as JsonObject[];
  if (status === null) {
    assert.equal((agenda[0].returns as JsonObject[])[0].status, "explicit_unverified");
    assert.equal(agenda[0].step, "await_return_delivery");
  } else assert.equal(agenda[0].step, "recover_evidence");
});
}

test("a legal combined Inbox and peer-return page retains content beyond the inline wire budget", async t => {
  const f = fixture(t), message = "x".repeat(50_000), text = "😀".repeat(20_000);
  const operations = join(f.base, "peer-operations", targets.exact);
  const receiver = join(f.base, "entries", "5ab0deb53efce4b1d0cf830afb67ce662e0e9aff3127e2424a4ce768212b5586");
  for (const folder of [operations, receiver, join(f.base, "decisions"), join(f.base, "roundtrips")]) mkdirSync(folder, {recursive: true});
  for (let n = 1; n <= 20; n++) {
    f.publish(n, {message});
    const row = f.row(100 + n, {agent_id: "reviewer", source_kind: "peer", source_agent_id: "worker",
      brief: {schema_version: "collaboration_brief_v0", purpose: "Independent review", context: "Inspect the artifact",
        constraints: [], inputs: [], acceptance: ["Return findings"], return_requirement: "Reply to the requester"}});
    const identity = Object.fromEntries(["request_id", "goal_id", "agent_id", "source_id", "goal_ref"].map(key => [key, row[key as keyof typeof row]]));
    writeFileSync(join(receiver, row.request_id + ".json"), JSON.stringify(row));
    writeFileSync(join(operations, row.request_id + ".json"), JSON.stringify(row));
    writeFileSync(join(f.base, "roundtrips", row.request_id + ".json"), JSON.stringify(identity));
    writeFileSync(join(f.base, "decisions", row.request_id + ".json"), JSON.stringify({...identity, decision: "adopt"}));
    const replies = join(f.base, "replies", row.request_id);
    mkdirSync(replies, {recursive: true});
    writeFileSync(join(replies, "conclusion.json"), JSON.stringify({...identity, phase: "conclusion",
      decision: "adopt", text, created_at: "2026-10-19T00:00:00Z"}));
  }
  const observed = await f.call("receiver_read"), page = observed.result as JsonObject;
  assert.equal(observed.completed, true);
  assert.equal(Buffer.byteLength(JSON.stringify(observed)) > 2 * 1024 * 1024, true);
  assert.equal((page.items as JsonObject[]).length, 20);
  assert.equal(((page.peer_returns as JsonObject).items as JsonObject[]).length, 20);
  for (const item of page.items as JsonObject[]) assert.equal(item.message, message);
  for (const item of (page.peer_returns as JsonObject).items as JsonObject[]) assert.equal(item.text, text);
});

test("a large private agenda lets another runtime request finish before the scan completes", async t => {
  const large = fixture(t), other = fixture(t);
  for (let n = 1; n <= 256; n++) large.publish(n);
  let finished = false;
  const scanning = large.call("turn_context").then(context => {finished = true; return context;});
  const independent = await other.call("turn_context");
  assert.equal((independent.unfinished_delegations as JsonObject).incoming_count, 0);
  assert.equal(finished, false);
  assert.equal(((await scanning).unfinished_delegations as JsonObject).incoming_count, 256);
});

for (const profile of ["legacy", "exact"] as const) {
  test(`${profile}: a complete result chain preserves its remainder, delivery and corruption boundaries`, async t => {
    const f = fixture(t, profile), row = f.publish(1);
    const identity = Object.fromEntries(["request_id", "goal_id", "agent_id", "source_id", "goal_ref"]
      .filter(key => Object.hasOwn(row, key)).map(key => [key, row[key as keyof typeof row]]));
    const replies = join(f.base, "replies", row.request_id);
    for (const folder of [replies, join(f.base, "decisions"), join(f.base, "roundtrips")]) mkdirSync(folder, {recursive: true});
    writeFileSync(join(f.base, "decisions", row.request_id + ".json"), JSON.stringify({...identity, decision: "adopt"}));
    writeFileSync(join(f.base, "roundtrips", row.request_id + ".json"), JSON.stringify(identity));
    const remaining_work = {summary: "Validate the packaged repair", resume_when: "The isolated package is available"};
    const key = (n: number) => n ? "conclusion-" + String(n).padStart(8, "0") : "conclusion";
    for (let n = 0; n < 4; n++) {
      writeFileSync(join(replies, key(n) + ".json"), JSON.stringify({...identity, phase: "conclusion", decision: "adopt",
        text: "Recorded evidence " + n + "😀".repeat(2000), remaining_work,
        ...(n ? {result_key: key(n), previous_result_key: key(n - 1), update_id: "evidence-" + n} : {})}));
      writeFileSync(join(replies, key(n) + ".delivery.json"), JSON.stringify({status: "delivered"}));
    }
    const original = sourceBytes(replies);
    const pending = (await f.call("pending")).result as JsonObject;
    assert.deepEqual((pending.items as JsonObject[])[0].remaining_work, remaining_work);
    const agenda = (await f.call("turn_context")).unfinished_delegations as JsonObject;
    assert.equal(agenda.incoming_count, 1);
    assert.equal((agenda.items as JsonObject[])[0].step, "review_request_work");
    assert.deepEqual(sourceBytes(replies), original);

    const damaged = join(replies, key(2) + ".json"), bytes = readFileSync(damaged, "utf8");
    const corrupt = JSON.parse(bytes);
    corrupt.previous_result_key = "conclusion";
    writeFileSync(damaged, JSON.stringify(corrupt));
    const failed = (await f.call("pending")).result as JsonObject;
    assert.equal((failed.items as JsonObject[])[0].inbox_state, "receipt_unavailable");
    const unavailable = (await f.call("turn_context")).unfinished_delegations as JsonObject;
    assert.equal((unavailable.items as JsonObject[])[0].step, "recover_evidence");
    writeFileSync(damaged, bytes);
    assert.deepEqual(sourceBytes(replies), original);

    writeFileSync(join(replies, key(4) + ".json"), JSON.stringify({...identity, phase: "conclusion", decision: "adopt",
      text: "The requested package passed validation.", result_key: key(4), previous_result_key: key(3), update_id: "validated"}));
    const queued = (await f.call("turn_context")).unfinished_delegations as JsonObject;
    assert.equal((queued.items as JsonObject[])[0].step, "await_return_delivery");
    writeFileSync(join(replies, key(4) + ".delivery.json"), JSON.stringify({status: "delivered"}));
    assert.deepEqual(((await f.call("turn_context")).unfinished_delegations as JsonObject).items, []);
  });
}
