import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {mkdtemp, readdir, rm, stat} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {readAndProjectInteractionWorkContext, projectInteractionWorkContext}
  from "../../loopx/control_plane/work_items/interaction_contract.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {engageCanonicalRoute} from "./local_promotion_fixture.ts";

const goal = "context-read";
const text = "Keep complete requirements. ".repeat(60) + "Stop before deployment.";
const revision = "sha256:" + createHash("sha256").update(text.trim()).digest("hex");

function request(root: string): JsonObject {
  return {required_reads: [{source: "selected_todo", command: "read-selected"}], source_results: [],
    selected_todo: {todo_id: "todo_selected", status: "open", claimed_by: "agent-a",
      text: "Keep complete requirements.", content_revision: revision,
      _context_text_sha256: createHash("sha256").update(text).digest("hex")},
    source_context: {runtime_root: root, goal_id: goal}};
}

for (const state of ["active", "archive", "missing", "changed", "invalid_read_model"] as const) {
  test(`canonical selected context: ${state}`, async t => {
    const root = await mkdtemp(join(tmpdir(), "selected-context-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    await engageCanonicalRoute(root, goal);
    const store = new FileAuthorityStore(join(root, "authority", "file-v0"), goal);
    const todo = {todo_id: "todo_selected", role: "agent", status: "open", done: false,
      text: state === "changed" ? text + " Changed requirement." : text,
      archive_state: state === "archive" ? "archive" : "active", task_class: "advancement_task",
      claimed_by: "agent-a", note: "Retain this independent note."};
    const projection = authorityProjectionFixture(goal, state === "missing" ? [] : [todo]);
    if (state === "invalid_read_model") (projection.todo_read_model as JsonObject).records_sha256 = "0".repeat(64);
    const seeded = await store.commitAuthority({operation_id: "seed-context", expected_provider_revision: null,
      next_projection: projection,
      events: [], receipts: []});
    assert.equal(seeded.status, "applied");
    const before = await store.loadAuthority();
    const input = request(root), original = structuredClone(input);
    const result = await readAndProjectInteractionWorkContext(input);
    const context = result.work_context as JsonObject;
    assert.equal(context.complete, state === "active");
    if (state === "active") {
      const content = ((context.sources as JsonObject[])[0].content as JsonObject);
      assert.equal((content.todo as JsonObject).text, text);
      assert.equal((content.todo as JsonObject).note, todo.note);
      assert.equal((content.todo as JsonObject).content_revision, revision);
      assert.equal((content.authority_read as JsonObject).provider_revision, seeded.provider_revision);
      assert.equal(context.selected_todo_text_verified, true);
    } else {
      assert.equal((context.failures as JsonObject[])[0].error_code, "context_source_unavailable");
    }
    assert.deepEqual(await store.loadAuthority(), before);
    assert.deepEqual(input, original);
  });
}

test("context without a selected source performs no provider read", async t => {
  const directory = await mkdtemp(join(tmpdir(), "context-no-read-"));
  t.after(() => rm(directory, {recursive: true, force: true}));
  const root = join(directory, "absent-runtime");
  const input = {...request(root), required_reads: []};
  assert.deepEqual(await readAndProjectInteractionWorkContext(input), projectInteractionWorkContext(input));
  await assert.rejects(stat(root), {code: "ENOENT"});
});

test("an unavailable canonical store is not recreated by context reading", async t => {
  const root = await mkdtemp(join(tmpdir(), "context-missing-store-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  await engageCanonicalRoute(root, goal);
  const before = await readdir(root, {recursive: true});
  const result = await readAndProjectInteractionWorkContext(request(root));
  assert.equal((result.work_context as JsonObject).complete, false);
  assert.deepEqual(await readdir(root, {recursive: true}), before);
});
