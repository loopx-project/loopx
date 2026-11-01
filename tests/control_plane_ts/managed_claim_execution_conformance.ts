import assert from "node:assert/strict";
import test from "node:test";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {executeCoordinationTodoClaim as claim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {executeCoordinationTodoTerminalLifecycle as complete} from "../../loopx/control_plane/coordination/todo_terminal_lifecycle.ts";
import {executeCoordinationTodoUpdate as update} from "../../loopx/control_plane/coordination/todo_update.ts";
import {executeCanonicalTaskLeaseLifecycle as maintain} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {todoUpdateLeaseRecovery} from "../../loopx/control_plane/coordination/task_lease_proof.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";

async function loaded(store: AuthorityStore) {
  const value = await store.loadAuthority();
  if (value.status !== "loaded") throw new Error("synthetic authority unavailable");
  return value;
}
export function registerManagedClaimExecutionConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  async function setup(t: test.TestContext, mode = "hard_lease") {
    const {store} = await factory(t);
    const projection = authorityProjectionFixture("goal-a", [{todo_id: "todo_managed", role: "agent",
      status: "open", done: false, archive_state: "active", text: "Deliver validated output",
      task_class: "advancement_task", required_write_scopes: ["src/**"]}], [], "native", {handoff_mode: mode});
    assert.equal((await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
      next_projection: projection, events: [], receipts: []})).status, "applied");
    const now = new Date("2026-09-13T10:00:00Z");
    const request = {goal_id: "goal-a", todo_id: "todo_managed", claimed_by: "agent-a", actor_agent_id: "agent-a",
      expected_role: "agent", registered_agents: ["agent-a", "agent-b"], operation_id: "claim-a",
      manage_execution: true, dry_run: false, now};
    const progress = {goal_id: request.goal_id, todo_id: request.todo_id, actor_agent_id: "agent-a",
      expected_role: "agent", registered_agents: request.registered_agents, operation_id: "progress-a",
      patch: {note: "Validated progress"}, clear_fields: [], dry_run: false, now,
      claim_operation_id: request.operation_id};
    return {store, request, progress};
  }
  function completion(request: Awaited<ReturnType<typeof setup>>["request"]) {
    return {goal_id: request.goal_id, todo_id: request.todo_id, actor_agent_id: "agent-a",
      expected_role: "agent" as const, registered_agents: request.registered_agents, command: "complete" as const,
      operation_identity: {kind: "completion_turn" as const}, claim_operation_id: request.operation_id,
      lifecycle_grants: [], authority_reason: null, decision_outcome: null,
      lease_idempotency_key: null, lease_expected_version: null, allow_user_gate_auto_acquire: false,
      requested_no_followup: false, requested_completion_turn_key: "original-turn",
      requested_completion_identity_source: "turn_settlement" as const, linked_successor_todo_ids: [],
      successor_intents: [], note: null, evidence: "evidence:validated-output", reason: null,
      clear_claim: false, validation_declaration: null, validation_receipt: null,
      completion_policy_request: null, dry_run: false, now: request.now};
  }
  test(`${provider} managed execution: terminal recovery uses ordinary claim admission without effects`, async t => {
    for (const active of [false, true]) {
      const {store, request} = await setup(t);
      if (active) assert.equal((await claim(store, request)).status, "applied");
      const original = {...completion(request), claim_operation_id: undefined};
      const before = await loaded(store);
      const rejected = await complete(store, original);
      assert.equal(rejected.status, "failed");
      assert.equal(rejected.reason_code, active ? "lease_fence_required" : "handoff_mode_requires_lease");
      const recovery = rejected.recovery as JsonObject;
      assert.equal(recovery.action, "claim_task");
      assert.equal(recovery.execution_authority_granted, false);
      assert.deepEqual((recovery.claim as JsonObject).cli_args, ["todo", "claim", "--goal-id", request.goal_id,
        "--todo-id", request.todo_id, "--agent-id", "agent-a", "--claimed-by", "agent-a"]);
      assert.deepEqual((recovery.retry as JsonObject).requires_flags, ["--claim-operation-id"]);
      assert.equal(recovery.inspect, undefined);
      assert.equal(recovery.acquire, undefined);
      assert.deepEqual(await loaded(store), before, "the completion refusal and its claim preview never write");
      const prepared = await claim(store, {...request, operation_id: "prepared-after-refusal"});
      assert.ok(["applied", "no_change"].includes(String(prepared.status)), JSON.stringify(prepared));
      const retry = {...original, claim_operation_id: String(prepared.claim_operation_id)};
      assert.equal((await complete(store, retry)).status, "applied");
      const done = await loaded(store);
      assert.equal((done.head.leases as JsonObject[])[0]!.status, "released");
      assert.equal((await complete(store, retry)).status, "replayed");
      assert.deepEqual(await loaded(store), done, "recovery keeps the original terminal identity and releases once");
    }
  });
  test(`${provider} managed execution: terminal advice does not replace explicit proof or bypass task blockers`, async t => {
    for (const scenario of ["explicit-proof", "foreign-holder", "excluded", "blocked", "overlapping-work"]) {
      const {store, request} = await setup(t);
      let original = {...completion(request), claim_operation_id: undefined} as Parameters<typeof complete>[1];
      if (scenario === "explicit-proof") original = {...original, lease_idempotency_key: "unproven-key"};
      if (scenario === "foreign-holder") {
        assert.equal((await claim(store, {...request, actor_agent_id: "agent-b", claimed_by: "agent-b"})).status, "applied");
      }
      if (["excluded", "blocked", "overlapping-work"].includes(scenario)) {
        const head = await loaded(store); const todo = (head.head.todos as JsonObject[])[0]!;
        const change = scenario === "excluded" ? {...todo, excluded_agents: ["agent-a"]}
          : scenario === "blocked" ? {...todo, status: "blocked"}
          : {...todo, todo_id: "todo_other", text: "Independent work occupying the same scope"};
        assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
          operation_id: "prepare-blocker", expected_provider_revision: head.provider_revision, projection: head.head,
          mutations: [{kind: "todo_upsert", todo: change}]}))).status, "applied");
        if (scenario === "overlapping-work") assert.equal((await claim(store, {...request, todo_id: "todo_other",
          actor_agent_id: "agent-b", claimed_by: "agent-b", operation_id: "occupy-scope"})).status, "applied");
      }
      const before = await loaded(store); const result = await complete(store, original);
      assert.equal(result.status, "failed", scenario);
      assert.notEqual((result.recovery as JsonObject | undefined)?.action, "claim_task", scenario);
      if (scenario === "explicit-proof") assert.equal(result.recovery, undefined);
      assert.deepEqual(await loaded(store), before, scenario);
    }
  });
  test(`${provider} managed execution: unavailable recovery observation retains the original terminal refusal`, async t => {
    const {store, request} = await setup(t); const before = await loaded(store);
    const unavailable = new Proxy(store, {get(target, key) {
      if (key === "readReceipt") return async (id: string) => {
        if (id.startsWith("terminal-claim-preview-")) throw new Error("synthetic observation unavailable");
        return target.readReceipt(id);
      };
      const value = Reflect.get(target, key, target);
      return typeof value === "function" ? value.bind(target) : value;
    }});
    const result = await complete(unavailable, {...completion(request), claim_operation_id: undefined});
    assert.equal(result.reason_code, "handoff_mode_requires_lease");
    assert.equal(result.recovery, undefined);
    assert.deepEqual(await loaded(store), before);
  });
  test(`${provider} managed execution: supersede never recommends an unsupported claim-reference retry`, async t => {
    const {store, request} = await setup(t);
    const before = await loaded(store);
    const rejected = await complete(store, {...completion(request), command: "supersede" as const,
      claim_operation_id: undefined, operation_identity: {kind: "explicit" as const, operation_id: "terminal-supersede"},
      requested_completion_turn_key: null, reason: "Continue through declared successor work"});
    assert.equal(rejected.status, "failed");
    assert.equal(rejected.recovery, undefined);
    assert.deepEqual(await loaded(store), before);
  });
  test(`${provider} managed execution: ordinary claim/progress carry one execution, renew only near expiry, recover without writes`, async t => {
    const {store, request, progress} = await setup(t);
    const before = await loaded(store);
    const preview = await claim(store, {...request, dry_run: true});
    assert.equal(preview.status, "planned", JSON.stringify(preview));
    assert.deepEqual(await loaded(store), before);
    const accepted = await claim(store, request);
    assert.equal(accepted.status, "applied", JSON.stringify(accepted));
    const original = accepted.lease as JsonObject;
    assert.deepEqual(original.write_scopes, ["src/**"]);
    assert.equal(original.expires_at, "2026-09-13T10:45:00Z");
    assert.equal(accepted.claim_operation_id, "claim-a");
    assert.equal((await update(store, progress)).status, "applied");
    let current = await loaded(store);
    assert.deepEqual((current.head.leases as JsonObject[])[0], original, "early activity never renews");
    const renewed = await update(store, {...progress, operation_id: "progress-near-expiry",
      patch: {note: "New validated evidence"}, now: new Date("2026-09-13T10:31:00Z")});
    assert.equal(renewed.status, "applied", JSON.stringify(renewed));
    current = await loaded(store);
    const lease = (current.head.leases as JsonObject[])[0]!;
    assert.equal(lease.version, 2); assert.equal(lease.lease_epoch, original.lease_epoch);
    assert.equal(lease.idempotency_key, original.idempotency_key);
    assert.equal(lease.expires_at, "2026-09-13T11:16:00Z");
    assert.equal((current.head.todos as JsonObject[])[0]!.note, "New validated evidence");
    assert.equal((await update(store, {...progress, operation_id: "progress-near-expiry",
      patch: {note: "New validated evidence"}, now: new Date("2026-09-13T10:55:00Z")})).status, "replayed");
    assert.deepEqual(await loaded(store), current, "lost response recovery cannot renew again");
    assert.equal((await claim(store, {...request, now: new Date("2026-09-13T10:55:00Z")})).status, "replayed");
    assert.deepEqual(await loaded(store), current, "claim replay is read-only");
  });
  test(`${provider} managed execution: missing/foreign/expired/replaced execution references cannot write or reacquire`, async t => {
    const {store, request, progress} = await setup(t);
    const accepted = await claim(store, request);
    const lease = accepted.lease as JsonObject;
    const before = await loaded(store);
    for (const change of [{claim_operation_id: "missing"}, {actor_agent_id: "agent-b"},
      {now: new Date("2026-09-13T10:45:00Z")},
      {lease_idempotency_key: lease.idempotency_key as string, lease_expected_version: 1}]) {
      assert.equal((await update(store, {...progress, ...change})).status, "failed");
      assert.deepEqual(await loaded(store), before);
    }
    const released = await maintain(store, {operation: "release", goal_id: request.goal_id, todo_id: request.todo_id,
      owner: "agent-a", idempotency_key: String(lease.idempotency_key), expected_version: 1,
      ttl_seconds: null, registered_agents: request.registered_agents, now: request.now});
    assert.equal(released.status, "applied", JSON.stringify(released));
    const replacement = await claim(store, {...request, operation_id: "new-execution"});
    assert.equal(replacement.status, "applied", JSON.stringify(replacement));
    const replaced = await loaded(store);
    assert.equal((await update(store, progress)).reason_code, "claim_execution_unavailable");
    assert.deepEqual(await loaded(store), replaced);
    assert.equal((await claim(store, request)).status, "failed", "old claim never revives retired execution");
  });
  test(`${provider} managed execution: claim conflict, exclusion, policy loss and provider CAS preserve the original task`, async t => {
    const {store, request, progress} = await setup(t);
    assert.equal((await claim(store, request)).status, "applied");
    const before = await loaded(store);
    assert.equal((await claim(store, {...request, actor_agent_id: "agent-b", claimed_by: "agent-b",
      operation_id: "foreign"})).status, "failed");
    assert.deepEqual(await loaded(store), before);
    const racing = new Proxy(store, {get(target, key) {
      if (key === "commitAuthority") return async () => ({status: "conflict", conflict_kind: "provider_revision_mismatch"});
      const value = Reflect.get(target, key, target);
      return typeof value === "function" ? value.bind(target) : value;
    }});
    assert.equal((await update(racing, {...progress, now: new Date("2026-09-13T10:31:00Z")})).status, "conflict");
    assert.deepEqual(await loaded(store), before, "progress and renewal are one CAS");
    const todo = (before.head.todos as JsonObject[])[0]!;
    assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
      operation_id: "exclude", expected_provider_revision: before.provider_revision, projection: before.head,
      mutations: [{kind: "todo_upsert", todo: {...todo, excluded_agents: ["agent-a"]}}]}))).status, "applied");
    const excluded = await loaded(store);
    assert.equal((await update(store, progress)).status, "failed");
    assert.deepEqual(await loaded(store), excluded);
  });
  test(`${provider} managed execution: authorized expiry takeover commits one generation and fences the old writer`, async t => {
    const {store, request, progress} = await setup(t);
    const accepted = await claim(store, request);
    assert.equal(accepted.status, "applied");
    const original = accepted.lease as JsonObject;
    const before = await loaded(store);
    const target = {...request, actor_agent_id: "agent-b", claimed_by: "agent-b", operation_id: "takeover",
      expected_provider_revision: before.provider_revision, now: new Date("2026-09-13T10:45:00Z")};
    assert.equal((await claim(store, {...target,
      now: new Date("2026-09-13T10:44:59Z")})).reason_code, "claim_owner_mismatch");
    assert.equal((await claim(store, {...target, registered_agents: ["agent-a"]})).status, "failed");
    assert.equal((await claim(store, {...target, manage_execution: false})).reason_code, "claim_owner_mismatch");
    assert.deepEqual(await loaded(store), before);
    const recovery = todoUpdateLeaseRecovery(before.head,
      {...progress, claim_operation_id: undefined, actor_agent_id: "agent-b", now: target.now}, "hard_lease");
    assert.equal(recovery.action, "claim_task");
    assert.equal(recovery.execution_authority_granted, false);
    assert.equal(recovery.inspect, undefined);
    assert.equal(recovery.acquire, undefined);
    assert.deepEqual(await loaded(store), before, "recovery advice is read-only");
    const attempts = ["takeover-one", "takeover-two"].map(operation_id => ({...target, operation_id}));
    const results = await Promise.all(attempts.map(input => claim(store, input)));
    const winner = results.findIndex(result => result.status === "applied");
    assert.equal(results.filter(result => result.status === "applied").length, 1, JSON.stringify(results));
    const after = await loaded(store);
    const current = (after.head.leases as JsonObject[])[0]!;
    assert.equal((after.head.todos as JsonObject[])[0]!.claimed_by, "agent-b");
    assert.equal(current.owner, "agent-b");
    assert.equal(current.lease_epoch, Number(original.lease_epoch) + 1);
    assert.equal(current.version, Number(original.version) + 1);
    assert.notEqual(current.idempotency_key, original.idempotency_key);
    assert.deepEqual(current.write_scopes, original.write_scopes);
    assert.equal((await update(store, {...progress, now: target.now})).status, "failed");
    for (const operation of ["renew", "release"] as const) {
      assert.equal((await maintain(store, {operation, goal_id: request.goal_id, todo_id: request.todo_id,
        owner: "agent-a", idempotency_key: String(original.idempotency_key), expected_version: Number(original.version),
        ttl_seconds: null, registered_agents: request.registered_agents, now: target.now})).status, "failed");
    }
    assert.deepEqual(await loaded(store), after, "late old execution cannot write or end its replacement");
    assert.equal((await claim(store, attempts[winner]!)).status, "replayed");
    assert.deepEqual(await loaded(store), after, "lost takeover response does not acquire another generation");
  });
  test(`${provider} managed execution: unavailable receipt and changed policy reject without maintenance`, async t => {
    const {store, request, progress} = await setup(t);
    assert.equal((await claim(store, request)).status, "applied");
    const before = await loaded(store);
    const unavailable = new Proxy(store, {get(target, key) {
      if (key === "readReceipt") return async (id: string) => id === request.operation_id
        ? {status: "unavailable", reason_code: "offline", reason: "synthetic transport failure"}
        : target.readReceipt(id);
      const value = Reflect.get(target, key, target);
      return typeof value === "function" ? value.bind(target) : value;
    }});
    assert.equal((await update(unavailable, progress)).status, "failed");
    assert.deepEqual(await loaded(store), before);
    assert.equal((await update(store, progress, async () => false)).status, "failed");
    assert.deepEqual(await loaded(store), before);
    assert.equal((await update(store, progress)).status, "applied", "normal recovery retains the same original operation");
    const recovered = await loaded(store);
    assert.equal((await store.commitAuthority({operation_id: "policy-change", expected_provider_revision: recovered.provider_revision,
      next_projection: {...recovered.head, handoff_mode: "soft_claim"}, events: [], receipts: []})).status, "applied");
    const changed = await loaded(store);
    assert.equal((await update(store, {...progress, operation_id: "post-policy-change"})).reason_code, "claim_execution_unavailable");
    assert.deepEqual(await loaded(store), changed);
  });
  test(`${provider} managed execution: expiry takeover rejects ineligible or unproven recovery without writes`, async t => {
    for (const scenario of ["released", "invalid-expiry", "unrelated-holder", "excluded", "blocked", "soft-claim"]) {
      const {store, request} = await setup(t);
      assert.equal((await claim(store, request)).status, "applied");
      const head = await loaded(store);
      const todo = {...(head.head.todos as JsonObject[])[0]!};
      const lease = {...(head.head.leases as JsonObject[])[0]!};
      if (scenario === "released") lease.status = "released";
      if (scenario === "invalid-expiry") lease.expires_at = "invalid";
      if (scenario === "unrelated-holder") lease.owner = "agent-b";
      if (scenario === "excluded") todo.excluded_agents = ["agent-b"];
      if (scenario === "blocked") todo.status = "blocked";
      assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
        operation_id: "recovery-boundary", expected_provider_revision: head.provider_revision,
        projection: {...head.head, ...(scenario === "soft-claim" ? {handoff_mode: "soft_claim"} : {})},
        mutations: [{kind: "todo_upsert", todo}, {kind: "lease_upsert", lease}]}))).status, "applied");
      const before = await loaded(store);
      const result = await claim(store, {...request, operation_id: "takeover", actor_agent_id: "agent-b",
        claimed_by: "agent-b", now: new Date("2026-09-13T10:45:00Z")});
      assert.equal(result.status, "failed", `${scenario}: ${JSON.stringify(result)}`);
      assert.deepEqual(await loaded(store), before, scenario);
    }
  });
  test(`${provider} managed execution: pre-upgrade plain claim recovery never changes its intent`, async t => {
    const {store, request} = await setup(t);
    assert.equal((await claim(store, request)).status, "applied");
    const legacy = {...request, operation_id: "pre-upgrade-claim", manage_execution: false};
    assert.equal((await claim(store, legacy)).status, "no_change");
    const before = await loaded(store);
    const recovered = await claim(store, {...legacy, manage_execution: true});
    assert.equal(recovered.status, "replayed", JSON.stringify(recovered));
    assert.equal(recovered.execution_managed, undefined, "old assignment is not upgraded into execution proof");
    assert.deepEqual(await loaded(store), before);
    assert.equal((await claim(store, {...legacy, manage_execution: true, expected_role: null})).reason_code,
      "coordination_operation_identity_mismatch", "other changed intent remains rejected");
    assert.deepEqual(await loaded(store), before);
  });
  test(`${provider} managed execution: completion releases once and the original Turn closes without new execution`, async t => {
    const {store, request} = await setup(t);
    assert.equal((await claim(store, request)).status, "applied");
    const terminal = completion(request);
    const done = await complete(store, terminal);
    assert.equal(done.status, "applied", JSON.stringify(done));
    const completed = await loaded(store);
    assert.equal((completed.head.leases as JsonObject[])[0]!.status, "released");
    const final = await complete(store, {...terminal, requested_no_followup: true});
    assert.equal(final.status, "applied", JSON.stringify(final));
    const closed = await loaded(store);
    assert.equal((closed.head.todos as JsonObject[])[0]!.no_followup, true);
    assert.equal((closed.head.leases as JsonObject[])[0]!.version, 1);
    assert.equal((await complete(store, {...terminal, requested_no_followup: true,
      now: new Date("2030-01-01T00:00:00Z")})).status, "replayed");
    assert.deepEqual(await loaded(store), closed);
    assert.equal((await complete(store, {...terminal,
      operation_identity: {kind: "explicit", operation_id: "unrelated-new-completion"}})).status, "failed");
    assert.deepEqual(await loaded(store), closed, "released proof cannot authorize new work");
  });
  test(`${provider} managed execution: soft claims do not create or maintain leases`, async t => {
    const {store, request, progress} = await setup(t, "soft_claim");
    assert.equal((await claim(store, request)).status, "applied");
    assert.equal((await update(store, progress)).status, "applied");
    assert.deepEqual((await loaded(store)).head.leases, []);
  });
}
