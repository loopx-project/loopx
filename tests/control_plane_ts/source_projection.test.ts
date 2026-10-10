import assert from "node:assert/strict";
import {readFile, symlink} from "node:fs/promises";
import {join} from "node:path";
import test from "node:test";
import {projectCoordinationSource, SOURCE_PROJECTION_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/source_projection.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";

// Frozen from the persisted pre-extension contract, not from today's field
// list: deriving the shape by filtering the current manifest would silently
// re-point this test at whichever release is current.
const historicalManifest = JSON.parse(await readFile(new URL(
  "../fixtures/coordination/todo-pre-validator-revision-fields.json", import.meta.url,
), "utf8"));

const todo = {schema_version: "todo_item_v0", todo_id: "a", role: "agent", status: "open",
  done: false, text: "Capture exact state", archive_state: "active", source_section: "Agent Todo"};
function request(extra: Record<string, unknown> = {}) {
  return {schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA, kind: "snapshot", goal_id: "goal",
    handoff_mode: "hard_lease", read_model_schema: "loopx_todo_canonical_read_record_v0", todos: [todo], leases: [], ...extra};
}

test("capture preserves current lease watermarks, archived facts and exact consumer digest", () => {
  const archived = {...todo, todo_id: "archived", status: "done", done: true, archive_state: "archive"};
  const current = {todo_id: "a", goal_id: "goal", status: "released", version: 7, lease_epoch: 4};
  const input = request({todos: [archived, {...todo, succession_evaluation: {transient: true}}],
    leases: [{...current, todo_id: "gone"}, {...current, todo_id: "archived"}, current]});
  const before = structuredClone(input);
  const output = projectCoordinationSource(input).projection as Record<string, unknown>;
  assert.deepEqual(input, before);
  assert.deepEqual(output.todos, [todo, archived]);
  assert.deepEqual(output.leases, [current]);
  const model = output.todo_read_model as Record<string, unknown>;
  assert.equal(model.todo_count, 2);
  assert.equal(model.records_sha256, canonicalAuthoritySha256([todo, archived]));
  const partition = projectCoordinationSource({schema_version: SOURCE_PROJECTION_REQUEST_SCHEMA,
    kind: "todo_partition", handoff_mode: "hard_lease", todos: [archived, todo]}).projection;
  assert.deepEqual(partition, {handoff_mode: "hard_lease", todos: [todo, archived]});
});

for (const [label, patch, message] of [
  ["missing collection", {todos: null}, /must be an array/],
  ["non-record Todo", {todos: [todo, null]}, /must be an object/],
  ["missing Todo identity", {todos: [todo, {}]}, /todo_id/],
  ["duplicate Todo", {todos: [todo, {...todo, archive_state: "archive"}]}, /duplicate todos/],
  ["invalid handoff", {handoff_mode: "automatic"}, /handoff mode/],
  ["non-record lease", {leases: [null]}, /must be an object/],
  ["missing lease identity", {leases: [{}]}, /todo_id/],
  ["duplicate retired lease", {leases: [{todo_id: "gone"}, {todo_id: "gone"}]}, /duplicate leases/],
  ["foreign retired lease", {leases: [{todo_id: "gone", goal_id: "other"}]}, /another Goal/],
  ["unknown Todo authority", {todos: [{...todo, future_authority: true}]}, /unversioned fields/],
  ["unsafe integer", {leases: [{todo_id: "a", version: 2 ** 53}]}, /safe integer/],
  ["fractional observation", {leases: [{todo_id: "a", version: 1.5}]}, /safe integer/],
] as const) test(`capture rejects ${label} without manufacturing an empty source`, () => {
  assert.throws(() => projectCoordinationSource(request(patch)), message);
});

test("source union rejects snapshot fields on a partition and omitted snapshot members", () => {
  assert.throws(() => projectCoordinationSource(request({kind: "todo_partition"})), /fields mismatch/);
  const {leases: _leases, ...partial} = request();
  assert.throws(() => projectCoordinationSource(partial), /fields mismatch/);
  assert.deepEqual((projectCoordinationSource(request({todos: []})).projection as Record<string, unknown>).todos, []);
});


test("native records preserve the declared manifest across historical capture folds", () => {
  const {source_section: _section, ...domain} = todo;
  const native = {...domain, schema_version: "todo_domain_record_v0"};
  const readSchema = "loopx_todo_domain_read_record_v0";
  for (const todos of [[], [native]]) {
    const output = projectCoordinationSource(request({todos, read_model_schema: readSchema})).projection as Record<string, unknown>;
    assert.deepEqual(output.todos, todos);
    assert.equal((output.todo_read_model as Record<string, unknown>).schema_version, readSchema);
  }
  // Existing shadow folds use a legacy manifest over native domain records.
  // Each record's own schema remains authoritative; preserve that compatibility.
  const captured = projectCoordinationSource(request({todos: [native]})).projection as Record<string, unknown>;
  assert.deepEqual(captured.todos, [native]);
  assert.equal((captured.todo_read_model as Record<string, unknown>).schema_version, "loopx_todo_canonical_read_record_v0");
});


test("source revalidation preserves the supported pre-validator manifest and rejects corrupt digests", async (t) => {
  const {fixture, sourceRequest, projection, todo: legacyTodo} = await import("./shadow_file_fixture.ts");
  const {verifyShadowSourceSnapshot} = await import("../../loopx/control_plane/coordination/runtime_shadow.ts");
  const source = await fixture(t);
  const head = projection([legacyTodo()]);
  const manifest = head.todo_read_model as Record<string, unknown>;
  manifest.contract_fields = historicalManifest.canonical_fields;
  const before = structuredClone(head);
  const request = await sourceRequest(source, head);
  await verifyShadowSourceSnapshot(request as import("../../loopx/control_plane/coordination/runtime_shadow.ts").ShadowRequest);
  assert.deepEqual(head, before);
  manifest.records_sha256 = "0".repeat(64);
  await assert.rejects(verifyShadowSourceSnapshot(await sourceRequest(source, head) as import("../../loopx/control_plane/coordination/runtime_shadow.ts").ShadowRequest), /digest mismatch/);
});

test("source runtime aliases preserve the snapshot in either transport direction", async t => {
  const {fixture, sourceRequest} = await import("./shadow_file_fixture.ts");
  const {verifyShadowSourceSnapshot} = await import("../../loopx/control_plane/coordination/runtime_shadow.ts");
  const source = await fixture(t);
  const alias = join(source.root, "runtime-alias");
  await symlink(source.root, alias, "junction");
  for (const registeredAlias of [true, false]) {
    const request = await sourceRequest(source, source.baseline);
    if (registeredAlias) (request.source_snapshot as Record<string, unknown>).registered_runtime_root = alias;
    else request.runtime_root = alias;
    const before = structuredClone(request);
    await verifyShadowSourceSnapshot(request as import("../../loopx/control_plane/coordination/runtime_shadow.ts").ShadowRequest);
    assert.deepEqual(request, before, "directory equivalence must not rewrite retained source facts");
  }
});
