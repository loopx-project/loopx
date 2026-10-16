import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {
  canonicalAuthorityObject,
  canonicalAuthoritySha256,
} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {executeCoordinationTodoTerminalLifecycle as execute,
  type CoordinationTodoTerminalLifecycleInput} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {
  goalAcceptanceTodoDigest,
  normalizeGoalAcceptanceDocument,
} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {localTodoCompletionIdentity} from "../../loopx/control_plane/todos/completion_fence.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";

export function registerTerminalReentryConformance(provider: string, factory: AuthorityStoreConformanceFactory): void {
  for (const schema of ["legacy", "native"] as const) {
    for (const policyClaim of [null, "agent-a"]) {
      test(`${provider}: imported untyped completion repairs once before terminal closeout (${schema}, claim=${policyClaim})`, async t => {
        const {store, contender} = await factory(t);
        const todo: JsonObject = {schema_version: schema === "native" ? "todo_domain_item_v0" : "todo_item_v0",
          todo_id: "todo_untyped", role: "agent", status: "done", done: true, archive_state: "active",
          text: "Repair imported terminal metadata", claimed_by: "agent-a", completion_turn_key: "original-turn",
          note: "Original note", evidence: "Original evidence", reason: "Original reason",
          last_actor_agent_id: "agent-before", updated_at: "2026-09-01T01:00:00Z",
          completion_receipt_id: "tcw_original", completed_at: "2026-09-01T00:00:00Z",
          ...(schema === "legacy" ? {source_section: "Agent Todo"} : {})};
        await store.commitAuthority({operation_id: "import-untyped", expected_provider_revision: null,
          next_projection: authorityProjectionFixture("goal-a", [todo], [], schema, {handoff_mode: "soft_claim"}),
          events: [], receipts: [{original_import: true}]});
        const producerPolicy = {
          schema_version: "loopx_todo_completion_policy_request_v0",
          goal_id: "goal-a", agent_model: "peer_v1", claimed_by: policyClaim,
          registered_agents: ["agent-a", "agent-b"], next_claimed_by: null,
          next_agent_todo: null, next_continuation_policy: null,
          next_excluded_agents: [], self_merged: false, evidence: null,
          linked_successors: [],
        };
        const request: CoordinationTodoTerminalLifecycleInput = {
          goal_id: "goal-a", todo_id: "todo_untyped", expected_role: "agent", command: "complete",
          operation_identity: {kind: "completion_turn"},
          actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], lifecycle_grants: [],
          authority_reason: null, decision_outcome: null, lease_idempotency_key: null, lease_expected_version: null,
          allow_user_gate_auto_acquire: true, requested_no_followup: false,
          requested_completion_turn_key: "original-turn", requested_completion_identity_source: "turn_settlement",
          linked_successor_todo_ids: [], successor_intents: [], note: null, evidence: null, reason: null,
          clear_claim: false, validation_declaration: null, validation_receipt: null,
          completion_policy_request: producerPolicy,
          dry_run: false, now: new Date("2026-09-07T07:00:00Z"),
        };
        const before = await store.loadAuthority(), importedReceipt = await store.readReceipt("import-untyped");
        const rejectedChanges = [{requested_no_followup: true}, {actor_agent_id: "agent-b"},
          {requested_completion_turn_key: "other-turn"},
          {note: "Replace original note"}, {evidence: "Replace original evidence"},
          {reason: "Replace original reason"}, {clear_claim: true}, {decision_outcome: "approve"},
          {completion_policy_request: {claimed_by: "agent-a"}},
          {completion_policy_request: {...producerPolicy, claimed_by: "agent-b"}},
          {completion_policy_request: {...producerPolicy, goal_id: "other-goal"}},
          {completion_policy_request: {...producerPolicy, registered_agents: ["agent-a"]}},
          {completion_policy_request: {...producerPolicy, evidence: "Fresh policy evidence"}},
          {completion_policy_request: {...producerPolicy, next_agent_todo: "Fresh policy successor"}},
          {completion_policy_request: {...producerPolicy, unexpected: true}},
          {successor_intents: [{role: "agent", text: "Cannot add work during repair"}]}] satisfies
          readonly Partial<CoordinationTodoTerminalLifecycleInput>[];
        for (const change of rejectedChanges) {
          assert.equal((await execute(store, {...request, ...change})).status, "failed", JSON.stringify(change));
          assert.deepEqual(await store.loadAuthority(), before);
        }
        const preview = await execute(store, {...request, dry_run: true});
        assert.equal(preview.status, "planned", JSON.stringify(preview));
        assert.equal(preview.changed, true);
        assert.deepEqual(await store.loadAuthority(), before);
        const repair = await execute(store, request);
        assert.equal(repair.status, "applied", JSON.stringify(repair));
        assert.equal(repair.changed, true);
        assert.equal(repair.completion_continuation, "active_goal");
        const repaired = await store.loadAuthority();
        assert.equal(repaired.status, "loaded");
        if (repaired.status !== "loaded") throw new Error("repair head missing");
        assert.equal(before.status, "loaded");
        if (before.status !== "loaded") throw new Error("imported head missing");
        const repairedRows = repaired.head.todos, importedRows = before.head.todos;
        if (!Array.isArray(repairedRows) || repairedRows.length !== 1 ||
            !Array.isArray(importedRows) || importedRows.length !== 1) {
          throw new Error("imported repair Todo missing");
        }
        const item = canonicalAuthorityObject(repairedRows[0], "repaired imported Todo");
        const imported = canonicalAuthorityObject(importedRows[0], "original imported Todo");
        assert.equal(item.completion_continuation, "active_goal");
        assert.equal(item.completion_turn_key, "original-turn");
        assert.equal(item.completed_at, todo.completed_at);
        assert.equal(item.completion_receipt_id, todo.completion_receipt_id);
        assert.deepEqual(item, {...imported, completion_continuation: "active_goal"});
        assert.deepEqual(await store.readReceipt("import-untyped"), importedReceipt);
        assert.equal((await execute(contender, request)).status, "replayed");
        assert.deepEqual(await store.loadAuthority(), repaired);
        const closed = await execute(store, {...request, requested_no_followup: true,
          operation_identity: {kind: "completion_turn"}});
        assert.equal(closed.status, "applied", JSON.stringify(closed));
        assert.equal(closed.completion_continuation, "no_followup");
        assert.equal(closed.completion_recovery, "same_turn_terminal_closeout");
      });
    }
    test(`${provider}: imported untyped completion repair bypasses fresh validators (${schema})`, async t => {
      const {store} = await factory(t);
      const declaration = {validation_command: null, validation_command_argv: ["true"],
        validation_label: "historical-work-check", validation_timeout_seconds: 5};
      const todo: JsonObject = {schema_version: schema === "native" ? "todo_domain_item_v0" : "todo_item_v0",
        todo_id: "todo_untyped_validated", role: "agent", status: "done", done: true, archive_state: "active",
        text: "Preserve historical validation", claimed_by: "agent-a", completion_turn_key: "validated-turn",
        completion_validation_required: true, completion_validation_sha256: canonicalAuthoritySha256(declaration),
        completed_at: "2026-09-01T00:00:00Z", ...(schema === "legacy" ? {source_section: "Agent Todo"} : {})};
      const projection = authorityProjectionFixture("goal-a", [todo], [], schema, {handoff_mode: "soft_claim"});
      const rows = projection.todos;
      if (!Array.isArray(rows) || rows.length !== 1) throw new Error("validated fixture Todo missing");
      const persisted = canonicalAuthorityObject(rows[0], "validated fixture Todo");
      const document = normalizeGoalAcceptanceDocument({objective: "Preserve accepted historical work",
        non_goals: [], criteria: [{id: "criterion-a", description: "Historical acceptance remains valid",
          validation_argv: ["true"], validation_timeout_seconds: 5}],
        bindings: [{todo_id: "todo_untyped_validated", criterion_ids: ["criterion-a"]}]});
      projection.goal_acceptance = {schema_version: "loopx_goal_acceptance_v0", enabled: true, revision: 1,
        digest: canonicalAuthoritySha256(document), document, verification: null,
        bindings: [{todo_id: "todo_untyped_validated", todo_semantic_digest: goalAcceptanceTodoDigest(persisted),
          revision: 1, criterion_ids: ["criterion-a"], confirmed_by: "owner"}]};
      await store.commitAuthority({operation_id: "import-validated-untyped", expected_provider_revision: null,
        next_projection: projection, events: [], receipts: [{historical_validation: true}]});
      const request: CoordinationTodoTerminalLifecycleInput = {
        goal_id: "goal-a", todo_id: "todo_untyped_validated", expected_role: "agent", command: "complete",
        operation_identity: {kind: "completion_turn"}, actor_agent_id: "agent-a",
        registered_agents: ["agent-a"], lifecycle_grants: [], authority_reason: null, decision_outcome: null,
        lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: true,
        requested_no_followup: false, requested_completion_turn_key: "validated-turn",
        requested_completion_identity_source: "turn_settlement", linked_successor_todo_ids: [],
        successor_intents: [], note: null, evidence: null, reason: null, clear_claim: false,
        validation_declaration: null, validation_declaration_sha256: canonicalAuthoritySha256(declaration),
        validation_receipt: null, completion_policy_request: {
          schema_version: "loopx_todo_completion_policy_request_v0",
          goal_id: "goal-a", agent_model: "peer_v1", claimed_by: null,
          registered_agents: ["agent-a"], next_claimed_by: null,
          next_agent_todo: null, next_continuation_policy: null,
          next_excluded_agents: [], self_merged: false, evidence: null,
          linked_successors: [],
        },
        dry_run: false, now: new Date("2026-09-07T07:00:00Z"),
      };
      const repaired = await execute(store, request);
      assert.equal(repaired.status, "applied", JSON.stringify(repaired));
      assert.equal(Object.hasOwn(repaired, "validation_effect"), false);
      assert.equal(Object.hasOwn(repaired, "goal_acceptance_validation_effects"), false);
      const after = await store.loadAuthority();
      assert.equal(after.status, "loaded");
      if (after.status !== "loaded") throw new Error("validated repair head missing");
      const repairedRows = after.head.todos;
      if (!Array.isArray(repairedRows) || repairedRows.length !== 1) {
        throw new Error("validated repair Todo missing");
      }
      assert.deepEqual(canonicalAuthorityObject(repairedRows[0], "validated repaired Todo"),
        {...persisted, completion_continuation: "active_goal"});
    });
    for (const validationRequired of [false, true]) {
    test(`${provider}: migrated terminal reentry preserves completion and closes only continuation (${schema}, validation=${validationRequired})`, async t => {
      const {store, contender} = await factory(t);
      const goal = "goal-a", key = localTodoCompletionIdentity(goal, "todo_migrated");
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
          completion_continuation: "no_followup", completion_recovery: "lifecycle_reentry_terminal_closeout",
          updated_at: "2026-09-07T07:00:00Z"});
        assert.deepEqual(after.head.leases, before.head.leases);
      }
      assert.deepEqual(await store.readReceipt("migrated"), originalReceipt);
      assert.equal((await execute(contender, {...request, now: new Date("2026-09-08T07:00:00Z")},
        async () => {throw new Error("Historical replay needs no fresh authority");})).status,
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
