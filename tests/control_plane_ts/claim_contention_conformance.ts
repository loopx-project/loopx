/** Exercise command retries through real provider CAS, with deterministic races. */
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {configureGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import type {AuthorityStore, AuthorityStoreCommit} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoClaim as claim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

function beforeCommit(store: AuthorityStore, before: (input: AuthorityStoreCommit) => Promise<void>): AuthorityStore {
  return new Proxy(store, {get(target, key) {
    if (key === "commitAuthority") return async (input: AuthorityStoreCommit) => {
      await before(input);
      return target.commitAuthority(input);
    };
    const value = Reflect.get(target, key);
    return typeof value === "function" ? value.bind(target) : value;
  }});
}

export function registerClaimContentionConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  async function setup(t: test.TestContext, overlap = false) {
    const {store, contender} = await factory(t);
    const todos = ["todo_one", "todo_two"].map(todo_id => ({todo_id, role: "agent", status: "open",
      done: false, archive_state: "active", task_class: "advancement_task", text: "Synthetic work",
      claimed_by: null, required_write_scopes: [overlap ? "shared/**" : `${todo_id}/**`]}));
    assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      next_projection: authorityProjectionFixture("goal-a", todos, [], "native", {handoff_mode: "hard_lease"}),
      events: [], receipts: []})).status, "applied");
    const requests = ["agent-a", "agent-b"].map((agent, i) => ({goal_id: "goal-a", todo_id: todos[i].todo_id,
      claimed_by: agent, actor_agent_id: agent, expected_role: "agent", registered_agents: ["agent-a", "agent-b"],
      operation_id: `claim-${agent}`, lease_request: {idempotency_key: `execution-${agent}`, expected_version: 0, ttl_seconds: 600},
      dry_run: false, now: new Date("2026-09-30T10:00:00Z")}));
    return {store, contender, requests};
  }

  for (const scenario of ["independent", "same-todo", "overlap"] as const) {
    test(`${provider} claim contention: ${scenario} revalidates the latest head`, async t => {
      const {store, contender, requests} = await setup(t, scenario === "overlap");
      if (scenario === "same-todo") requests[1].todo_id = requests[0].todo_id;
      let arrivals = 0;
      let release!: () => void;
      const barrier = new Promise<void>(resolve => { release = resolve; });
      const stores = [store, contender].map(value => beforeCommit(value, async () => {
        if (++arrivals === 2) release();
        await barrier;
      }));
      const results = await Promise.all(requests.map((request, i) => claim(stores[i], request)));
      assert.equal(results.filter(r => r.status === "applied").length, scenario === "independent" ? 2 : 1,
        JSON.stringify(results));
      if (scenario !== "independent") {
        const loser = results.findIndex(r => r.status !== "applied");
        assert.equal(results[loser].status, "failed", JSON.stringify(results[loser]));
        assert.equal(results[loser].reason_code, scenario === "same-todo" ? "claim_owner_mismatch" : "write_scope_conflict");
        assert.equal((await store.readReceipt(requests[loser].operation_id)).status, "missing");
      }
      for (let i = 0; i < results.length; i++) if (results[i].status === "applied") {
        const receipt = await store.readReceipt(requests[i].operation_id);
        assert.equal(receipt.status, "found");
        assert.equal((await claim(stores[i], requests[i])).status, "replayed");
        assert.deepEqual(await store.readReceipt(requests[i].operation_id), receipt);
      }
      assert.equal(arrivals, scenario === "independent" ? 3 : 2);
      const final = await store.loadAuthority();
      if (final.status !== "loaded") throw new Error("missing final authority");
      const winners = requests.filter((_, i) => results[i].status === "applied");
      assert.equal((final.head.leases as JsonObject[]).length, winners.length);
      for (const winner of winners) {
        assert.equal((final.head.todos as JsonObject[]).find(todo => todo.todo_id === winner.todo_id)?.claimed_by,
          winner.claimed_by, "rebasing an independent claim must retain the previous winner");
      }
    });
  }

  test(`${provider} claim contention: newly required acceptance is rechecked`, async t => {
    const {store, contender, requests} = await setup(t);
    let commits = 0;
    const wrapped = beforeCommit(store, async () => {
      commits++;
      const current = await contender.loadAuthority();
      if (current.status !== "loaded") throw new Error("missing fixture");
      assert.equal((await configureGoalAcceptance(contender, {
        goal_id: "goal-a", actor_agent_id: null, operation_id: "require-acceptance",
        expected_provider_revision: current.provider_revision,
        document: {scope: {kind: "all_advancement"}, objective: "Validate the accepted work", non_goals: [],
          criteria: [{id: "outcome", description: "Validation passes", validation_argv: ["python", "-V"]}],
          bindings: []},
      })).status, "applied");
    });
    const result = await claim(wrapped, requests[0]);
    assert.equal(commits, 1);
    assert.equal(result.status, "failed");
    assert.equal((result.goal_acceptance_guard as JsonObject).allowed, false);
    assert.equal((await store.readReceipt(requests[0].operation_id)).status, "missing");
  });

  for (const scenario of ["pinned", "source-change", "exhausted"] as const) {
    test(`${provider} claim contention: ${scenario} stops without a claim receipt`, async t => {
      const {store, contender, requests} = await setup(t);
      const initial = await store.loadAuthority();
      assert.equal(initial.status, "loaded");
      if (initial.status !== "loaded") throw new Error("missing fixture");
      let commits = 0;
      const wrapped = beforeCommit(store, async () => {
        const current = await contender.loadAuthority();
        if (current.status !== "loaded") throw new Error("missing fixture");
        assert.equal((await contender.commitAuthority({operation_id: `peer-${++commits}`,
          expected_provider_revision: current.provider_revision, next_projection: current.head,
          events: [], receipts: []})).status, "applied");
      });
      const result = await claim(wrapped, {...requests[0], ...(scenario === "pinned"
        ? {expected_provider_revision: initial.provider_revision} : {})},
        async () => scenario !== "source-change" || commits === 0);
      assert.equal(commits, scenario === "exhausted" ? 3 : 1);
      assert.equal(result.status, scenario === "source-change" ? "failed" : "conflict", JSON.stringify(result));
      assert.equal((await store.readReceipt(requests[0].operation_id)).status, "missing");
    });
  }
}
