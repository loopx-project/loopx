import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, readdir, rm} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {agentPreferences, executeAgentPreferences} from "../../loopx/control_plane/capabilities/agent_preferences.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const request: JsonObject = {schema_version: "agent_preferences_request_v1", goal_state_ref: "fixture",
  goal_id: "goal-a", agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], action: "read"};
const update: JsonObject = {action: "remember", key: "review.collaboration", statement: "Ask the designated reviewer before merging.",
  source_kind: "user_instruction", source_ref: "owner-message-1", source_quote: "Use the designated reviewer for my changes.",
  expected_revision: null, operation_id: "remember-1", execute: true};
function view(result: JsonObject): JsonObject { return result.current as JsonObject; }
function items(result: JsonObject): JsonObject[] { return view(result).items as JsonObject[]; }

for (const [name, Store] of [["file", FileAuthorityStore], ["sqlite", SqliteAuthorityStore]] as const) {
  test(`${name}: correction, retirement, historical replay and fresh handles preserve current truth`, async t => {
    const dir = await mkdtemp(join(tmpdir(), "preferences-"));
    t.after(() => rm(dir, {recursive: true, force: true}));
    const call = (fields: JsonObject = {}) => executeAgentPreferences({...request, ...fields}, new Store(dir, "preferences"));
    assert.deepEqual(items(await call()), []);
    assert.deepEqual(await call({action: "observe"}), {ok: true, status: "absent", observation_count: 0});
    const added = await call(update);
    assert.equal(added.status, "applied", JSON.stringify(added));
    assert.equal(items(await call())[0]!.statement, update.statement);
    const changed = await call({...update, expected_revision: view(added).revision,
      operation_id: "correct-2", statement: "Do not contact a reviewer.", source_ref: "owner-message-2", source_quote: "Stop asking a reviewer."});
    assert.equal(changed.status, "applied");
    const replay = await call(update);
    assert.equal(replay.status, "replayed");
    assert.equal(items(replay)[0]!.statement, "Do not contact a reviewer.");
    assert.equal((await call({...update, operation_id: "stale-new"})).status, "revision_conflict");
    assert.equal((await call({...update, statement: "Merge without checking."})).status, "operation_conflict");
    const retired = await call({...update, action: "retire", operation_id: "retire-3", expected_revision: view(changed).revision,
      source_ref: "owner-message-3", source_quote: "Forget my review preference."});
    assert.equal(items(retired)[0]!.state, "retired");
    assert.equal(items(await call(update))[0]!.statement, null);
    assert.equal(items(await call())[0]!.state, "retired");
    assert.deepEqual(await call({action: "observe"}), {ok: true, status: "observed", observation_count: 1});
    const history = await call({action: "history"});
    assert.equal((history.events as JsonObject[]).length, 3);
    assert.equal((history.events as JsonObject[])[0]!.statement, update.statement);
    assert.equal(view(retired).authority, "advisory_only");
    await assert.rejects(call({agent_id: "agent-b"}), /scope/);
  });
  test(`${name}: two writers cannot overwrite the same observed revision`, async t => {
    const dir = await mkdtemp(join(tmpdir(), "preferences-race-"));
    t.after(() => rm(dir, {recursive: true, force: true}));
    const call = (fields: JsonObject) => executeAgentPreferences({...request, ...fields}, new Store(dir, "preferences"));
    const first = await call(update);
    const results = await Promise.all(["left", "right"].map(key => call({...update, key,
      expected_revision: view(first).revision, operation_id: key})));
    assert.equal(results.filter(r => r.ok === true).length, 1);
    assert.equal(items(await call({})).length, 2);
  });
}

test("read/preview are side-effect free; scope isolation, expiry and input bounds fail explicitly", async t => {
  const root = await mkdtemp(join(tmpdir(), "preferences-local-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const call = (fields: JsonObject = {}) => agentPreferences({...request, runtime_root: root, ...fields});
  assert.deepEqual(items(await call()), []);
  assert.equal((await call({...update, execute: false})).status, "preview");
  assert.deepEqual(await readdir(root), []);
  await assert.rejects(call({...update, source_kind: "retrieved_lesson"}), /explicit user/);
  await assert.rejects(call({...update, agent_id: "unknown"}), /registered/);
  await assert.rejects(call({...update, action: "retire"}), /existing/);
  await assert.rejects(call({...update, statement: "x".repeat(1201)}), /bound/);
  await assert.rejects(call({...update, expires_at: "2020-01-01T00:00:00Z"}), /future/);
  const added = await call(update);
  for (const fields of [{agent_id: "agent-b"}, {goal_id: "goal-b"}, {goal_state_ref: "other"}, {goal_instance_id: "replacement-goal"}]) {
    assert.deepEqual(items(await call(fields)), []);
    assert.deepEqual(await call({...fields, action: "observe"}), {ok: true, status: "absent", observation_count: 0});
  }
  const store = new FileAuthorityStore(join(root, "expiry"), "preferences");
  await executeAgentPreferences({...request, ...update, expires_at: "2030-01-02T00:00:00Z"}, store, "2030-01-01T00:00:00Z");
  const expired = await executeAgentPreferences(request, store, "2030-01-02T00:00:00Z");
  assert.equal(items(expired)[0]!.state, "expired");
  assert.equal(items(expired)[0]!.statement, null);
});

test("bounded context rejects overflow rather than evicting a constraint; malformed dates fail", async t => {
  const dir = await mkdtemp(join(tmpdir(), "preferences-bound-"));
  t.after(() => rm(dir, {recursive: true, force: true}));
  const store = new FileAuthorityStore(dir, "preferences");
  await assert.rejects(executeAgentPreferences({...request, ...update, expires_at: "2030-02-30T00:00:00Z"}, store), /calendar/);
  // Independent valid boundary fixture: do not derive capacity from implementation.
  const records = Array.from({length: 64}, (_, i) => ({key: `topic-${i}`, state: "active", statement: "Keep the stated constraint.",
    source_kind: "user_instruction", source_ref: "user-message", source_quote: "Keep this.",
    recorded_at: "2026-01-01T00:00:00.000Z", expires_at: null, operation_id: `op-${i}`, supersedes_operation_id: null}));
  const seeded = await store.commitAuthority({expected_provider_revision: null, operation_id: "seed",
    events: [], receipts: [], next_projection: {schema_version: "agent_preferences_v1",
      scope: {goal_state_ref: "fixture", goal_instance_id: null, goal_id: "goal-a", agent_id: "agent-a"}, records}});
  assert.equal(seeded.status, "applied");
  const before = await executeAgentPreferences(request, store);
  await assert.rejects(executeAgentPreferences({...request, ...update, expected_revision: view(before).revision}, store), /capacity/);
  assert.deepEqual(await executeAgentPreferences(request, store), before);
});

test("read with an irrelevant execute flag cannot recreate missing store identity", async t => {
  const root = await mkdtemp(join(tmpdir(), "preferences-readonly-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const call = (fields: JsonObject) => agentPreferences({...request, runtime_root: root, ...fields});
  assert.equal((await call(update)).status, "applied");
  const namespace = join(root, "agent-preferences");
  const directory = join(namespace, (await readdir(namespace))[0]!);
  await rm(join(directory, "store-identity"));
  const read = await call({execute: true});
  assert.equal(read.status, "unavailable");
  assert.equal((await readdir(directory)).includes("store-identity"), false);
});
