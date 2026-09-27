import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

async function head(store: AuthorityStore) {
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") throw new Error("binding fixture missing");
  return loaded;
}

export function registerCompletionValidationBindingConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  for (const schema of ["legacy", "native"] as const) {
    test(`${provider}: first validator binding on ${schema} work preserves fencing and durable replay`, async t => {
      const {store, contender} = await factory(t);
      const goal = "validator-binding";
      const work = {todo_id: "todo_target", role: "agent", status: "open", done: false,
        archive_state: "active", text: "Synthetic independently checked work", claimed_by: "agent-a"};
      const leases = [{todo_id: work.todo_id, owner: "agent-a", status: "active", version: 3,
        lease_epoch: 2, idempotency_key: "execute", expires_at: "2026-09-28T00:00:00Z", write_scopes: []}];
      await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        next_projection: authorityProjectionFixture(goal, [work], leases, schema, {handoff_mode: "hard_lease"}),
        events: [], receipts: []});
      const before = await head(store);
      const declaration = {validation_command: null, validation_command_argv: ["false"],
        validation_label: "Independent check", validation_timeout_seconds: 5};
      const request = {goal_id: goal, todo_id: work.todo_id, expected_role: "agent", actor_agent_id: "agent-a",
        registered_agents: ["agent-a", "agent-b"], operation_id: "bind", expected_provider_revision: before.provider_revision,
        patch: {}, clear_fields: [], lease_idempotency_key: "execute", lease_expected_version: 3,
        completion_validation_revision: {schema_version: "loopx_todo_completion_validation_revision_v1" as const,
          expected_declaration_sha256: null, declaration}, dry_run: false, now: new Date("2026-09-27T00:00:00Z")};
      for (const patch of [{actor_agent_id: "agent-b"}, {lease_expected_version: 2},
        {lease_idempotency_key: "wrong"}, {now: new Date("2026-09-29T00:00:00Z")}]) {
        assert.equal((await executeCoordinationTodoUpdate(store, {...request, ...patch})).status, "failed");
        assert.deepEqual(await head(store), before);
        assert.equal((await store.readReceipt("bind")).status, "missing");
      }
      assert.equal((await executeCoordinationTodoUpdate(store, {...request, dry_run: true})).status, "planned");
      assert.deepEqual(await head(store), before);
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "applied");
      const bound = await head(store);
      const target = (bound.head.todos as JsonObject[])[0]!;
      assert.equal(target.completion_validation_sha256, canonicalAuthoritySha256(declaration));
      assert.equal(target.status, "open");
      assert.deepEqual(bound.head.leases, leases);
      assert.equal((target.completion_validation_revision_history as JsonObject[])[0]!.previous_declaration_sha256, null);
      const immutable = await store.readReceipt("bind");
      assert.equal((await executeCoordinationTodoUpdate(contender, {...request, operation_id: "stale-bind"})).reason_code,
        "provider_revision_mismatch");
      assert.equal((await executeCoordinationTodoUpdate(contender, {...request, operation_id: "already-bound",
        expected_provider_revision: bound.provider_revision})).status, "failed");
      const replacement = {...declaration, validation_command_argv: ["true"]};
      assert.equal((await executeCoordinationTodoUpdate(contender, {...request, operation_id: "replace",
        expected_provider_revision: bound.provider_revision,
        completion_validation_revision: {schema_version: "loopx_todo_completion_validation_revision_v0",
          expected_declaration_sha256: canonicalAuthoritySha256(declaration), declaration: replacement}})).status, "applied");
      const latest = await head(store);
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
      assert.deepEqual(await head(store), latest, "old replay must not restore a subsequently replaced validator");
      assert.deepEqual(await store.readReceipt("bind"), immutable);
      assert.equal((await executeCoordinationTodoUpdate(store, {...request,
        completion_validation_revision: {...request.completion_validation_revision, declaration: replacement}})).reason_code,
        "coordination_operation_identity_mismatch");
    });
  }
}
