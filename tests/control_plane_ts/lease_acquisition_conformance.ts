/** Independent execution-admission invariants run unchanged on every store. */
import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import type {AuthorityStore} from "../../loopx/control_plane/coordination/authority_store.ts";
import {executeCanonicalTaskLeaseAcquire as acquire, type CanonicalTaskLeaseAcquireInput} from "../../loopx/control_plane/coordination/task_lease_acquire.ts";
import {executeCanonicalTaskLeaseLifecycle as mutate} from "../../loopx/control_plane/coordination/task_lease_lifecycle.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import type {AuthorityStoreConformanceFactory} from "./authority_store_conformance.ts";
import {productionScaleLeaseAcquisitionFixture} from "./production_scale_coordination_fixture.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {evaluateCanonicalTaskLeaseProof} from "../../loopx/control_plane/coordination/task_lease_proof.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {continuationExecutionAuthority} from "../../loopx/control_plane/coordination/continuation_adoption.ts";

async function loaded(store: AuthorityStore) {
  const result = await store.loadAuthority();
  assert.equal(result.status, "loaded");
  if (result.status !== "loaded") throw new Error("missing fixture authority");
  return result;
}

export function registerLeaseAcquisitionConformance(provider: string, factory: AuthorityStoreConformanceFactory) {
  async function setup(t: test.TestContext, schema: "native" | "legacy" = "native",
    change?: (projection: JsonObject, target: string, conflict: string) => void) {
    const {store, contender} = await factory(t);
    const fixture = productionScaleLeaseAcquisitionFixture("goal-a", schema);
    const projection = fixture.projection as JsonObject;
    change?.(projection, fixture.target, fixture.acquisition.conflict_todo_id);
    const seed = authorityProjectionFixture("goal-a", projection.todos as JsonObject[], projection.leases as JsonObject[], schema,
      {handoff_mode: projection.handoff_mode});
    assert.equal((await store.commitAuthority({expected_provider_revision: null, operation_id: "admission-seed",
      next_projection: seed, events: [], receipts: []})).status, "applied");
    const request: CanonicalTaskLeaseAcquireInput = {goal_id: "goal-a", todo_id: fixture.target, owner: "agent-a",
      idempotency_key: fixture.acquisition.execution_key, expected_version: 0,
      ttl_seconds: fixture.acquisition.ttl_seconds, write_scopes: fixture.acquisition.write_scopes,
      registered_agents: fixture.registered_agents, now: new Date(fixture.scenario.now)};
    return {store, contender, request, seed, fixture};
  }

  for (const schema of ["native", "legacy"] as const) {
    test(`${provider} ${schema} completion wait fences acquire and atomic claim, then archived completion releases the wait`, async t => {
      const {store, contender, request, fixture} = await setup(t, schema, (p, target, prerequisite) => {
        (p.todos as JsonObject[]).find(row => row.todo_id === target)!.resume_when = `todo_done:${prerequisite}`;
      });
      const before = await loaded(store);
      assert.equal((await acquire(store, request)).reason_code, "todo_dependency_pending");
      const claim = {goal_id: request.goal_id, todo_id: request.todo_id,
        claimed_by: request.owner, actor_agent_id: request.owner, expected_role: "agent" as const,
        registered_agents: request.registered_agents, operation_id: "waiting-atomic-claim",
        lease_request: {idempotency_key: "waiting-atomic", expected_version: 0, ttl_seconds: 600},
        dry_run: false, now: request.now};
      assert.equal((await executeCoordinationTodoClaim(store, claim)).reason_code, "todo_dependency_pending");
      assert.deepEqual(await loaded(store), before);
      const prerequisite = (before.head.todos as JsonObject[]).find(row => row.todo_id === fixture.acquisition.conflict_todo_id)!;
      const done = {...prerequisite, status: "done", done: true, archive_state: "archive"};
      assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
        expected_provider_revision: before.provider_revision, operation_id: "prerequisite-archived-complete",
        projection: before.head, mutations: [{kind: "todo_upsert", todo: done}]}))).status, "applied");
      assert.equal((await acquire(contender, request)).status, "applied");
    });
    test(`${provider} ${schema} acquire, current-proof replay, handover and new execution`, async t => {
      const {store, contender, request, seed} = await setup(t, schema);
      const first = await acquire(store, request);
      assert.equal(first.status, "applied", JSON.stringify(first));
      assert.deepEqual(first.lease, {schema_version: "task_lease_v0", goal_id: "goal-a", todo_id: request.todo_id,
        owner: "agent-a", idempotency_key: "acquire-a", version: 1, lease_epoch: 1, status: "active",
        write_scopes: ["lease-admission/**"], acquire_ttl_seconds: 600,
        acquired_at: "2026-09-13T10:05:00Z", updated_at: "2026-09-13T10:05:00Z", expires_at: "2026-09-13T10:15:00Z"});
      const after = await loaded(contender);
      const replay = await acquire(contender, request);
      assert.equal(replay.status, "replayed"); assert.deepEqual(replay.lease, first.lease);
      assert.deepEqual(replay.original_receipt, first.original_receipt);
      assert.deepEqual(await loaded(store), after);
      const maintenance = {goal_id: request.goal_id, todo_id: request.todo_id, owner: request.owner,
        idempotency_key: request.idempotency_key, expected_version: 1, ttl_seconds: 600,
        registered_agents: request.registered_agents, now: new Date("2026-09-13T10:06:00Z")};
      const renewed = await mutate(store, {...maintenance, operation: "renew"});
      assert.equal(renewed.status, "applied");
      const current = await acquire(contender, request);
      assert.equal(current.status, "replayed"); assert.deepEqual(current.lease, renewed.lease);
      assert.deepEqual(current.original_receipt, first.original_receipt);
      const transferred = await mutate(store, {...maintenance, operation: "transfer", expected_version: 2,
        new_owner: "agent-b", new_idempotency_key: "receiver-b"});
      assert.equal(transferred.status, "applied");
      const beforeStale = await loaded(store);
      assert.equal((await acquire(contender, request)).reason_code, "idempotency_key_reuse");
      assert.deepEqual(await loaded(store), beforeStale);
      const released = await mutate(store, {...maintenance, operation: "release", owner: "agent-b",
        idempotency_key: "receiver-b", expected_version: 3, ttl_seconds: null});
      assert.equal(released.status, "applied");
      const next = await acquire(contender, {...request, owner: "agent-b", idempotency_key: "acquire-b", expected_version: 3});
      assert.equal(next.status, "applied");
      assert.equal((next.lease as JsonObject).version, 4); assert.equal((next.lease as JsonObject).lease_epoch, 3);
      const final = await loaded(store);
      assert.equal(final.cursor, "6"); assert.deepEqual(final.head.todos, seed.todos);
      assert.deepEqual((final.head.leases as JsonObject[]).filter(l => l.todo_id !== request.todo_id), seed.leases);
    });
  }

  test(`${provider} retained acquisition receipt and renewal lose current authority after a new completion wait`, async t => {
    const {store, contender, request, fixture} = await setup(t);
    const first = await acquire(store, request);
    assert.equal(first.status, "applied");
    const renewal = {...request, operation: "renew" as const, expected_version: 1};
    assert.equal((await mutate(store, renewal)).status, "applied");
    const head = await loaded(store);
    const todo = (head.head.todos as JsonObject[]).find(row => row.todo_id === request.todo_id)!;
    assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
      expected_provider_revision: head.provider_revision, operation_id: "new-completion-wait",
      projection: head.head, mutations: [{kind: "todo_upsert", todo: {...todo,
        resume_when: `todo_done:${fixture.acquisition.conflict_todo_id}`}}]}))).status, "applied");
    const waiting = await loaded(store);
    const replay = await acquire(contender, request);
    assert.equal(replay.reason_code, "todo_dependency_pending");
    assert.deepEqual(replay.original_receipt, first.original_receipt);
    assert.equal((await mutate(store, renewal)).reason_code, "todo_dependency_pending");
    assert.equal((await mutate(store, {...request, operation: "renew", expected_version: 2})).reason_code,
      "todo_dependency_pending");
    assert.equal((await mutate(store, {...request, operation: "transfer", expected_version: 2,
      new_owner: "agent-b", new_idempotency_key: "waiting-transfer"})).reason_code, "todo_dependency_pending");
    assert.deepEqual(await loaded(store), waiting);
    assert.equal((await mutate(store, {...request, operation: "release", expected_version: 2,
      ttl_seconds: null})).status, "applied");
  });

  test(`${provider} continuation proof follows the current completion dependency`, async t => {
    const {store, request, fixture} = await setup(t);
    const currentRequest = {...request, now: new Date()};
    const first = await acquire(store, currentRequest);
    assert.equal(first.status, "applied");
    const execution = (head: JsonObject) => continuationExecutionAuthority(head, {goal_id: request.goal_id,
      todo_id: request.todo_id, agent_id: request.owner, registered_agents: request.registered_agents,
      proof: {idempotency_key: request.idempotency_key, expected_version: 1}});
    const before = await loaded(store);
    assert.equal(execution(before.head).allowed, true);
    const todo = (before.head.todos as JsonObject[]).find(row => row.todo_id === request.todo_id)!;
    assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
      expected_provider_revision: before.provider_revision, operation_id: "continuation-add-wait",
      projection: before.head, mutations: [{kind: "todo_upsert", todo: {...todo,
        resume_when: `todo_done:${fixture.acquisition.conflict_todo_id}`}}]}))).status, "applied");
    const waiting = await loaded(store);
    assert.deepEqual(execution(waiting.head), {allowed: false, reason_code: "todo_dependency_pending"});
  });

  test(`${provider} lost acquire response recovers once and changed intent stays rejected`, async t => {
    const {store, contender, request} = await setup(t);
    let commits = 0;
    const transport: AuthorityStore = {storeIdentity: () => store.storeIdentity(), loadAuthority: () => store.loadAuthority(),
      scanCommitted: (...a) => store.scanCommitted(...a), readReceipt: id => store.readReceipt(id),
      commitAuthority: async commit => {commits++; assert.equal((await store.commitAuthority(commit)).status, "applied"); throw new Error("lost response");}};
    const recovered = await acquire(transport, request);
    assert.equal(recovered.status, "recovered"); assert.equal(commits, 1);
    const after = await loaded(contender);
    for (const changed of [{ttl_seconds: 900}, {write_scopes: ["different/**"]}, {expected_version: 1}]) {
      assert.equal((await acquire(contender, {...request, ...changed})).reason_code, "coordination_operation_identity_mismatch");
      assert.deepEqual(await loaded(store), after);
    }
  });

  test(`${provider} competing acquisitions cannot grant two overlapping executions`, async t => {
    const {store, contender, request} = await setup(t);
    const loser = await acquire(store, request, async () => {
      const winner = await acquire(contender, {...request, owner: "agent-b", idempotency_key: "winner-b"});
      assert.equal(winner.status, "applied");
    });
    assert.equal(loser.status, "conflict");
    const current = await loaded(store);
    assert.equal(current.cursor, "2");
    assert.equal((current.head.leases as JsonObject[]).find(l => l.todo_id === request.todo_id)!.owner, "agent-b");
    assert.equal((await acquire(store, {...request, expected_version: null})).reason_code, "todo_lease_conflict");
    assert.deepEqual(await loaded(store), current);
  });

  for (const dimension of ["live", "archived", "excluded", "claim", "deregistered", "expired"] as const) {
    test(`${provider} complete scope scan adjudicates ${dimension} holder beyond display limits`, async t => {
      const {store, request, fixture} = await setup(t, "native", (p, _target, conflict) => {
        const todo = (p.todos as JsonObject[]).find(r => r.todo_id === conflict)!;
        const lease = (p.leases as JsonObject[]).find(r => r.todo_id === conflict)!;
        lease.write_scopes = ["lease-admission/shared/**"];
        if (dimension === "archived") todo.archive_state = "archive";
        if (dimension === "excluded") todo.excluded_agents = ["agent-b"];
        if (dimension === "claim") todo.claimed_by = "agent-a";
        if (dimension === "deregistered") lease.owner = "unregistered-agent";
        if (dimension === "expired") lease.expires_at = "2026-09-13T10:00:00Z";
      });
      const before = await loaded(store);
      const result = await acquire(store, request);
      if (dimension === "live") {
        assert.equal(result.reason_code, "write_scope_conflict");
        assert.equal((result.conflicts as JsonObject[])[0]!.todo_id, fixture.acquisition.conflict_todo_id);
        assert.deepEqual(await loaded(store), before);
      } else assert.equal(result.status, "applied", JSON.stringify(result));
    });
  }

  for (const [dimension, code] of [["mode", "handoff_mode_forbids_lease"], ["archive", "todo_not_found"],
    ["done", "todo_not_open"], ["claim", "owner_conflicts_with_claim"], ["excluded", "owner_excluded_from_todo"]] as const) {
    test(`${provider} acquisition rejects ${dimension} with no receipt or mutation`, async t => {
      const {store, request} = await setup(t, "native", (p, target) => {
        const todo = (p.todos as JsonObject[]).find(r => r.todo_id === target)!;
        if (dimension === "mode") p.handoff_mode = "soft_claim";
        if (dimension === "archive") todo.archive_state = "archive";
        if (dimension === "done") {todo.status = "done"; todo.done = true;}
        if (dimension === "claim") todo.claimed_by = "agent-b";
        if (dimension === "excluded") todo.excluded_agents = ["agent-a"];
      });
      const before = await loaded(store);
      assert.equal((await acquire(store, request)).reason_code, code);
      assert.deepEqual(await loaded(store), before);
    });
  }

  for (const reason of ["expired", "ineffective"] as const) {
    test(`${provider} ${reason} execution is replaced with a new version and epoch`, async t => {
      const {store, request} = await setup(t);
      assert.equal((await acquire(store, request)).status, "applied");
      const takeover = {...request, owner: "agent-b", idempotency_key: "next-b", expected_version: 1,
        now: new Date(reason === "expired" ? "2026-09-14T10:05:00Z" : "2026-09-13T10:06:00Z"),
        registered_agents: reason === "ineffective" ? ["agent-b"] : request.registered_agents};
      const next = await acquire(store, takeover);
      assert.equal(next.status, "applied");
      assert.equal((next.lease as JsonObject).version, 2); assert.equal((next.lease as JsonObject).lease_epoch, 2);
      assert.equal((await acquire(store, {...request, now: takeover.now})).reason_code, "idempotency_key_reuse");
    });
  }

  test(`${provider} atomic Todo claim uses the same archived-holder conflict rule`, async t => {
    const {store, request} = await setup(t, "native", (p, target, conflict) => {
      const todo = (p.todos as JsonObject[]).find(r => r.todo_id === target)!;
      todo.required_write_scopes = ["lease-admission/**"];
      (p.todos as JsonObject[]).find(r => r.todo_id === conflict)!.archive_state = "archive";
      (p.leases as JsonObject[]).find(r => r.todo_id === conflict)!.write_scopes = ["lease-admission/shared/**"];
    });
    const result = await executeCoordinationTodoClaim(store, {goal_id: request.goal_id, todo_id: request.todo_id,
      claimed_by: request.owner, actor_agent_id: request.owner, expected_role: "agent", registered_agents: request.registered_agents,
      operation_id: "claim-acquire", lease_request: {idempotency_key: request.idempotency_key, expected_version: 0, ttl_seconds: 600},
      dry_run: false, now: request.now});
    assert.equal(result.status, "applied", JSON.stringify(result));
    assert.equal((result.lease as JsonObject).version, 1);
  });

  for (const [kind, repository, frozen, allowed] of [
    ["different", "git:github.com/team/a", "git:github.com/team/b", true],
    ["same", "git:github.com/team/a", "git:github.com/team/a", false],
    ["case-alias", "git:github.com/Team/A", "git:github.com/team/a", false],
    ["legacy-holder", "git:github.com/team/a", null, false],
    ["unknown-requester", null, "git:github.com/team/b", false],
  ] as const) {
    test(`${provider} repository namespace ${kind} retains complete-scan admission`, async t => {
      const {store, request} = await setup(t, "native", (p, target, conflict) => {
        (p.todos as JsonObject[]).find(r => r.todo_id === target)!.task_repository = repository;
        // Even a known current Todo cannot backfill an old unknown grant.
        (p.todos as JsonObject[]).find(r => r.todo_id === conflict)!.task_repository = "git:github.com/team/b";
        const holder = (p.leases as JsonObject[]).find(r => r.todo_id === conflict)!;
        holder.write_scopes = ["lease-admission/shared/**"];
        if (frozen !== null) holder.write_repository = frozen;
      });
      const before = await loaded(store), result = await acquire(store, request);
      if (allowed) {
        assert.equal(result.status, "applied", JSON.stringify(result));
        assert.equal((result.lease as JsonObject).write_repository, repository);
      } else {
        assert.equal(result.reason_code, "write_scope_conflict", JSON.stringify(result));
        assert.deepEqual(await loaded(store), before);
      }
    });
  }

  test(`${provider} independent repositories acquire same paths and preserve frozen lineage`, async t => {
    const {store, contender, request, fixture} = await setup(t, "native", (p, target, conflict) => {
      p.leases = [];
      (p.todos as JsonObject[]).find(r => r.todo_id === target)!.task_repository = "https://github.com/team/a.git";
      const other = (p.todos as JsonObject[]).find(r => r.todo_id === conflict)!;
      other.task_repository = "git:github.com/team/b"; other.claimed_by = null;
      other.required_write_scopes = ["tests/**"];
    });
    const first = await acquire(store, {...request, write_scopes: ["tests/**"]});
    assert.equal(first.status, "applied", JSON.stringify(first));
    assert.equal((first.lease as JsonObject).write_repository, "git:github.com/team/a");
    const second = await executeCoordinationTodoClaim(contender, {goal_id: request.goal_id,
      todo_id: fixture.acquisition.conflict_todo_id, claimed_by: "agent-b", actor_agent_id: "agent-b",
      expected_role: "agent", registered_agents: request.registered_agents, operation_id: "second-repository-claim",
      lease_request: {idempotency_key: "repository-b", expected_version: 0, ttl_seconds: 600}, dry_run: false, now: request.now});
    assert.equal(second.status, "applied", JSON.stringify(second));
    assert.equal((second.lease as JsonObject).write_repository, "git:github.com/team/b");
    assert.deepEqual((second.lease as JsonObject).write_scopes, ["tests/**"]);
    const maintenance = {...request, operation: "renew" as const, expected_version: 1};
    const renewed = await mutate(store, maintenance);
    assert.equal(renewed.status, "applied");
    const replay = await acquire(contender, {...request, write_scopes: ["tests/**"]});
    assert.equal(replay.status, "replayed"); assert.deepEqual(replay.original_receipt, first.original_receipt);
    assert.deepEqual(replay.lease, renewed.lease);
    const transfer = await mutate(store, {...maintenance, operation: "transfer", expected_version: 2,
      new_owner: "agent-b", new_idempotency_key: "transferred-a"});
    assert.equal(transfer.status, "applied");
    const release = await mutate(store, {...maintenance, operation: "release", owner: "agent-b",
      idempotency_key: "transferred-a", expected_version: 3, ttl_seconds: null});
    assert.equal(release.status, "applied");
    for (const result of [renewed, transfer, release]) assert.equal((result.lease as JsonObject).write_repository, "git:github.com/team/a");
    const head = await loaded(store);
    assert.equal((head.head.leases as JsonObject[]).filter(r => r.status === "active").length, 1);
  });

  test(`${provider} repository drift rejects current proof and renewal without rewriting receipt`, async t => {
    const {store, contender, request} = await setup(t, "native", (p, target) => {
      (p.todos as JsonObject[]).find(r => r.todo_id === target)!.task_repository = "git:github.com/team/a";
    });
    const first = await acquire(store, request);
    assert.equal(first.status, "applied");
    const head = await loaded(store), next = structuredClone(head.head);
    const todo = (next.todos as JsonObject[]).find(r => r.todo_id === request.todo_id)!;
    todo.task_repository = "git:github.com/team/b";
    assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: request.goal_id,
      expected_provider_revision: head.provider_revision, operation_id: "synthetic-repository-drift",
      projection: head.head, mutations: [{kind: "todo_upsert", todo}]}))).status, "applied");
    const before = await loaded(store);
    const replay = await acquire(contender, request);
    assert.equal(replay.reason_code, "lease_repository_divergence", JSON.stringify(replay));
    assert.deepEqual(replay.original_receipt, first.original_receipt);
    assert.equal(evaluateCanonicalTaskLeaseProof({todo, lease: first.lease as JsonObject,
      handoff_mode: "hard_lease", actor_agent_id: request.owner, registered_agents: request.registered_agents,
      lease_idempotency_key: request.idempotency_key, lease_expected_version: 1, now: request.now}).code, "lease_repository_divergence");
    assert.equal((await mutate(store, {...request, operation: "renew", expected_version: 1})).reason_code, "lease_repository_divergence");
    assert.deepEqual(await loaded(store), before);
    // Cleanup still uses the exact holder/key/version, not the current repository.
    const released = await mutate(store, {...request, operation: "release", expected_version: 1, ttl_seconds: null});
    assert.equal(released.status, "applied");
    assert.equal((released.lease as JsonObject).version, 1);
    const recovered = await acquire(contender, {...request, idempotency_key: "repository-recovered", expected_version: 1});
    assert.equal(recovered.status, "applied", JSON.stringify(recovered));
    assert.equal((recovered.lease as JsonObject).write_repository, "git:github.com/team/b");
    assert.equal(evaluateCanonicalTaskLeaseProof({todo, lease: recovered.lease as JsonObject,
      handoff_mode: "hard_lease", actor_agent_id: request.owner, registered_agents: request.registered_agents,
      lease_idempotency_key: "repository-recovered", lease_expected_version: 2, now: request.now}).code, "terminal_fence_verified");
    assert.equal((await acquire(store, request)).reason_code, "idempotency_key_reuse");
    assert.deepEqual((await acquire(store, request)).original_receipt, first.original_receipt);
  });

  test(`${provider} malformed frozen repository fails closed with no mutation`, async t => {
    const {store, request} = await setup(t, "native", (p, _target, conflict) => {
      (p.leases as JsonObject[]).find(r => r.todo_id === conflict)!.write_repository = "https://github.com/team/b.git";
    });
    const before = await loaded(store);
    assert.equal((await acquire(store, request)).reason_code, "invalid_canonical_acquire_state");
    assert.deepEqual(await loaded(store), before);
  });
}
