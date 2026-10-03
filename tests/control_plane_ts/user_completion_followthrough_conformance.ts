import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCoordinationTodoTerminalLifecycle as complete, type CoordinationTodoTerminalLifecycleInput} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleUserCompletionFixture} from "./production_scale_coordination_fixture.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

async function loaded(store: AuthorityStore) {
  const result = await store.loadAuthority();
  assert.ok(result.status === "loaded", "fixture authority missing");
  return result;
}

export function registerUserCompletionFollowthroughConformance(provider: string, factory: AuthorityStoreConformanceFactory): void {
  for (const schema of ["legacy", "native"] as const) {
    for (const outcome of [null, "cancel"] as const) {
      test(`${provider}: ordinary bound User ${outcome ?? "completion"} closes without execution authority (${schema})`, async t => {
        const {store, contender} = await factory(t);
        const goal = "ordinary-user-closure";
        const source = {todo_id: "todo_action", role: "user", task_class: "user_action", status: "open",
          text: "Read the observation request", done: false, archive_state: "active", bound_agent: "agent-a",
          unblocks_todo_id: "todo_dependent"};
        const target = {todo_id: "todo_dependent", role: "agent", task_class: "advancement_task", status: "blocked",
          text: "Continue after the observation", done: false, archive_state: "active", claimed_by: "agent-a"};
        await store.commitAuthority({operation_id: "seed-action", expected_provider_revision: null,
          next_projection: authorityProjectionFixture(goal, [source, target], [], schema, {handoff_mode: "hard_lease"}),
          events: [], receipts: []});
        const before = await loaded(store);
        const request: CoordinationTodoTerminalLifecycleInput = {goal_id: goal, todo_id: source.todo_id, expected_role: "user", command: "complete",
          actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], lifecycle_grants: [],
          authority_reason: null, decision_outcome: outcome, operation_identity: {kind: "explicit", operation_id: "close-action"},
          lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: true,
          requested_no_followup: false, requested_completion_turn_key: null, requested_completion_identity_source: null,
          linked_successor_todo_ids: [], successor_intents: [], note: null, evidence: "Synthetic ordinary observation",
          reason: null, clear_claim: false, validation_declaration: null, validation_receipt: null,
          completion_policy_request: null, dry_run: false, now: new Date("2026-09-18T06:00:00Z")};
        for (const change of [{actor_agent_id: "agent-b"}, {actor_agent_id: null},
          {lease_idempotency_key: "stale"}, {decision_outcome: "approve" as const}, {decision_outcome: "reject" as const}]) {
          assert.equal((await complete(store, {...request, ...change})).status, "failed");
          assert.deepEqual(await loaded(store), before);
          assert.equal((await store.readReceipt("close-action")).status, "missing");
        }
        assert.equal((await complete(store, {...request, dry_run: true})).status, "planned");
        assert.deepEqual(await loaded(store), before);
        const originalCommit = store.commitAuthority.bind(store);
        let raced = false;
        store.commitAuthority = async commit => {
          if (!raced && commit.operation_id === "close-action") {
            raced = true;
            await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
              operation_id: "race", expected_provider_revision: before.provider_revision,
              projection: before.head, mutations: []}));
          }
          return originalCommit(commit);
        };
        assert.notEqual((await complete(store, request)).status, "applied");
        assert.deepEqual((await loaded(store)).head.todos, before.head.todos);
        assert.equal((await store.readReceipt("close-action")).status, "missing");
        store.commitAuthority = async commit => {await originalCommit(commit); throw new Error("injected lost response");};
        const result = await complete(store, request);
        store.commitAuthority = originalCommit;
        assert.equal(result.status, "recovered", JSON.stringify(result));
        const after = await loaded(store);
        const rows = after.head.todos as JsonObject[];
        assert.equal(rows.find(row => row.todo_id === source.todo_id)!.status, "done");
        const dependent = rows.find(row => row.todo_id === target.todo_id)!;
        assert.equal(dependent.status, outcome === "cancel" ? "blocked" : "open");
        if (outcome === "cancel") assert.deepEqual(dependent,
          (before.head.todos as JsonObject[]).find(row => row.todo_id === target.todo_id));
        assert.deepEqual(after.head.leases, before.head.leases, "closure must not mint or transfer a lease");
        assert.equal((result.unblock_resume as JsonObject).state, outcome === "cancel" ? "decision_cancelled" : "resumed");
        const receipt = await store.readReceipt("close-action");
        assert.equal((await complete(contender, request, async () => {throw new Error("replay precedes admission");})).status, "replayed");
        assert.deepEqual(await loaded(store), after);
        assert.deepEqual(await store.readReceipt("close-action"), receipt);
        assert.equal((await complete(store, {...request, decision_outcome: outcome === null ? "cancel" : null})).reason_code,
          "coordination_operation_identity_mismatch");
      });
    }
    for (const outcome of [null, "approve", "reject", "cancel"] as const) {
      test(`${provider}: linked User ${outcome ?? "closure without decision"} is atomic and replay-safe (${schema})`, async t => {
        const {store, contender} = await factory(t);
        const goal = "user-decision-followthrough";
        const fixture = productionScaleUserCompletionFixture(goal, schema, outcome === "approve");
        await store.commitAuthority({operation_id: "seed-user-decision", expected_provider_revision: null,
          next_projection: fixture.projection, events: [], receipts: []});
        const before = await loaded(store);
        const request: CoordinationTodoTerminalLifecycleInput = {goal_id: goal, todo_id: fixture.source, expected_role: "user", command: "complete",
          actor_agent_id: "agent-a", registered_agents: fixture.registered_agents, lifecycle_grants: [],
          authority_reason: null, decision_outcome: outcome, operation_identity: {kind: "explicit" as const, operation_id: "decide"}, lease_idempotency_key: null,
          lease_expected_version: null, allow_user_gate_auto_acquire: true, requested_no_followup: false,
          requested_completion_turn_key: null, requested_completion_identity_source: null,
          linked_successor_todo_ids: [], successor_intents: [], note: null, evidence: "Synthetic exact owner decision",
          reason: null, clear_claim: false, validation_declaration: null, validation_receipt: null,
          completion_policy_request: null, dry_run: false, now: new Date("2026-09-18T06:00:00Z")};
        const preview = await complete(store, {...request, dry_run: true});
        assert.equal(preview.status, "planned", JSON.stringify(preview));
        assert.deepEqual(await loaded(store), before);
        assert.equal((await complete(store, {...request, actor_agent_id: "agent-b"})).status, "failed");
        assert.deepEqual(await loaded(store), before);
        // Inject a real competing write at commit: source, dependent and receipt
        // must all remain untouched when the shared CAS loses.
        const originalCommit = store.commitAuthority.bind(store);
        let raced = false;
        store.commitAuthority = async commit => {
          if (!raced && commit.operation_id === "decide") {
            raced = true;
            await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
              operation_id: "competing-write", expected_provider_revision: before.provider_revision,
              projection: before.head, mutations: []}));
          }
          return originalCommit(commit);
        };
        const lost = await complete(store, request);
        assert.notEqual(lost.status, "applied");
        const racedHead = await loaded(store);
        assert.deepEqual(racedHead.head.todos, before.head.todos);
        assert.equal((await store.readReceipt("decide")).status, "missing");
        // The write succeeds but its transport response is lost. Recover the
        // receipt rather than applying the dependent effect a second time.
        store.commitAuthority = async commit => {
          await originalCommit(commit);
          throw new Error("injected lost response");
        };
        const applied = await complete(store, request);
        store.commitAuthority = originalCommit;
        assert.equal(applied.status, "recovered", JSON.stringify(applied));
        const after = await loaded(store);
        const rows = after.head.todos as JsonObject[];
        const dependent = rows.find(row => row.todo_id === fixture.target)!;
        assert.equal(rows.find(row => row.todo_id === fixture.source)!.status, "done");
        assert.equal(dependent.status, "blocked");
        assert.equal(dependent.claimed_by, "agent-a");
        if (outcome === null) {
          assert.deepEqual(dependent, (before.head.todos as JsonObject[]).find(row => row.todo_id === fixture.target),
            "Gate closure without a decision must not change dependent authority or state");
          assert.equal(applied.unblock_resume == null, true);
          assert.equal(applied.decision_scope_resolution == null, true);
        } else if (outcome === "approve") {
          assert.deepEqual(dependent.required_decision_scopes, []);
          assert.equal((applied.unblock_resume as JsonObject).state, "other_user_blockers_active");
        } else {
          assert.deepEqual(dependent.required_decision_scopes, [fixture.scope]);
          assert.equal((dependent.decision_scope_outcomes as JsonObject[]).length, 1);
          assert.equal((dependent.decision_scope_outcomes as JsonObject[])[0].outcome, outcome);
          assert.equal((dependent.decision_scope_outcomes as JsonObject[])[0].source_todo_id, fixture.source);
        }
        assert.deepEqual(rows.filter(row => ![fixture.source, fixture.target].includes(String(row.todo_id))),
          (before.head.todos as JsonObject[]).filter(row => ![fixture.source, fixture.target].includes(String(row.todo_id))));
        assert.deepEqual((after.head.leases as JsonObject[]).filter(row => row.todo_id !== fixture.source),
          before.head.leases, "approval must not mint or transfer the dependent task lease");
        const gateLease = (after.head.leases as JsonObject[]).find(row => row.todo_id === fixture.source)!;
        assert.equal(gateLease.status, "released", "the exact gate auto-acquire is released by terminal completion");
        const receipt = await store.readReceipt("decide");
        assert.equal((await complete(contender, request,
          async () => {throw new Error("replay must precede current admission");})).status, "replayed");
        assert.deepEqual(await loaded(store), after);
        assert.deepEqual(await store.readReceipt("decide"), receipt);
        assert.equal((await complete(store, {...request, decision_outcome: outcome === "approve" ? "reject" : "approve"})).reason_code,
          "coordination_operation_identity_mismatch");
      });
    }
  }
}
