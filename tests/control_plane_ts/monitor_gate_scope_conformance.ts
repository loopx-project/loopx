/** Gate qualification and mutation must share the provider head/CAS. */
import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import {executeCoordinationMonitorPoll} from "../../loopx/control_plane/coordination/todo_monitor_poll.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

export function registerMonitorGateScopeConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  for (const moment of ["before_plan", "before_cas", "after_commit"] as const) {
    test(`${provider}: auxiliary gate change ${moment} preserves write authority and historical replay`, async t => {
      const {store, contender} = await factory(t);
      const goal = "monitor-gate-scope";
      const base = {schema_version: TODO_DOMAIN_ITEM_SCHEMA, status: "open", done: false, archive_state: "active"};
      const todos: JsonObject[] = [
        {...base, todo_id: "todo_monitor", role: "agent", text: "Observe a synthetic target", task_class: "continuous_monitor", claimed_by: "agent-a", cadence: "1h", target_key: "synthetic-target"},
        {...base, todo_id: "todo_other", role: "agent", text: "Await owner input", task_class: "advancement_task", status: "blocked"},
        {...base, todo_id: "todo_gate", role: "user", text: "Decide a dependency", task_class: "user_gate", blocks_agent: "agent-a", unblocks_todo_id: "todo_other"},
      ];
      todos.sort((a, b) => String(a.todo_id).localeCompare(String(b.todo_id)));
      assert.equal((await store.commitAuthority({operation_id: "seed-gate", expected_provider_revision: null,
        events: [], receipts: [], next_projection: {goal_id: goal, handoff_mode: "soft_claim", todos, leases: [],
          todo_read_model: {schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA, todo_count: todos.length,
            records_sha256: canonicalAuthoritySha256(todos), contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields]}}})).status, "applied");
      const poll = {goal_id: goal, operation_id: "guarded-poll", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
        dry_run: false, gate_scope_guard: true, intent: {}, observation: {todo_id: "todo_monitor", target_key: "synthetic-target",
          generated_at: "2026-09-01T00:00:00Z", result_hash: "observed", material_change: false}};
      const gate = async (operation: string, target: string) => {
        const result = await executeCoordinationTodoUpdate(contender, {goal_id: goal, operation_id: operation,
          todo_id: "todo_gate", expected_role: "user", actor_agent_id: "agent-a", registered_agents: ["agent-a"],
          patch: {}, planning_intent: {unblocks_todo_id: target}, clear_fields: [], dry_run: false, now: new Date()});
        assert.equal(result.status, "applied", JSON.stringify(result));
      };
      if (moment === "before_plan") await gate("block-before-plan", "todo_monitor");
      let raced = false;
      const writer = new Proxy(store, {get(target, key) {
        if (key === "commitAuthority") return async (commit: Parameters<AuthorityStore["commitAuthority"]>[0]) => {
          if (moment === "before_cas" && !raced && commit.operation_id === poll.operation_id) {
            raced = true;
            await gate("block-before-cas", "todo_monitor");
          }
          return target.commitAuthority(commit);
        };
        const value = Reflect.get(target, key);
        return typeof value === "function" ? value.bind(target) : value;
      }});
      const result = await executeCoordinationMonitorPoll(writer, poll);
      if (moment !== "after_commit") {
        assert.notEqual(result.status, "applied", JSON.stringify(result));
        const head = await store.loadAuthority();
        assert.equal(head.status, "loaded");
        if (head.status !== "loaded") throw new Error("missing test head");
        assert.equal((head.head.todos as JsonObject[]).find(row => row.todo_id === "todo_monitor")!.result_hash, undefined);
        assert.equal((await store.readReceipt(poll.operation_id)).status, "missing");
        const denied = await executeCoordinationMonitorPoll(store, poll);
        assert.equal(denied.reason_code, "monitor_poll_rejected");
        assert.ok(denied.no_effect);
        await gate("restore-dependency", "todo_other");
        assert.equal((await executeCoordinationMonitorPoll(store, poll)).status, "applied");
      } else assert.equal(result.status, "applied", JSON.stringify(result));
      await gate("block-after-commit", "todo_monitor");
      const committed = await store.loadAuthority();
      assert.equal((await executeCoordinationMonitorPoll(store, poll)).status, "replayed");
      assert.deepEqual(await store.loadAuthority(), committed);
      assert.equal((await executeCoordinationMonitorPoll(store, {...poll, operation_id: "new-poll",
        observation: {...poll.observation, generated_at: "2026-09-01T02:00:00Z"}})).status, "failed");
    });
  }
}
