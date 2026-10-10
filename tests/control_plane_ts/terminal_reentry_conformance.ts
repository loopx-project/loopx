import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoTerminalLifecycle as execute,
  type CoordinationTodoTerminalLifecycleInput} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

export function registerTerminalReentryConformance(provider: string, factory: AuthorityStoreConformanceFactory): void {
  for (const schema of ["legacy", "native"] as const) {
    for (const validationRequired of [false, true]) {
    test(`${provider}: migrated terminal reentry preserves completion and closes only continuation (${schema}, validation=${validationRequired})`, async t => {
      const {store, contender} = await factory(t);
      const goal = "goal-a", key = `${goal}:agent-a:todo_migrated:original-turn`;
      const todo: JsonObject = {schema_version: schema === "native" ? "todo_domain_item_v0" : "todo_item_v0",
        todo_id: "todo_migrated", role: "agent", status: "done", done: true, archive_state: "active",
        text: "Retain previously validated work", claimed_by: "agent-a", evidence: "Original validation passed",
        completed_at: "2026-09-01T00:00:00Z", completion_turn_key: key,
        completion_receipt_id: "tcw_original", completion_continuation: "active_goal",
        completion_validation_required: validationRequired,
        ...(validationRequired ? {completion_validation_sha256: "a".repeat(64)} : {}),
        ...(schema === "legacy" ? {source_section: "Agent Todo"} : {})};
      await store.commitAuthority({operation_id: "migrated", expected_provider_revision: null,
        next_projection: authorityProjectionFixture(goal, [todo], [], schema, {handoff_mode: "hard_lease"}),
        events: [], receipts: [{original_completion: "immutable", original_quota_spend: 1}]});
      const request: CoordinationTodoTerminalLifecycleInput = {
        goal_id: goal, todo_id: String(todo.todo_id), expected_role: "agent", command: "complete",
        operation_identity: {kind: "explicit", operation_id: "old-adapter-ordinary-id"},
        actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], lifecycle_grants: [],
        authority_reason: null, decision_outcome: null, lease_idempotency_key: null, lease_expected_version: null,
        allow_user_gate_auto_acquire: false, requested_no_followup: true,
        requested_completion_turn_key: key, requested_completion_identity_source: "lifecycle_reentry",
        linked_successor_todo_ids: [], successor_intents: [], note: null, evidence: todo.evidence as string,
        reason: null, clear_claim: false, validation_declaration: null, validation_receipt: null,
        completion_policy_request: null, dry_run: false, now: new Date("2026-09-07T07:00:00Z"),
      };
      const before = await store.loadAuthority(), originalReceipt = await store.readReceipt("migrated");
      const activeLease = {schema_version: "task_lease_v0", goal_id: goal, todo_id: todo.todo_id,
        owner: "agent-a", idempotency_key: "held-execution", write_scopes: [], acquire_ttl_seconds: 600,
        version: 1, lease_epoch: 1, acquired_at: "2026-09-07T06:55:00Z", updated_at: "2026-09-07T06:55:00Z",
        expires_at: "2026-09-07T07:05:00Z", status: "active"};
      for (const [patch, leases] of [
        [{status: "open", done: false}, []],
        [{successor_todo_ids: ["todo_existing_successor"]}, []],
        [{completion_continuation: null}, []],
        [{}, [activeLease]],
      ] as [JsonObject, JsonObject[]][]) {
        const isolated = (await factory(t)).store;
        await isolated.commitAuthority({operation_id: "invalid-closeout-source", expected_provider_revision: null,
          next_projection: authorityProjectionFixture(goal, [{...todo, ...patch}], leases, schema,
            {handoff_mode: "hard_lease"}), events: [], receipts: []});
        const source = await isolated.loadAuthority();
        assert.equal((await execute(isolated, request)).status, "failed", JSON.stringify(patch));
        assert.deepEqual(await isolated.loadAuthority(), source);
      }
      for (const change of [{actor_agent_id: "agent-b"}, {actor_agent_id: null},
        {registered_agents: ["agent-b"]}, {requested_completion_turn_key: "another-turn"},
        {evidence: "Replace original evidence"}, {note: "new completion"}, {clear_claim: true},
        {completion_result: {sha256: "b".repeat(64)}}, {validation_receipt: {passed: true}},
        {successor_intents: [{role: "agent", text: "New work"}]}]) {
        assert.equal((await execute(store, {...request, ...change})).status, "failed", JSON.stringify(change));
        assert.deepEqual(await store.loadAuthority(), before);
      }
      assert.equal((await execute(store, request, async () => false)).reason_code, "authority_source_changed");
      const preview = await execute(store, {...request, dry_run: true});
      assert.equal(preview.status, "planned", JSON.stringify(preview));
      assert.deepEqual(await store.loadAuthority(), before);
      const lostResponse = new Proxy(store, {get(target, name) {
        if (name === "commitAuthority") return async (...args: Parameters<AuthorityStore["commitAuthority"]>) => {
          await target.commitAuthority(...args);
          throw new Error("Synthetic response loss after closeout commit");
        };
        const member = Reflect.get(target, name);
        return typeof member === "function" ? member.bind(target) : member;
      }});
      const result = await execute(lostResponse, request);
      assert.equal(result.status, "recovered", JSON.stringify(result));
      assert.equal(result.changed, true);
      assert.equal((result.terminal_decision as JsonObject).outcome, "apply");
      assert.equal(result.completion_continuation, "no_followup");
      assert.equal(result.completion_recovery, "lifecycle_reentry_terminal_closeout");
      assert.equal(result.completion_receipt_id, "tcw_original");
      const after = await store.loadAuthority();
      assert.equal(before.status, "loaded"); assert.equal(after.status, "loaded");
      if (before.status === "loaded" && after.status === "loaded") {
        const prior = (before.head.todos as JsonObject[])[0];
        assert.deepEqual((after.head.todos as JsonObject[])[0], {...prior, no_followup: true,
          completion_continuation: "no_followup", completion_recovery: "lifecycle_reentry_terminal_closeout"});
        assert.deepEqual(after.head.leases, before.head.leases);
      }
      assert.deepEqual(await store.readReceipt("migrated"), originalReceipt);
      assert.equal((await execute(contender, request, async () => {throw new Error("Historical replay needs no fresh authority");})).status,
        "replayed");
      assert.equal((await execute(store, {...request, actor_agent_id: "agent-b"})).reason_code,
        "coordination_operation_identity_mismatch");
      assert.equal((await execute(store, {...request, evidence: "Changed intent on retry"})).reason_code,
        "coordination_operation_identity_mismatch");
      assert.deepEqual(await store.loadAuthority(), after);
    });
    }
  }
}
