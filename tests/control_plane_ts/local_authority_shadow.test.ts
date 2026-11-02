import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {localAuthorityShadowHeadDigest, localAuthorityShadowPartitionDigest,
  readLocalAuthorityShadow} from "../../loopx/control_plane/coordination/local_authority_shadow.ts";

test("runtime shadow parity ignores only the resume evaluation observation clock", () => {
  const head = {
    handoff_mode: "hard_lease",
    todos: [{
      todo_id: "todo-a",
      resume_ready: false,
      resume_condition: {
        evaluated_at: "2026-09-20T00:00:00Z",
        satisfied: false,
        availability_reason: "resume_condition_pending",
      },
    }],
    leases: [],
  };
  const laterObservation = structuredClone(head);
  laterObservation.todos[0]!.resume_condition.evaluated_at = "2026-09-21T00:00:00Z";

  assert.equal(
    localAuthorityShadowHeadDigest(head),
    localAuthorityShadowHeadDigest(laterObservation),
  );
  assert.equal(
    localAuthorityShadowPartitionDigest("todos", {
      handoff_mode: head.handoff_mode,
      todos: head.todos,
    }),
    localAuthorityShadowPartitionDigest("todos", {
      handoff_mode: laterObservation.handoff_mode,
      todos: laterObservation.todos,
    }),
  );

  const changedDecision = structuredClone(laterObservation);
  changedDecision.todos[0]!.resume_condition.satisfied = true;
  assert.notEqual(
    localAuthorityShadowHeadDigest(head),
    localAuthorityShadowHeadDigest(changedDecision),
  );
  assert.notEqual(
    localAuthorityShadowPartitionDigest("todos", {
      handoff_mode: head.handoff_mode,
      todos: head.todos,
    }),
    localAuthorityShadowPartitionDigest("todos", {
      handoff_mode: changedDecision.handoff_mode,
      todos: changedDecision.todos,
    }),
  );
});


test("retired record RPC rejects even a stale client without creating any storage", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-retired-observer-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const handlers = createEffectRuntimeHandlers({fingerprint: "test", requestShutdown: () => undefined});
  for (const request of [{}, {schema_version: "loopx_local_authority_shadow_request_v0",
    mode: "file_one_way", runtime_root: root, goal_id: "goal-a", observation_id: "old-op",
    source_digest: `sha256:${"a".repeat(64)}`, observation_trigger: "todo_update", source_projection: {}}]) {
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "coordination.local_authority_shadow.record", request),
      {code: "unsupported_method"});
  }
  assert.deepEqual(await readdir(root), []);
});

test("historical observation remains readable but is not a runtime capture lineage", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-retained-observer-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const store = new FileAuthorityStore(join(root, "authority-shadow", "file", "goal-a"), "goal-a");
  const head = {schema_version: "loopx_local_authority_shadow_projection_v0", goal_id: "goal-a",
    handoff_mode: "legacy", todos: [], leases: []};
  assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "old-op",
    events: [], receipts: [{observation_id: "old-op"}], next_projection: head})).status, "applied");
  const before = await store.loadAuthority();
  const view = await readLocalAuthorityShadow({schema_version: "loopx_coordination_runtime_shadow_outbox_read_v0",
    runtime_root: root, goal_id: "goal-a", store_kind: "legacy_observation"});
  assert.equal(view.status, "loaded");
  assert.deepEqual(view.head, head);
  assert.deepEqual(await store.loadAuthority(), before);
  assert.deepEqual(await readdir(join(root, "authority-shadow")), ["file"]);
});
