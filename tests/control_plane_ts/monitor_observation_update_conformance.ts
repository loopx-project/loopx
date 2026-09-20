/** The same observation/reactivation contract on every real provider. */
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoUpdate, type CoordinationTodoUpdateInput} from "../../loopx/control_plane/coordination/todo_update.ts";
import {executeCoordinationMonitorPoll} from "../../loopx/control_plane/coordination/todo_monitor_poll.ts";
import {coordinationTodoReadModel} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {evaluateTodoResumeConditions} from "../../loopx/control_plane/todos/resume_condition.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleCompletedMonitorFixture, productionScaleLeasedMonitorFixture} from "./production_scale_coordination_fixture.ts";

function request(fixture: ReturnType<typeof productionScaleCompletedMonitorFixture>): CoordinationTodoUpdateInput {
  return {goal_id: String(fixture.projection.goal_id), todo_id: fixture.target,
    expected_role: "agent", actor_agent_id: fixture.actor, registered_agents: fixture.registered_agents,
    operation_id: "reactivate", patch: {}, clear_fields: [], dry_run: false, now: fixture.now,
    planning_intent: {status: "open", no_followup: false, reason: "A new observation cycle"},
    monitor_observation: {generated_at: fixture.now.toISOString(), result_hash: "previous-evidence",
      material_change: true, monitor_effect_id: "reactivate", cadence: "1h"}};
}

async function seed(store: AuthorityStore, projection: JsonObject) {
  assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    events: [], receipts: [], next_projection: projection})).status, "applied");
}

export function registerMonitorObservationUpdateConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  for (const schema of ["legacy", "native"] as const) {
    test(`${provider}: Monitor observation update ${schema} reactivates a new cycle and preserves historical replay`, async t => {
      const {store, contender} = await factory(t);
      const fixture = productionScaleCompletedMonitorFixture("monitor-reactivation", schema);
      await seed(store, fixture.projection);
      const input = request(fixture);
      const before = await store.loadAuthority();
      const preview = await executeCoordinationTodoUpdate(store, {...input, dry_run: true});
      assert.equal(preview.status, "planned", JSON.stringify(preview));
      assert.equal((preview.monitor_poll_transition as JsonObject).material_change_generation, 5);
      assert.deepEqual(await store.loadAuthority(), before);
      const result = await executeCoordinationTodoUpdate(store, input);
      assert.equal(result.status, "applied", JSON.stringify(result));
      const head = await store.loadAuthority();
      assert.equal(head.status, "loaded"); if (head.status !== "loaded") return;
      const original = fixture.projection.todos as JsonObject[];
      const todos = head.head.todos as JsonObject[];
      const monitor = todos.find(todo => todo.todo_id === fixture.target)!;
      assert.equal(monitor.status, "open"); assert.equal(monitor.done, false);
      assert.equal(monitor.result_hash, "previous-evidence", "equal hashes do not erase a new lifecycle");
      assert.equal(monitor.material_change_generation, 5);
      for (const field of ["completed_at", "no_followup", "completion_continuation", "completion_recovery", "completion_turn_key"]) {
        assert.equal(Object.hasOwn(monitor, field), false, field);
      }
      assert.deepEqual(head.head.leases, fixture.projection.leases);
      assert.deepEqual(todos.filter(todo => todo.todo_id !== fixture.target), original.filter(todo => todo.todo_id !== fixture.target));
      const satisfied = [original, todos].map(source => {
        const result = evaluateTodoResumeConditions({schema_version: "todo_resume_evaluation_request_v0",
          items: source.filter(todo => todo.todo_id === fixture.dependent), source_items: source, kinds: ["monitor_changed"]});
        return ((result.conditions as JsonObject[])[0]!.condition as JsonObject).satisfied;
      });
      assert.deepEqual(satisfied, [false, true]);
      // The normal quota poll still owns subsequent observations and successors.
      const poll = await executeCoordinationMonitorPoll(contender, {goal_id: input.goal_id,
        operation_id: "next-poll", actor_agent_id: input.actor_agent_id, registered_agents: input.registered_agents,
        dry_run: false, observation: {todo_id: fixture.target, generated_at: "2026-09-01T02:00:00Z",
          result_hash: "later-evidence", material_change: true}, intent: {}});
      assert.equal(poll.status, "applied", JSON.stringify(poll));
      assert.equal((poll.writeback as JsonObject).material_change_generation, 6);
      const current = await store.loadAuthority();
      assert.equal(current.status, "loaded"); if (current.status !== "loaded") return;
      const retired = (current.head.todos as JsonObject[]).map(todo => todo.todo_id === fixture.target
        ? {...todo, status: "done", done: true, completed_at: "2026-09-01T03:00:00Z"} : todo);
      await contender.commitAuthority({operation_id: "retire-again", expected_provider_revision: current.provider_revision,
        events: [], receipts: [], next_projection: {...current.head, todos: retired,
          todo_read_model: coordinationTodoReadModel(retired, (current.head.todo_read_model as JsonObject).schema_version)}});
      const final = await store.loadAuthority();
      const replay = await executeCoordinationTodoUpdate(contender, input);
      assert.equal(replay.status, "replayed");
      assert.equal((replay.monitor_poll_transition as JsonObject).material_change_generation, 5);
      assert.deepEqual(await store.loadAuthority(), final, "historical success never reopens current completion");
      const stale = await executeCoordinationTodoUpdate(store, {...input, operation_id: "old-observation-new-id"});
      assert.equal(stale.status, "failed"); assert.match(String(stale.reason), /newer than completion/);
      const conflict = await executeCoordinationTodoUpdate(store, {...input,
        monitor_observation: {...input.monitor_observation!, result_hash: "changed-retry"}});
      assert.equal(conflict.status, "failed");
      assert.deepEqual(await store.loadAuthority(), final);
    });
  }

  test(`${provider}: Monitor observation update rejects invalid authority, effects and stale reactivation without writes`, async t => {
    const {store} = await factory(t);
    const fixture = productionScaleCompletedMonitorFixture("monitor-update-negatives");
    await seed(store, fixture.projection);
    const input = request(fixture), before = await store.loadAuthority();
    const invalid: Partial<CoordinationTodoUpdateInput>[] = [
      {actor_agent_id: null}, {actor_agent_id: "agent-b"}, {registered_agents: []},
      {planning_intent: {}}, {planning_intent: {status: "done"}},
      {planning_intent: {status: "open", claimed_by: "agent-b"}},
      {planning_intent: {status: "open", monitor_metadata: {watch_only: false}}},
      {patch: {text: "No mixed edit"}}, {clear_fields: ["note"]},
      {monitor_observation: {...input.monitor_observation!, material_change: false}},
      {monitor_observation: {...input.monitor_observation!, generated_at: "2026-09-01T00:30:00Z"}},
      {monitor_observation: {...input.monitor_observation!, material_change_generation: 900}},
      {monitor_observation: {...input.monitor_observation!, target_key: "different-target"}},
      {expected_provider_revision: "stale-revision"},
    ];
    for (const mutation of invalid) {
      const result = await executeCoordinationTodoUpdate(store, {...input, ...mutation});
      assert.equal(result.status, "failed", JSON.stringify({mutation, result}));
      assert.deepEqual(await store.loadAuthority(), before);
      assert.equal((await store.readReceipt(input.operation_id)).status, "missing");
    }
    assert.equal((await executeCoordinationTodoUpdate(store, input, async () => false)).status, "failed");
    assert.deepEqual(await store.loadAuthority(), before);
    for (const [index, fields] of [
      {archive_state: "archive"}, {superseded_by: "other-work"}, {excluded_agents: [fixture.actor]},
      {bound_agent: "agent-b"}, {completed_at: "invalid"}, {role: "user", task_class: "user_action"},
    ].entries()) {
      const loaded = await store.loadAuthority(); assert.equal(loaded.status, "loaded");
      if (loaded.status !== "loaded") return;
      const todos = (fixture.projection.todos as JsonObject[]).map(todo => todo.todo_id === fixture.target ? {...todo, ...fields} : todo);
      assert.equal((await store.commitAuthority({operation_id: `source-negative-${index}`, expected_provider_revision: loaded.provider_revision,
        events: [], receipts: [], next_projection: {...fixture.projection, todos,
          todo_read_model: coordinationTodoReadModel(todos, (fixture.projection.todo_read_model as JsonObject).schema_version)}})).status, "applied");
      const source = await store.loadAuthority();
      assert.equal((await executeCoordinationTodoUpdate(store, input)).status, "failed", JSON.stringify(fields));
      assert.deepEqual(await store.loadAuthority(), source);
    }
  });

  test(`${provider}: Monitor observation update holds the current lease without changing execution`, async t => {
    const {store} = await factory(t);
    const fixture = productionScaleLeasedMonitorFixture("monitor-update-lease");
    await seed(store, fixture.projection);
    const input = {...request(fixture), planning_intent: {reason: "Observed current execution"},
      lease_idempotency_key: fixture.proof.idempotency_key, lease_expected_version: fixture.proof.expected_version,
      monitor_observation: {...request(fixture).monitor_observation!, result_hash: "new-evidence"}};
    const before = await store.loadAuthority();
    for (const extra of [{lease_expected_version: 999}, {lease_idempotency_key: "old-key"},
      {now: new Date("2099-01-01T00:00:00Z")}, {planning_intent: {status: "blocked"}}]) {
      assert.equal((await executeCoordinationTodoUpdate(store, {...input, ...extra})).status, "failed");
      assert.deepEqual(await store.loadAuthority(), before);
    }
    const result = await executeCoordinationTodoUpdate(store, input);
    assert.equal(result.status, "applied", JSON.stringify(result));
    const after = await store.loadAuthority(); assert.equal(after.status, "loaded");
    if (after.status === "loaded") assert.deepEqual(after.head.leases, fixture.projection.leases);
    if (after.status === "loaded") {
      const completed = (after.head.todos as JsonObject[]).map(todo => todo.todo_id === fixture.target ?
        {...todo, status: "done", done: true, completed_at: "2026-09-01T01:30:00Z"} : todo);
      await store.commitAuthority({operation_id: "retained-lease", expected_provider_revision: after.provider_revision,
        events: [], receipts: [], next_projection: {...after.head, todos: completed,
          todo_read_model: coordinationTodoReadModel(completed, (after.head.todo_read_model as JsonObject).schema_version)}});
      const retired = await store.loadAuthority();
      const rejected = await executeCoordinationTodoUpdate(store, {...input, operation_id: "cannot-resume-execution",
        planning_intent: {status: "open"}, monitor_observation: {...input.monitor_observation,
          generated_at: "2026-09-01T02:00:00Z", monitor_effect_id: "next-cycle"}});
      assert.equal(rejected.reason_code, "monitor_reactivation_lease_transition_required");
      assert.deepEqual(await store.loadAuthority(), retired);
    }
  });

  test(`${provider}: Monitor reactivation CAS and lost-response recovery retain one cycle`, async t => {
    const {store, contender} = await factory(t);
    const fixture = productionScaleCompletedMonitorFixture("monitor-update-recovery");
    await seed(store, fixture.projection);
    const input = request(fixture);
    let lost = false;
    const responseLost = new Proxy(store, {get(target, property) {
      if (property === "commitAuthority") return async (commit: AuthorityStoreCommit) => {
        await target.commitAuthority(commit); lost = true; throw new Error("response lost after commit");
      };
      if (property === "readReceipt") return async (id: string) => {
        if (lost) throw new Error("readback unavailable"); return target.readReceipt(id);
      };
      const value = Reflect.get(target, property); return typeof value === "function" ? value.bind(target) : value;
    }});
    assert.equal((await executeCoordinationTodoUpdate(responseLost, input)).status, "ambiguous");
    const committed = await contender.loadAuthority();
    assert.equal((await executeCoordinationTodoUpdate(contender, input)).status, "replayed");
    assert.deepEqual(await store.loadAuthority(), committed);
    const race = new Proxy(store, {get(target, property) {
      if (property === "commitAuthority") return async (commit: AuthorityStoreCommit) => {
        const current = await contender.loadAuthority();
        assert.equal(current.status, "loaded"); if (current.status !== "loaded") throw new Error("missing head");
        await contender.commitAuthority({operation_id: "competing-write", expected_provider_revision: current.provider_revision,
          events: [], receipts: [], next_projection: current.head});
        return target.commitAuthority(commit);
      };
      const value = Reflect.get(target, property); return typeof value === "function" ? value.bind(target) : value;
    }});
    const next = {...input, operation_id: "raced", planning_intent: {},
      monitor_observation: {...input.monitor_observation!, monitor_effect_id: "raced", result_hash: "new-evidence",
        generated_at: "2026-09-01T02:00:00Z"}};
    assert.equal((await executeCoordinationTodoUpdate(race, next)).status, "conflict");
    assert.equal((await store.readReceipt(next.operation_id)).status, "missing");
  });
}
