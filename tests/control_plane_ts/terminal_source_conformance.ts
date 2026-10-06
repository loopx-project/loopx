/** One mixed production-scale graph; independent expectations for review,
 * validation, lease retirement and receipt recovery on every real provider. */
import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {executeCoordinationTodoTerminalLifecycle as execute,
  type CoordinationTodoTerminalLifecycleInput} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleCoordinationFixture,
  PRODUCTION_SCALE_VALIDATION_DECLARATION as declaration} from "./production_scale_coordination_fixture.ts";

async function loaded(store: AuthorityStore) {
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") throw new Error("fixture head missing");
  return head;
}

const validation = {schema_version: "issue_fix_validation_command_v0", command_label: declaration.validation_label,
  passed: true, exit_code: 0, status: "passed", summary: "Synthetic validation passed",
  stdout_captured: false, stderr_captured: false, local_path_captured: false};

export function registerTerminalSourceConformance(provider: string, factory: AuthorityStoreConformanceFactory): void {
  for (const schema of ["legacy", "native"] as const) {
    test(`${provider}: supersede links existing work and retires its original lease atomically (${schema})`, async t => {
      const {store, contender} = await factory(t);
      const goal = "terminal-existing-successor";
      const fixture = productionScaleCoordinationFixture(goal, schema);
      await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        next_projection: fixture.projection, events: [], receipts: []});
      const before = await loaded(store);
      const todos = before.head.todos as JsonObject[];
      const successor = todos.find(todo => todo.todo_id !== fixture.completion_todo_id && todo.status === "open")!;
      const originalLease = (before.head.leases as JsonObject[]).find(lease => lease.todo_id === fixture.completion_todo_id)!;
      const request: CoordinationTodoTerminalLifecycleInput = {
        goal_id: goal, todo_id: fixture.completion_todo_id, expected_role: "agent", command: "supersede",
        actor_agent_id: "agent-a", registered_agents: fixture.registered_agents, lifecycle_grants: [],
        authority_reason: null, decision_outcome: null, operation_identity: {kind: "explicit", operation_id: "replace-with-existing"},
        lease_idempotency_key: String(originalLease.idempotency_key), lease_expected_version: Number(originalLease.version),
        allow_user_gate_auto_acquire: false, requested_no_followup: false, requested_completion_turn_key: null,
        requested_completion_identity_source: null, linked_successor_todo_ids: [String(successor.todo_id)],
        successor_intents: [], note: "superseded", evidence: null, reason: "Continue the existing declared work.",
        clear_claim: false, validation_declaration: null, validation_receipt: null, completion_policy_request: null,
        dry_run: false, now: new Date(String(fixture.semantic_cases.terminal_source!.observed_at)),
      };
      const foreign = await factory(t);
      const foreignFixture = productionScaleCoordinationFixture("another-goal", schema);
      const foreignTodo = (foreignFixture.projection.todos as JsonObject[]).find(todo => todo.status === "open")!;
      foreignTodo.todo_id = "todo_foreign_successor";
      await foreign.store.commitAuthority({operation_id: "seed-foreign", expected_provider_revision: null,
        next_projection: foreignFixture.projection, events: [], receipts: []});
      const foreignBefore = await loaded(foreign.store);
      for (const [changes, reason] of [
        [{linked_successor_todo_ids: [request.todo_id]}, "todo_successor_cycle"],
        [{linked_successor_todo_ids: ["todo_missing_successor"]}, "todo_successor_not_found"],
        [{linked_successor_todo_ids: ["todo_foreign_successor"]}, "todo_successor_not_found"],
        [{lease_expected_version: Number(originalLease.version) + 1}, "version_mismatch"],
        [{actor_agent_id: "unregistered"}, "actor_not_registered"],
      ] as const) {
        const rejected = await execute(store, {...request, ...changes});
        assert.equal(rejected.reason_code, reason, JSON.stringify(rejected));
        assert.deepEqual(await loaded(store), before);
      }
      assert.deepEqual(await loaded(foreign.store), foreignBefore);
      const refusedSource = await execute(store, request, async () => false);
      assert.equal(refusedSource.reason_code, "authority_source_changed", JSON.stringify(refusedSource));
      assert.deepEqual(await loaded(store), before);
      const preview = await execute(store, {...request, dry_run: true});
      assert.equal(preview.status, "planned");
      assert.deepEqual(await loaded(store), before);
      const lostResponse = new Proxy(store, {get(target, key) {
        if (key === "commitAuthority") return async (...args: Parameters<AuthorityStore["commitAuthority"]>) => {
          await target.commitAuthority(...args);
          throw new Error("Synthetic lost retirement response");
        };
        const member = Reflect.get(target, key);
        return typeof member === "function" ? member.bind(target) : member;
      }});
      const accepted = await execute(lostResponse, request);
      assert.ok(["recovered", "replayed"].includes(String(accepted.status)), JSON.stringify(accepted));
      const after = await loaded(store);
      const source = (after.head.todos as JsonObject[]).find(todo => todo.todo_id === request.todo_id)!;
      assert.equal(source.status, "done");
      assert.deepEqual(source.successor_todo_ids, [successor.todo_id]);
      assert.equal(source.superseded_by, successor.todo_id);
      assert.deepEqual((after.head.todos as JsonObject[]).filter(todo => todo.todo_id !== request.todo_id),
        todos.filter(todo => todo.todo_id !== request.todo_id));
      assert.deepEqual((after.head.leases as JsonObject[]).find(lease => lease.todo_id === request.todo_id),
        {...originalLease, status: "released", updated_at: request.now.toISOString().replace(".000Z", "Z")});
      const replay = await execute(contender, request, async () => {throw new Error("Replay precedes fresh source checks");});
      assert.equal(replay.status, "replayed");
      assert.deepEqual(await loaded(store), after);
      assert.equal((await execute(store, {...request, linked_successor_todo_ids: []})).reason_code,
        "coordination_operation_identity_mismatch");
      const another = todos.find(todo => todo.todo_id !== request.todo_id && todo.todo_id !== successor.todo_id)!;
      const late = await execute(store, {...request, operation_identity: {kind: "explicit", operation_id: "late-link"},
        linked_successor_todo_ids: [String(another.todo_id)]});
      assert.equal(late.reason_code, "todo_terminal_successor_intent_after_completion", JSON.stringify(late));
      assert.deepEqual(await loaded(store), after);
    });
    test(`${provider}: terminal review and validation bind one complete ${schema} source`, async t => {
      const {store, contender} = await factory(t);
      const goal = "terminal-source";
      const fixture = productionScaleCoordinationFixture(goal, schema);
      const scenario = fixture.semantic_cases.terminal_source!;
      assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        next_projection: fixture.projection, events: [], receipts: []})).status, "applied");
      const before = await loaded(store);
      const request: CoordinationTodoTerminalLifecycleInput = {goal_id: goal, todo_id: fixture.completion_todo_id,
        expected_role: "agent", command: "complete", actor_agent_id: "agent-a", registered_agents: fixture.registered_agents,
        lifecycle_grants: [], authority_reason: null, decision_outcome: null, operation_identity: {kind: "explicit" as const, operation_id: "reviewed-close"},
        lease_idempotency_key: fixture.completion_lease_idempotency_key,
        lease_expected_version: fixture.completion_lease_expected_version,
        allow_user_gate_auto_acquire: false, requested_no_followup: true,
        requested_completion_turn_key: "reviewed-close", requested_completion_identity_source: null,
        linked_successor_todo_ids: [], successor_intents: [], note: "Reviewed result", evidence: "Verified result", reason: null,
        clear_claim: false, validation_declaration: null, validation_receipt: null,
        validation_declaration_sha256: canonicalAuthoritySha256(declaration),
        completion_policy_request: null, dry_run: false, now: new Date(String(scenario.observed_at)),
        review_basis: {provider_revision: before.provider_revision, registry_sha256: "a".repeat(64)},
        validation_source_provider_revision: null};
      for (const [changes, code] of [
        [{actor_agent_id: "agent-b"}, "claim_owner_mismatch"],
        [{lease_expected_version: 999}, "version_mismatch"],
        [{review_basis: {...request.review_basis!, provider_revision: "stale"}}, scenario.changed_source_rejection],
        [{validation_receipt: validation}, scenario.missing_source_rejection],
      ] as const) {
        const result = await execute(store, {...request, ...changes});
        assert.equal(result.reason_code, code, JSON.stringify(result));
        assert.deepEqual(await loaded(store), before);
        assert.equal((await store.readReceipt(request.operation_identity.operation_id)).status, "missing");
      }
      assert.equal((await execute(store, {...request, validation_declaration: declaration, dry_run: true})).status, "planned");
      assert.deepEqual(await loaded(store), before);
      const resolve = await execute(store, request);
      assert.equal(resolve.status, "resolve_validation");
      assert.equal(resolve.validation_declaration_sha256, request.validation_declaration_sha256);
      const withDeclaration = {...request, validation_declaration: declaration,
        validation_source_provider_revision: before.provider_revision};
      const effect = await execute(store, withDeclaration);
      assert.equal(effect.status, "execute_validation", JSON.stringify(effect));
      assert.equal(effect.provider_revision, before.provider_revision);
      const resume = {...withDeclaration, validation_receipt: validation};
      assert.equal((await execute(store, {...resume, now: new Date(String(scenario.after_expiry))})).reason_code,
        "handoff_mode_requires_lease");
      assert.deepEqual(await loaded(store), before);
      const other = (before.head.todos as JsonObject[]).find(todo => todo.todo_id !== request.todo_id)!;
      assert.equal((await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
        operation_id: "peer-during-validation", expected_provider_revision: before.provider_revision, projection: before.head,
        mutations: [{kind: "todo_upsert", todo: {...other, note: "Concurrent change"}}]}))).status, "applied");
      const advanced = await loaded(store);
      assert.equal((await execute(store, resume)).reason_code, scenario.changed_source_rejection);
      // The ordinary CLI also binds validation, without a Chat review basis.
      assert.equal((await execute(store, {...resume, review_basis: undefined})).reason_code, scenario.changed_source_rejection);
      assert.deepEqual(await loaded(store), advanced);
      const fresh = {...withDeclaration, validation_source_provider_revision: advanced.provider_revision,
        operation_identity: {kind: "explicit" as const, operation_id: "fresh-review"}, review_basis: {
        ...request.review_basis!, provider_revision: advanced.provider_revision}};
      const freshEffect = await execute(store, fresh);
      assert.equal(freshEffect.status, "execute_validation");
      const commit = {...fresh, validation_receipt: validation, validation_source_provider_revision: advanced.provider_revision};
      // Real commit, deliberately lost response, then a different provider instance recovers it.
      const losingResponse = new Proxy(store, {get(target, key) {
        if (key === "commitAuthority") return async (...args: Parameters<AuthorityStore["commitAuthority"]>) => {
          const result = await target.commitAuthority(...args);
          assert.equal(result.status, "applied");
          throw new Error("Synthetic response loss");
        };
        const member = Reflect.get(target, key);
        return typeof member === "function" ? member.bind(target) : member;
      }});
      const accepted = await execute(losingResponse, commit);
      assert.ok(["recovered", "replayed"].includes(String(accepted.status)), JSON.stringify(accepted));
      const after = await loaded(store);
      assert.equal((after.head.todos as JsonObject[]).find(todo => todo.todo_id === request.todo_id)!.status, "done");
      const oldLease = (advanced.head.leases as JsonObject[]).find(lease => lease.todo_id === request.todo_id)!;
      assert.deepEqual((after.head.leases as JsonObject[]).find(lease => lease.todo_id === request.todo_id),
        {...oldLease, status: "released", updated_at: scenario.observed_at});
      assert.deepEqual((after.head.todos as JsonObject[]).filter(todo => todo.todo_id !== request.todo_id),
        (advanced.head.todos as JsonObject[]).filter(todo => todo.todo_id !== request.todo_id));
      const receipt = await store.readReceipt(commit.operation_identity.operation_id);
      const replay = await execute(contender, {...commit, validation_declaration: null, validation_receipt: null,
        validation_source_provider_revision: null, now: new Date(String(scenario.after_expiry))},
        async () => {throw new Error("Historical recovery must precede source checks");});
      assert.equal(replay.status, "replayed");
      assert.equal(replay.changed, false);
      for (const changes of [{note: "Changed acknowledgement"}, {evidence: "Changed evidence"},
        {review_basis: {...fresh.review_basis, registry_sha256: "b".repeat(64)}}]) {
        assert.equal((await execute(store, {...commit, ...changes})).reason_code, "coordination_operation_identity_mismatch");
      }
      assert.deepEqual(await loaded(store), after);
      assert.deepEqual(await store.readReceipt(commit.operation_identity.operation_id), receipt);
      const lateLink = await execute(store, {...commit, requested_no_followup: false,
        review_basis: {...fresh.review_basis, provider_revision: after.provider_revision},
        validation_source_provider_revision: after.provider_revision,
        validation_receipt: null,
        operation_identity: {kind: "explicit", operation_id: "late-completion-link"},
        linked_successor_todo_ids: [String(other.todo_id)]});
      assert.equal(lateLink.reason_code, "todo_terminal_successor_intent_after_completion", JSON.stringify(lateLink));
      assert.deepEqual(await loaded(store), after);
    });
  }
}
