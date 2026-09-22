/** Ordered concurrency, not timer luck: the first miss precedes a peer commit
 * and the next head observes it. Every provider must recover that operation. */
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {commitTeamPlan} from "../../loopx/control_plane/work_items/team_plan_authority.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {executeCanonicalTaskLeaseLifecycle} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {productionScaleCoordinationFixture} from "./production_scale_coordination_fixture.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

import {commandObservationCases, commandObservationScenario} from "./command_observation_scenarios.ts";

function deferred() {
  let resolve!: () => void;
  const promise = new Promise<void>(done => {resolve = done;});
  return {promise, resolve};
}

/** Only the first absent receipt is delayed. All effects use the real backend. */
export function pauseMissingReceipt(store: AuthorityStore) {
  const missing = deferred(), release = deferred();
  let reads = 0, writes = 0;
  const delayed = new Proxy(store, {get(target, key) {
    if (key === "readReceipt") return async (operation: string) => {
      const result = await target.readReceipt(operation);
      if (++reads === 1) {
        assert.equal(result.status, "missing");
        missing.resolve();
        await release.promise;
      }
      return result;
    };
    if (key === "commitAuthority") return async (...args: Parameters<AuthorityStore["commitAuthority"]>) => {
      writes++;
      return target.commitAuthority(...args);
    };
    const value = Reflect.get(target, key, target);
    return typeof value === "function" ? value.bind(target) : value;
  }});
  return {store: delayed, missing: missing.promise, release: release.resolve, writes: () => writes};
}

function teamRequest(): JsonObject {
  return {goal_id: "goal-a", actor_agent_id: null, registered_agents: ["agent-a", "agent-b"],
    supported_action_kinds: ["implement"], observed_at: "2026-09-13T10:05:00Z",
    expected_state_fingerprint: "reviewed", current_state_fingerprint: "reviewed",
    plan: {schema_version: "steward_team_plan_preview_v0", kind: "steward_team_plan_preview",
      goal_id: "goal-a", proposal_id: "command-observation", objective: "Deliver both independent acceptance results",
      quota_envelope: {slots: 2}, stop_condition: "Owner closes the request",
      lanes: ["agent-a", "agent-b"].map(agent => ({lane_id: `lane-${agent}`, agent_id: agent,
        acceptance: `Independent evidence from ${agent}`, first_todo: {text: "Same bounded work",
          priority: "P1", task_class: "advancement_task", action_kind: "implement"}}))}};
}

export function registerCommandObservationConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  for (const schema of ["native", "legacy"] as const) {
    for (const kind of commandObservationCases) {
      test(`${provider} command observation: ${kind} recovers the ${schema} operation before new admission`, async t => {
        const {store, contender} = await factory(t);
        const {run} = await commandObservationScenario(kind, schema, store);
        const paused = pauseMissingReceipt(store);
        const outcome = run(paused.store).then(value => ({value}), error => ({error}));
        await Promise.race([paused.missing, outcome.then(result => {throw new Error(`command returned before receipt lookup: ${JSON.stringify(result)}`);})]);
        const winner = await run(contender).finally(paused.release);
        assert.equal(winner.status, "applied", JSON.stringify(winner));
        const after = await contender.loadAuthority();
        const replay = await outcome;
        assert.ok("value" in replay, `late receipt must precede ${kind} admission`);
        assert.equal(replay.value.status, "replayed", JSON.stringify(replay.value));
        assert.equal(replay.value.provider_revision, winner.provider_revision);
        assert.equal(replay.value.cursor, winner.cursor);
        assert.equal(replay.value.changed, false);
        assert.equal(paused.writes(), 0, "historical recovery must not submit a second transaction");
        assert.deepEqual(await store.loadAuthority(), after, "all unrelated Todos, decisions and leases survive replay");
      });
    }
    test(`${provider} command observation: concurrent team replay preserves the full ${schema} graph`, async t => {
      const {store, contender} = await factory(t);
      const fixture = productionScaleCoordinationFixture("goal-a", schema);
      assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        next_projection: fixture.projection, events: [], receipts: []})).status, "applied");
      const input = teamRequest(), paused = pauseMissingReceipt(store);
      const later = commitTeamPlan(paused.store, input);
      // Attach a handler before releasing the barrier, including on failure.
      const outcome = later.then(value => ({value}), error => ({error}));
      await Promise.race([paused.missing, outcome.then(result => {throw new Error(`command returned before receipt lookup: ${JSON.stringify(result)}`);})]);
      const first = await commitTeamPlan(contender, input).finally(paused.release);
      assert.equal(first.status, "applied", JSON.stringify(first));
      const afterWinner = await contender.loadAuthority();
      const replay = await outcome;
      assert.ok("value" in replay, "same-operation replay must not run create admission against the committed Todos");
      assert.equal(replay.value.status, "replayed", JSON.stringify(replay.value));
      assert.equal(paused.writes(), 0);
      assert.equal(replay.value.provider_revision, first.provider_revision);
      assert.deepEqual(await store.loadAuthority(), afterWinner);
    });

    test(`${provider} command observation: retired atomic claim lease cannot grant ${schema} execution`, async t => {
      const {store} = await factory(t);
      const projection = authorityProjectionFixture("goal-a", [{todo_id: "todo_claim_target", role: "agent",
        text: "Adopt and execute a bounded task", status: "open", done: false, archive_state: "active",
        required_write_scopes: ["command-observation/**"]}], [], schema, {handoff_mode: "hard_lease"});
      assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        next_projection: projection, events: [], receipts: []})).status, "applied");
      const input = {goal_id: "goal-a", todo_id: "todo_claim_target", claimed_by: "agent-a", actor_agent_id: "agent-a",
        expected_role: "agent" as const, registered_agents: ["agent-a", "agent-b"], operation_id: "claim-acquire",
        lease_request: {idempotency_key: "execution-a", expected_version: 0, ttl_seconds: 600},
        dry_run: false, now: new Date("2026-09-13T10:05:00Z")};
      const first = await executeCoordinationTodoClaim(store, input);
      assert.equal(first.status, "applied", JSON.stringify(first));
      const released = await executeCanonicalTaskLeaseLifecycle(store, {goal_id: "goal-a", todo_id: input.todo_id,
        operation: "release", owner: "agent-a", idempotency_key: "execution-a", expected_version: 1,
        ttl_seconds: null, registered_agents: input.registered_agents, now: input.now});
      assert.equal(released.status, "applied", JSON.stringify(released));
      const before = await store.loadAuthority();
      const replay = await executeCoordinationTodoClaim(store, input);
      assert.equal(replay.status, "failed", "historical claim receipt must not return an active retired lease");
      assert.equal(replay.reason_code, "idempotency_key_reuse");
      assert.deepEqual(await store.loadAuthority(), before);
    });
  }
}
