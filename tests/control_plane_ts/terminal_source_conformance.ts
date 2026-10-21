/** One mixed production-scale graph; independent expectations for review,
 * validation, lease retirement and receipt recovery on every real provider. */
import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {executeCoordinationTodoUpdate} from "../../loopx/control_plane/coordination/todo_update.ts";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {executeCoordinationTodoTerminalLifecycle as execute,
  type CoordinationTodoTerminalLifecycleInput} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {authorityProjectionFixture, projectionFixtureAsSchema} from "./authority_projection_fixture.ts";
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
    test(`${provider}: Turn completion binds exact task text through CAS and historical recovery (${schema})`, async t => {
      const {store, contender} = await factory(t);
      const goal = "turn-text-source";
      const body = "Retain all rows. ".repeat(50) + "最后保留 checksum v1.";
      const digest = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
      const target: JsonObject = {todo_id: "todo_turn_source", role: "agent", status: "open", done: false,
        archive_state: "active", text: body, task_class: "advancement_task", claimed_by: "agent-a"};
      const other = {...target, todo_id: "todo_other", text: "Inspect an independent artifact."};
      await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
        next_projection: authorityProjectionFixture(goal, [target, other], [], schema, {handoff_mode: "soft_claim"}),
        events: [], receipts: []});
      const request: CoordinationTodoTerminalLifecycleInput = {
        goal_id: goal, todo_id: String(target.todo_id), expected_role: "agent", command: "complete",
        actor_agent_id: "agent-a", registered_agents: ["agent-a"], lifecycle_grants: [],
        authority_reason: null, decision_outcome: null,
        operation_identity: {kind: "explicit", operation_id: "complete-turn-source"},
        expected_todo_text_sha256: digest(body),
        lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: false,
        requested_no_followup: false, requested_completion_turn_key: "turn-source",
        requested_completion_identity_source: "turn_settlement", linked_successor_todo_ids: [],
        successor_intents: [], note: "Verified artifact", evidence: "Independent artifact check passed",
        reason: null, clear_claim: false, validation_declaration: null, validation_receipt: null,
        completion_policy_request: null, dry_run: false, now: new Date("2026-09-05T23:00:00Z"),
      };
      let head = await loaded(store);
      const missing = await execute(store, {...request, expected_todo_text_sha256: null});
      assert.equal(missing.reason_code, "todo_text_source_unavailable");
      assert.deepEqual(await loaded(store), head);
      assert.equal((await store.readReceipt(request.operation_identity.operation_id)).status, "missing");
      // Old accepted requests had no commitment. Explicit missing proof must
      // retain their fingerprint for receipt recovery, but cannot write anew.
      const legacy = {...request, todo_id: String(other.todo_id), expected_todo_text_sha256: undefined,
        operation_identity: {kind: "explicit" as const, operation_id: "legacy-accepted"}};
      assert.equal((await execute(store, legacy)).status, "applied");
      head = await loaded(store);
      assert.equal((await execute(contender, {...legacy, expected_todo_text_sha256: null},
        async () => {throw new Error("Legacy receipt must recover before missing-source rejection");})).status, "replayed");
      assert.deepEqual(await loaded(store), head);
      const storedTarget = (head.head.todos as JsonObject[]).find(todo => todo.todo_id === target.todo_id)!;
      const storedOther = (head.head.todos as JsonObject[]).find(todo => todo.todo_id === other.todo_id)!;
      const invalid = await execute(store, {...request, expected_todo_text_sha256: "invalid"});
      assert.equal(invalid.reason_code, "invalid_coordination_todo_terminal_lifecycle");
      assert.deepEqual(await loaded(store), head);
      // Notes and other work advance the global revision without replacing the
      // requirement this result proves. Preserve both concurrent changes.
      await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
        operation_id: "unrelated-writes", expected_provider_revision: head.provider_revision,
        projection: head.head, mutations: [
          {kind: "todo_upsert", todo: {...storedTarget, note: "Concurrent observation"}},
          {kind: "todo_upsert", todo: {...storedOther, text: "Different independent requirement"}},
        ]}));
      const committed = await execute(store, request);
      assert.equal(committed.status, "applied", JSON.stringify(committed));
      assert.equal((await execute(store, {...request, expected_todo_text_sha256: null})).reason_code,
        "coordination_operation_identity_mismatch");
      head = await loaded(store);
      const accepted = (head.head.todos as JsonObject[]).find(todo => todo.todo_id === target.todo_id)!;
      assert.equal(accepted.status, "done");
      const nextText = body.replace("checksum v1", "checksum v2");
      await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
        operation_id: "new-requirement", expected_provider_revision: head.provider_revision,
        projection: head.head, mutations: [{kind: "todo_upsert", todo: {
          ...accepted, status: "open", done: false, text: nextText,
        }}]}));
      head = await loaded(store);
      const originalReceipt = await store.readReceipt(request.operation_identity.operation_id);
      const replay = await execute(contender, request,
        async () => {throw new Error("Receipt recovery must precede current-source checks");});
      assert.equal(replay.status, "replayed", JSON.stringify(replay));
      assert.deepEqual(await loaded(store), head);
      assert.deepEqual(await store.readReceipt(request.operation_identity.operation_id), originalReceipt);
      assert.equal((await execute(store, {...request, expected_todo_text_sha256: digest(nextText)})).reason_code,
        "coordination_operation_identity_mismatch");
      const stale = {...request, operation_identity: {kind: "explicit" as const, operation_id: "stale-completion"}};
      assert.equal((await execute(store, stale)).reason_code, "todo_text_changed");
      assert.deepEqual(await loaded(store), head);
      assert.equal((await store.readReceipt("stale-completion")).status, "missing");
      // The source changes after the body check but before the provider write.
      // That write must lose CAS rather than completing the replacement task.
      let raced = false;
      const racingStore = new Proxy(store, {get(owner, key) {
        if (key === "commitAuthority") return async (...args: Parameters<AuthorityStore["commitAuthority"]>) => {
          assert.equal(raced, false);
          raced = true;
          const current = await loaded(contender);
          const todo = (current.head.todos as JsonObject[]).find(row => row.todo_id === target.todo_id)!;
          const changed = await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
            operation_id: "during-completion-cas", expected_provider_revision: current.provider_revision,
            projection: current.head, mutations: [{kind: "todo_upsert", todo: {...todo, text: nextText + " Keep all footnotes."}}]}));
          assert.equal(changed.status, "applied");
          return owner.commitAuthority(...args);
        };
        const member = Reflect.get(owner, key);
        return typeof member === "function" ? member.bind(owner) : member;
      }});
      const racingRequest = {...stale, expected_todo_text_sha256: digest(nextText),
        operation_identity: {kind: "explicit" as const, operation_id: "racing-completion"}};
      const conflict = await execute(racingStore, racingRequest);
      assert.equal(raced, true);
      assert.equal(conflict.status, "conflict", JSON.stringify(conflict));
      assert.equal((await store.readReceipt("racing-completion")).status, "missing");
      assert.equal((await execute(store, racingRequest)).reason_code, "todo_text_changed");
      const afterRace = await loaded(store);
      assert.equal((afterRace.head.todos as JsonObject[]).find(todo => todo.todo_id === target.todo_id)!.status, "open");
    });
    for (const command of ["complete", "supersede"] as const) {
      test(`${provider}: ${command} replaces its own title without admitting duplicate work (${schema})`, async t => {
        const {store, contender} = await factory(t);
        const goal = "terminal-same-title", title = "Continue the next verified slice";
        const origin: JsonObject = {todo_id: "todo_origin", role: "agent", status: "open", done: false,
          archive_state: "active", text: title, task_class: "advancement_task", claimed_by: "agent-a"};
        await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
          next_projection: authorityProjectionFixture(goal, [origin], [], schema, {handoff_mode: "soft_claim"}),
          events: [], receipts: []});
        const policy = {schema_version: "loopx_todo_completion_policy_request_v0", goal_id: goal,
          agent_model: "peer_v1", registered_agents: ["agent-a", "agent-b"], claimed_by: "agent-a",
          next_claimed_by: "agent-a", next_agent_todo: title, next_continuation_policy: null,
          next_excluded_agents: [], self_merged: true, evidence: "Synthetic verified completion"};
        const request: CoordinationTodoTerminalLifecycleInput = {
          goal_id: goal, todo_id: "todo_origin", expected_role: "agent", command,
          actor_agent_id: "agent-a", registered_agents: ["agent-a", "agent-b"], lifecycle_grants: [],
          authority_reason: null, decision_outcome: null,
          operation_identity: {kind: "explicit", operation_id: "replace-origin"},
          lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: false,
          requested_no_followup: false, requested_completion_turn_key: null,
          requested_completion_identity_source: null, linked_successor_todo_ids: [],
          successor_intents: [{role: "agent", text: title, task_class: "advancement_task", claimed_by: "agent-a"}],
          note: null, evidence: "Synthetic verified completion", reason: null, clear_claim: false,
          validation_declaration: null, validation_receipt: null,
          completion_policy_request: command === "complete" ? policy : null,
          dry_run: false, now: new Date("2026-09-05T23:00:00Z"),
        };
        const before = await loaded(store);
        const foreign = await execute(store, {...request, actor_agent_id: "agent-b"});
        assert.equal(foreign.reason_code, "claim_owner_mismatch");
        assert.deepEqual(await loaded(store), before);
        assert.equal((await store.readReceipt("replace-origin")).status, "missing");
        const duplicateUser = {role: "user", text: "Verify the same outcome", task_class: "user_action", bound_agent: "agent-a"};
        const siblings = await execute(store, {...request, successor_intents: [duplicateUser, duplicateUser],
          completion_policy_request: command === "complete" ? {...policy, next_agent_todo: null, next_claimed_by: null} : null});
        assert.equal(siblings.reason_code, "invalid_todo_successor_derivation", JSON.stringify(siblings));
        assert.match(String(siblings.reason), /at most one generated user successor/);
        assert.deepEqual(await loaded(store), before);
        assert.equal((await store.readReceipt("replace-origin")).status, "missing");
        const preview = await execute(store, {...request, dry_run: true});
        assert.equal(preview.status, "planned", JSON.stringify(preview));
        assert.deepEqual(await loaded(store), before);
        assert.equal((await store.readReceipt("replace-origin")).status, "missing");
        const result = await execute(store, request);
        assert.equal(result.status, "applied", JSON.stringify(result));
        const after = await loaded(store), records = after.head.todos as JsonObject[];
        const completed = records.find(todo => todo.todo_id === "todo_origin")!;
        const successor = records.find(todo => todo.todo_id !== "todo_origin")!;
        assert.equal(records.length, 2);
        assert.equal(completed.status, "done");
        assert.equal(successor.status, "open");
        assert.equal(successor.text, title);
        if (command === "complete") assert.equal(successor.unblocks_todo_id, "todo_origin");
        else {
          assert.equal(completed.superseded_by, successor.todo_id);
          assert.equal(successor.unblocks_todo_id, origin.unblocks_todo_id);
        }
        assert.deepEqual(completed.successor_todo_ids, [successor.todo_id]);
        // A still-open peer with this title remains a conflict; completing the
        // new successor cannot create a third copy while that peer is active.
        const peer = {...successor, todo_id: "todo_peer", claimed_by: "agent-b"};
        await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
          operation_id: "later-peer", expected_provider_revision: after.provider_revision,
          projection: after.head, mutations: [{kind: "todo_upsert", todo: peer}]}));
        const later = await loaded(store);
        const duplicate = await execute(store, {...request, todo_id: String(successor.todo_id),
          operation_identity: {kind: "explicit", operation_id: "must-not-duplicate"}});
        assert.equal(duplicate.reason_code, "todo_semantic_duplicate_conflict", JSON.stringify(duplicate));
        assert.equal(duplicate.todo_id, "todo_peer");
        assert.deepEqual(await loaded(store), later);
        assert.equal((await store.readReceipt("must-not-duplicate")).status, "missing");
        assert.equal((await execute(contender, request)).status, "replayed");
        assert.deepEqual(await loaded(store), later);
      });
    }
    test(`${provider}: completion resolves linked work from its canonical head (${schema})`, async t => {
      // Caller snapshots may lag a peer's completion, reopening or role change.
      // The declared link order, rather than caller-supplied facts or store order,
      // determines the first currently open agent successor.
      for (const [role, status, callerRole, callerStatus, expectedLink] of [
        ["agent", "done", "agent", "open", false],
        ["agent", "open", "agent", "done", true],
        ["user", "open", "agent", "open", false],
        ["agent", "open", "user", "open", true],
      ] as const) {
        const {store, contender} = await factory(t);
        const goal = "terminal-linked-source";
        const fixture = productionScaleCoordinationFixture(goal, schema);
        const todos = fixture.projection.todos as JsonObject[];
        const target = todos.find(todo => todo.todo_id === fixture.completion_todo_id)!;
        delete target.completion_validation_required;
        delete target.completion_validation_sha256;
        const candidates = todos.filter(todo => todo.todo_id !== target.todo_id && todo.role === "agent");
        const source = role === "user" ? todos.find(todo => todo.role === "user")! : candidates[0];
        const successor: JsonObject = {...source, role, status, done: status === "done"};
        const other: JsonObject = {...candidates[1], role: "agent", status: "open", done: false};
        const projection = projectionFixtureAsSchema({...fixture.projection,
          todos: todos.map(todo => todo.todo_id === successor.todo_id ? successor
            : todo.todo_id === other.todo_id ? other : todo)}, schema);
        await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
          next_projection: projection, events: [], receipts: []});
        const policy = {schema_version: "loopx_todo_completion_policy_request_v0", goal_id: goal,
          agent_model: "peer_v1", registered_agents: fixture.registered_agents, claimed_by: "agent-a",
          next_claimed_by: null, next_agent_todo: null, next_continuation_policy: null,
          next_excluded_agents: [], self_merged: false, evidence: "Verified linked work",
          linked_successors: [{todo_id: other.todo_id, role: "agent", status: "open"},
            {todo_id: successor.todo_id, role: callerRole, status: callerStatus}]};
        const request: CoordinationTodoTerminalLifecycleInput = {
          goal_id: goal, todo_id: fixture.completion_todo_id, expected_role: "agent", command: "complete",
          actor_agent_id: "agent-a", registered_agents: fixture.registered_agents, lifecycle_grants: [],
          authority_reason: null, decision_outcome: null,
          operation_identity: {kind: "explicit", operation_id: "complete-linked-work"},
          lease_idempotency_key: fixture.completion_lease_idempotency_key,
          lease_expected_version: fixture.completion_lease_expected_version,
          allow_user_gate_auto_acquire: false, requested_no_followup: false,
          requested_completion_turn_key: "complete-linked-work", requested_completion_identity_source: null,
          linked_successor_todo_ids: [String(successor.todo_id)], successor_intents: [],
          note: "Verified completion", evidence: "Verified linked work", reason: null, clear_claim: false,
          validation_declaration: null, validation_receipt: null, completion_policy_request: policy,
          dry_run: false, now: new Date(String(fixture.semantic_cases.terminal_source!.observed_at)),
        };
        const before = await loaded(store);
        const ordered = await execute(store, {...request, dry_run: true,
          linked_successor_todo_ids: [String(other.todo_id), String(successor.todo_id)]});
        assert.equal(ordered.status, "planned", JSON.stringify(ordered));
        assert.equal((ordered.completion_policy as JsonObject).linked_successor_id, other.todo_id);
        assert.deepEqual(await loaded(store), before);
        const result = await execute(store, request);
        assert.equal(result.status, "applied", JSON.stringify(result));
        assert.equal((result.completion_policy as JsonObject).linked_successor_id,
          expectedLink ? successor.todo_id : null);
        const after = await loaded(store);
        assert.deepEqual((after.head.todos as JsonObject[]).filter(todo => todo.todo_id !== target.todo_id),
          (projection.todos as JsonObject[]).filter(todo => todo.todo_id !== target.todo_id));
        const receipt = await store.readReceipt(request.operation_identity.operation_id);
        // Advance the linked record after completion; historical recovery must
        // return the original policy without recomputing it from the new head.
        const changed = {...successor, role: "agent", status: status === "open" ? "done" : "open",
          done: status === "open"};
        await contender.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
          operation_id: "later-linked-change", expected_provider_revision: after.provider_revision,
          projection: after.head, mutations: [{kind: "todo_upsert", todo: changed}]}));
        const advanced = await loaded(store);
        const replay = await execute(contender, {...request, completion_policy_request: {...policy,
          linked_successors: []}}, async () => {throw new Error("Replay must precede fresh source checks");});
        assert.equal(replay.status, "replayed");
        assert.deepEqual(replay.completion_policy, result.completion_policy);
        assert.deepEqual(await loaded(store), advanced);
        assert.deepEqual(await store.readReceipt(request.operation_identity.operation_id), receipt);
      }
    });
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
      // A result association may follow ordinary completion without reopening
      // the work or renewing its retired lease. Exercise the same provider/CAS
      // path as normal updates, including a second provider instance on replay.
      const attach = {goal_id: goal, todo_id: request.todo_id, expected_role: "agent",
        actor_agent_id: "agent-a", registered_agents: fixture.registered_agents,
        operation_id: "attach-terminal-evidence", patch: {}, clear_fields: [],
        planning_intent: {append_explore_result_node_refs: ["bounded-question"]},
        lease_idempotency_key: String(oldLease.idempotency_key),
        lease_expected_version: Number(oldLease.version), dry_run: false,
        now: new Date(String(scenario.after_expiry))};
      for (const changes of [
        {actor_agent_id: "agent-b"},
        {lease_expected_version: Number(oldLease.version) + 1},
        {lease_idempotency_key: "different-execution"},
        {patch: {note: "Unrelated edit"}},
      ]) {
        assert.equal((await executeCoordinationTodoUpdate(store, {...attach, ...changes})).status, "failed");
        assert.deepEqual(await loaded(store), after);
      }
      const associated = await executeCoordinationTodoUpdate(store, attach);
      assert.equal(associated.status, "applied", JSON.stringify(associated));
      const linked = await loaded(contender);
      assert.deepEqual(linked.head.leases, after.head.leases);
      const source = (after.head.todos as JsonObject[]).find(todo => todo.todo_id === request.todo_id)!;
      const linkedSource = (linked.head.todos as JsonObject[]).find(todo => todo.todo_id === request.todo_id)!;
      assert.equal(linkedSource.status, "done");
      assert.deepEqual(linkedSource.completion_continuation, source.completion_continuation);
      assert.ok((linkedSource.explore_result_node_refs as string[]).includes("bounded-question"));
      assert.deepEqual((linked.head.todos as JsonObject[]).filter(todo => todo.todo_id !== request.todo_id),
        (after.head.todos as JsonObject[]).filter(todo => todo.todo_id !== request.todo_id));
      assert.equal((await executeCoordinationTodoUpdate(contender, attach)).status, "replayed");
      assert.deepEqual(await loaded(store), linked);
      assert.equal((await executeCoordinationTodoUpdate(store, {...attach,
        planning_intent: {append_explore_result_node_refs: ["changed-question"]}})).status, "failed");
      assert.deepEqual(await loaded(store), linked);
      assert.deepEqual(await store.readReceipt(commit.operation_identity.operation_id), receipt);
    });
  }
}
