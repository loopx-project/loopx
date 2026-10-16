import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import {TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA, TODO_DOMAIN_RECORD_CONTRACT,
  TODO_ITEM_SCHEMA, TODO_CANONICAL_READ_RECORD_SCHEMA, TODO_CANONICAL_READ_RECORD_FIELDS}
  from "../../loopx/control_plane/coordination/coordination_state_contract.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";

async function head(store: AuthorityStore) {
  const loaded = await store.loadAuthority();
  assert.equal(loaded.status, "loaded");
  if (loaded.status !== "loaded") throw new Error("fixture head missing");
  return loaded;
}

export function registerNativePlanningUpdateConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  test(`${provider}: reviewed edits fence new work and recover immutable history on the full graph`, async t => {
    const {store, contender} = await factory(t);
    const fixture = productionScaleCoordinationFixture("reviewed-planning");
    const projection = structuredClone(fixture.projection);
    projection.handoff_mode = "soft_claim";
    // Keep unrelated leases and the full dependency graph. This target's
    // execution lineage is intentionally absent, so it permits metadata delegation.
    projection.leases = (projection.leases as JsonObject[]).filter(l => l.todo_id !== fixture.completion_todo_id);
    assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      next_projection: projection, events: [], receipts: []})).status, "applied");
    const before = await head(store);
    const request = {goal_id: "reviewed-planning", todo_id: fixture.completion_todo_id,
      expected_role: "agent", actor_agent_id: "agent-b", registered_agents: fixture.registered_agents,
      operation_id: "reviewed-edit", patch: {note: "Owner reviewed correction"}, clear_fields: [],
      expected_provider_revision: before.provider_revision, authority_reason: "Bounded correction",
      lifecycle_grants: [{agent_id: "agent-b", actions: ["update"], requires_reason: true}],
      dry_run: false, now: new Date("2026-09-07T06:00:00Z")};
    for (const [label, change, code] of [
      ["ungranted", {lifecycle_grants: []}, "update_owner_mismatch"],
      ["reasonless", {authority_reason: null}, "delegation_reason_required"],
      ["wrong-action", {lifecycle_grants: [{agent_id: "agent-b", actions: ["reassign"], requires_reason: true}]}, "delegation_action_not_granted"],
    ] as const) {
      const rejected = await executeCoordinationTodoUpdate(store, {...request, ...change, operation_id: label});
      assert.equal(rejected.reason_code, code, JSON.stringify(rejected));
      assert.deepEqual(await head(store), before);
      assert.equal((await store.readReceipt(label)).status, "missing");
    }
    let sourceChecks = 0;
    const rejected = await executeCoordinationTodoUpdate(store, request, async () => ++sourceChecks === 1);
    assert.equal(rejected.reason_code, "authority_source_changed");
    assert.deepEqual(await head(store), before);
    const applied = await executeCoordinationTodoUpdate(store, request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const original = await store.readReceipt(request.operation_id);
    const after = await head(store);
    assert.deepEqual(after.head.leases, before.head.leases);
    assert.deepEqual((after.head.todos as JsonObject[]).filter(r => r.todo_id !== request.todo_id),
      (before.head.todos as JsonObject[]).filter(r => r.todo_id !== request.todo_id));
    assert.equal((await executeCoordinationTodoUpdate(contender, {...request,
      operation_id: "unreviewed-new-edit"})).reason_code, "provider_revision_mismatch");
    assert.equal((await executeCoordinationTodoUpdate(contender, {...request, operation_id: "later-edit",
      expected_provider_revision: after.provider_revision, patch: {note: "Later accepted correction"}})).status, "applied");
    const latest = await head(store);
    const replay = await executeCoordinationTodoUpdate(store, {...request, lifecycle_grants: []},
      async () => { throw new Error("historical receipt must precede current source admission"); });
    assert.equal(replay.status, "replayed");
    assert.deepEqual(await head(store), latest);
    assert.deepEqual(await store.readReceipt(request.operation_id), original);
    assert.equal((await executeCoordinationTodoUpdate(store, {...request, authority_reason: "Changed justification"})).reason_code,
      "coordination_operation_identity_mismatch");
  });

  for (const native of [false, true]) {
    test(`${provider}: planning transaction uses complete ${native ? "native" : "legacy"} records and keeps wait generation`, async t => {
      const {store, contender} = await factory(t);
      const goal = "planning-goal";
      const todos: JsonObject[] = [
        {todo_id: "todo_aaa_target", task_class: "advancement_task", claimed_by: "agent-a", note: "retained",
          successor_todo_ids: ["todo_bbb_successor"]},
        {todo_id: "todo_bbb_successor", task_class: "advancement_task"},
        // Deliberately beyond the hot read-model limit: authoring uses the head, not its summary.
        ...Array.from({length: 40}, (_, i) => ({todo_id: `todo_other_${String(i).padStart(2, "0")}`, task_class: "advancement_task"})),
        {todo_id: "todo_zzz_monitor", task_class: "continuous_monitor", material_change_generation: 7},
      ].map(record => ({schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : TODO_ITEM_SCHEMA,
        role: "agent", status: "open", done: false, text: "Synthetic planning record", archive_state: "active",
        ...(!native ? {source_section: "Agent Todo"} : {}), ...record}));
      const seed = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        events: [], receipts: [], next_projection: {goal_id: goal, handoff_mode: "soft_claim", todos, leases: [],
          todo_read_model: {schema_version: native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA,
            todo_count: todos.length, records_sha256: canonicalAuthoritySha256(todos),
            contract_fields: native ? [...TODO_DOMAIN_RECORD_CONTRACT.fields] : [...TODO_CANONICAL_READ_RECORD_FIELDS]}}});
      assert.equal(seed.status, "applied");
      const request = {goal_id: goal, todo_id: "todo_aaa_target", expected_role: "agent", actor_agent_id: "agent-a",
        registered_agents: ["agent-a", "agent-b"], operation_id: "wait",
        patch: {text: "Synthetic planning record"}, clear_fields: [],
        planning_intent: {resume_when: "monitor_changed:todo_zzz_monitor", reason: "Await material change",
          action_kind: "IMPLEMENT", task_domain: "Code.Review",
          task_repository: "git@github.com:example/project.git",
          required_capabilities: ["Code-Review", "code_review"],
          target_capabilities: ["Delivery"], required_write_scopes: ["src/**"],
          explore_result_node_refs: ["Node:alpha"]},
        dry_run: false, now: new Date("2026-09-10T00:00:00Z")};
      const before = await head(store);
      const preview = await executeCoordinationTodoUpdate(store, {...request, dry_run: true});
      assert.equal(preview.status, "planned", JSON.stringify(preview));
      assert.deepEqual(await head(store), before);
      const applied = await executeCoordinationTodoUpdate(store, request);
      assert.equal(applied.status, "applied", JSON.stringify(applied));
      let current = await head(store);
      let target = (current.head.todos as JsonObject[])[0]!;
      assert.equal(target.resume_monitor_generation, 7);
      assert.equal(target.action_kind, "implement");
      assert.equal(target.task_repository, "git:github.com/example/project");
      assert.equal(target.task_domain, "code.review");
      assert.deepEqual(target.required_capabilities, ["code_review"]);
      assert.deepEqual(target.target_capabilities, ["delivery"]);
      assert.deepEqual(target.required_write_scopes, ["src/**"]);
      assert.deepEqual(target.explore_result_node_refs, ["Node:alpha"]);
      assert.deepEqual((current.head.todos as JsonObject[]).slice(1), todos.slice(1));
      // Simulate an independent observation at a new canonical revision.
      const observed = structuredClone(current.head);
      (observed.todos as JsonObject[]).at(-1)!.material_change_generation = 8;
      (observed.todo_read_model as JsonObject).records_sha256 = canonicalAuthoritySha256(observed.todos);
      assert.equal((await contender.commitAuthority({operation_id: "observe", expected_provider_revision: current.provider_revision,
        next_projection: observed, events: [], receipts: []})).status, "applied");
      const topology = await executeCoordinationTodoUpdate(store, {...request, operation_id: "topology",
        planning_intent: {successor_todo_ids: ["todo_bbb_successor"], evidence: "Checked dependency"}});
      assert.equal(topology.status, "failed", JSON.stringify(topology));
      assert.match(String(topology.reason), /clear the satisfied resume_when/);
      assert.equal((await store.readReceipt("topology")).status, "missing");
      const evidenceOnly = await executeCoordinationTodoUpdate(store, {...request, operation_id: "evidence",
        planning_intent: {evidence: "Checked dependency"}});
      assert.equal(evidenceOnly.status, "applied", JSON.stringify(evidenceOnly));
      current = await head(store);
      target = (current.head.todos as JsonObject[])[0]!;
      assert.equal(target.resume_monitor_generation, 7, "retained wait must not silently re-arm");
      assert.equal((await executeCoordinationTodoUpdate(store, request)).status, "replayed");
      assert.deepEqual(await head(store), current);
      assert.equal((await executeCoordinationTodoUpdate(store, {...request,
        planning_intent: {reason: "Different request"}})).reason_code, "coordination_operation_identity_mismatch");
      // Lost commit response is recovered from the same durable receipt.
      const lostResponse: AuthorityStore = {storeIdentity: () => store.storeIdentity(),
        loadAuthority: () => store.loadAuthority(), readReceipt: id => store.readReceipt(id),
        scanCommitted: (cursor, limit) => store.scanCommitted(cursor, limit),
        commitAuthority: async input => {await store.commitAuthority(input); return {
          status: "ambiguous", reason_code: "lost_response", reason: "Synthetic lost response"};}};
      const cleared = await executeCoordinationTodoUpdate(lostResponse, {...request, operation_id: "clear",
        planning_intent: {clear_resume_when: true, successor_todo_ids: [], no_followup: false,
          required_capabilities: [], target_capabilities: [], required_write_scopes: [], explore_result_node_refs: []}});
      assert.equal(cleared.status, "recovered", JSON.stringify(cleared));
      target = ((await head(store)).head.todos as JsonObject[])[0]!;
      assert.equal(target.resume_when, undefined);
      assert.equal(target.resume_monitor_generation, undefined);
      assert.deepEqual(target.successor_todo_ids, []);
      assert.equal(target.no_followup, false);
      assert.equal(target.claimed_by, "agent-a");
      assert.equal(target.note, "retained");
      assert.deepEqual(target.required_capabilities, []);
      const aliasReplay = await executeCoordinationTodoUpdate(store, {...request, planning_intent: {
        ...request.planning_intent, required_capabilities: ["code_review"],
        task_repository: "https://github.com/example/project", action_kind: "implement",
      }});
      assert.equal(aliasReplay.status, "replayed", JSON.stringify(aliasReplay));
      assert.deepEqual(((await head(store)).head.todos as JsonObject[])[0], target,
        "replaying the original declaration must not restore requirements subsequently cleared");
      for (const planning_intent of [
        {required_capabilities: ["valid", "bad/token"]}, {required_write_scopes: ["src/**", "../escape"]},
        {task_repository: "https://user:password@example.com/project"},
        {decision_outcome: "approve"}, {required_decision_scopes: ["not-a-scope"]},
        {decision_scope: {kind: "direction", granularity: "goal", scope_key: "release"}},
      ]) {
        const beforeInvalid = await head(store);
        const result = await executeCoordinationTodoUpdate(store, {...request,
          operation_id: "invalid-requirement", planning_intent});
        assert.equal(result.status, "failed", JSON.stringify(result));
        assert.deepEqual(await head(store), beforeInvalid);
        assert.equal((await store.readReceipt("invalid-requirement")).status, "missing");
      }

      // Force a canonical-head race between plan and CAS; do not recompute against a stale dependency.
      const racing: AuthorityStore = {...lostResponse, commitAuthority: async input => {
        const fresh = await head(contender);
        await contender.commitAuthority({operation_id: "race-winner", expected_provider_revision: fresh.provider_revision,
          next_projection: fresh.head, events: [], receipts: []});
        return store.commitAuthority(input);
      }};
      const conflict = await executeCoordinationTodoUpdate(racing, {...request, operation_id: "race-loser",
        planning_intent: {...request.planning_intent, successor_todo_ids: ["todo_bbb_successor"]}});
      assert.equal(conflict.status, "conflict", JSON.stringify(conflict));
      assert.equal((await store.readReceipt("race-loser")).status, "missing");
      assert.deepEqual(((await head(store)).head.todos as JsonObject[])[0], target);
    });
  }

  test(`${provider}: production-scale planning edit preserves unrelated state and active lease`, async t => {
    const {store} = await factory(t);
    const fixture = productionScaleCoordinationFixture("planning-scale");
    assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      next_projection: fixture.projection, events: [], receipts: []})).status, "applied");
    const request = {goal_id: "planning-scale", todo_id: fixture.completion_todo_id, expected_role: "agent",
      actor_agent_id: "agent-a", registered_agents: fixture.registered_agents, operation_id: "scale-edit",
      patch: {}, clear_fields: [], planning_intent: {reason: "Bounded planning update", evidence: "Synthetic readback"},
      lease_idempotency_key: fixture.completion_lease_idempotency_key,
      lease_expected_version: fixture.completion_lease_expected_version,
      dry_run: false, now: new Date("2026-09-07T06:00:00Z")};
    const before = await head(store);
    const applied = await executeCoordinationTodoUpdate(store, request);
    assert.equal(applied.status, "applied", JSON.stringify(applied));
    const after = await head(store);
    assert.deepEqual(after.head.leases, before.head.leases);
    const records = after.head.todos as JsonObject[];
    const original = before.head.todos as JsonObject[];
    assert.equal(records.length, fixture.expected_initial_todo_count);
    assert.deepEqual(records.filter(t => t.todo_id !== request.todo_id), original.filter(t => t.todo_id !== request.todo_id));
    assert.equal(records.find(t => t.todo_id === request.todo_id)?.last_actor_agent_id,
      original.find(t => t.todo_id === request.todo_id)?.last_actor_agent_id,
      "planning-only updates preserve legacy actor attribution");
    for (const [operation_id, change] of [
      ["partial-proof", {lease_idempotency_key: null}],
      ["status-change", {planning_intent: {status: "deferred", resume_when: "pr_merged:#123"}}],
      ["requirements-change", {planning_intent: {required_write_scopes: ["new/**"]}}],
      ["wrong-owner", {actor_agent_id: "agent-b"}],
    ] as const) {
      const result = await executeCoordinationTodoUpdate(store, {...request, operation_id, ...change});
      assert.equal(result.status, "failed", JSON.stringify(result));
      if (operation_id === "status-change") assert.equal(result.reason_code, "update_lease_status_transition_unsupported");
      assert.deepEqual(await head(store), after);
      assert.equal((await store.readReceipt(operation_id)).status, "missing");
    }
  });

  for (const native of [false, true]) test(`${provider}: update intent matrix preserves compatibility clears and user scope`, async t => {
    const {store} = await factory(t);
    const fixture = productionScaleCoordinationFixture("update-matrix");
    // Authority projections require deterministic todo_id ordering. Keep the
    // fixture declaration readable while making its synthesized head valid.
    const cases = Object.values(fixture.update_cases)
      .sort((left, right) => String(left.todo_id).localeCompare(String(right.todo_id)));
    const todos = cases.map((item, index) => ({
      schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : TODO_ITEM_SCHEMA,
      todo_id: item.todo_id,
      role: item.role,
      status: item.status,
      done: false,
      text: "Synthetic update matrix Todo",
      archive_state: "active",
      ...(native ? {} : {source_section: item.role === "user" ? "User Todo" : "Agent Todo", index: index + 1}),
      ...(item.task_class ? {task_class: item.task_class} : {}),
      ...(item.claimed_by ? {claimed_by: item.claimed_by} : {}),
      ...(item.reason ? {reason: item.reason} : {}),
      ...(item.decision_scope ? {decision_scope: item.decision_scope} : {}),
      ...(item.required_decision_scopes ? {required_decision_scopes: item.required_decision_scopes} : {}),
      ...(item.goal_bound ? {goal_bound: true} : {}),
      ...(item.global_gate ? {global_gate: true} : {}),
    } as JsonObject));
    const projection = {
      goal_id: "update-matrix", handoff_mode: "soft_claim", todos, leases: [],
      todo_read_model: {
        schema_version: native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA,
        todo_count: todos.length, records_sha256: canonicalAuthoritySha256(todos),
        contract_fields: native ? [...TODO_DOMAIN_RECORD_CONTRACT.fields] : [...TODO_CANONICAL_READ_RECORD_FIELDS],
      },
    };
    assert.equal((await store.commitAuthority({operation_id: "update-matrix-seed",
      expected_provider_revision: null, next_projection: projection, events: [], receipts: []})).status, "applied");
    const run = async (name: string, item: Record<string, unknown>) => {
      const expected = item.expected as Record<string, unknown>;
      const todoId = String(item.todo_id);
      const expectedRole = item.role == null ? null : String(item.role);
      const actor = item.actor_agent_id == null ? null : String(item.actor_agent_id);
      const registered = Array.isArray(item.registered_agents)
        ? item.registered_agents.map(value => String(value)) : [];
      const result = await executeCoordinationTodoUpdate(store, {
        goal_id: "update-matrix", todo_id: todoId, expected_role: expectedRole,
        actor_agent_id: actor, registered_agents: registered, operation_id: `update-${name}`,
        patch: {}, clear_fields: [], planning_intent: item.intent as JsonObject, dry_run: false,
        now: new Date("2026-09-10T00:00:00Z"),
      });
      assert.equal(result.status, expected.status, JSON.stringify(result));
      if (expected.reason_value !== undefined) {
        const current = await head(store);
        const row = (current.head.todos as JsonObject[]).find(todo => todo.todo_id === todoId)!;
        assert.equal(row.reason, expected.reason_value);
      }
      for (const field of ["decision_scope", "required_decision_scopes"] as const) {
        if (expected[field] !== undefined) {
          const current = await head(store);
          const row = (current.head.todos as JsonObject[]).find(todo => todo.todo_id === todoId)!;
          assert.deepEqual(row[field], expected[field]);
        }
      }
      if (expected.blocks_agent !== undefined || expected.global_gate === null) {
        const current = await head(store);
        const row = (current.head.todos as JsonObject[]).find(todo => todo.todo_id === todoId)!;
        assert.equal(row.blocks_agent, expected.blocks_agent);
        assert.equal(Object.hasOwn(row, "global_gate"), false);
        // Agent-scoped gate repairs remove the old goal-wide marker rather
        // than persisting a second false-valued gate state.
        assert.notEqual(row.goal_bound, true);
      }
    };
    for (const [name, item] of Object.entries(fixture.update_cases)) await run(name, item);
    const before = await head(store);
    const rejected = await executeCoordinationTodoUpdate(store, {
      goal_id: "update-matrix", todo_id: String(cases[0]!.todo_id), expected_role: "agent",
      actor_agent_id: "agent-a", registered_agents: ["agent-a"], operation_id: "update-owned-null",
      patch: {}, clear_fields: [], planning_intent: {}, dry_run: false,
      now: new Date("2026-09-10T00:01:00Z"),
    });
    assert.equal(rejected.status, "failed");
    assert.deepEqual(await head(store), before);
  });
}
