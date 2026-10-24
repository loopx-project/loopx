import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {updateGoalObjective, inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {goalObjectiveWorkGuard, goalObjectiveWorkDigest, readGoalObjective, reviseGoalObjective, projectGoalObjective, adoptGoalObjective} from "../../loopx/control_plane/goals/goal_objective.ts";
import {projectCoreGoal} from "../../loopx/control_plane/work_items/replan_context.ts";
import {projectGoalAcceptance, goalAcceptanceWorkDigest} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const goal = "objective-goal";
const fixture = () => authorityProjectionFixture(goal, [{todo_id: "todo_a", role: "agent", status: "open", done: false,
  archive_state: "active", text: "Deliver the current result", task_class: "advancement_task", claimed_by: "agent-a"}], [], "native");
const document = {objective: "Deliver a reliable maintained service", reason: "The bootstrap milestone is complete", source_ref: "message:owner-direction"};
const request = {goal_id: goal, actor_agent_id: null, operation: "revise-objective", operation_id: "revision-1", document,
  source_objective: {registry_objective: "Bootstrap the project"}, registered_agents: ["agent-a", "agent-b"]};

test("current membership is read-only evidence and preserves historical peers and adoption", () => {
  const head = fixture();
  head.goal_objective = reviseGoalObjective(head, null, "Bootstrap the project", request.registered_agents, document);
  const revision = readGoalObjective(head)!.revisions.at(-1)!;
  head.goal_objective = adoptGoalObjective(head, "agent-a", {revision: revision.revision, digest: revision.digest,
    work_digest: goalObjectiveWorkDigest(head, "agent-a"), review_summary: "Reviewed the existing work."});
  const before = structuredClone(head), retained = projectGoalObjective(head);
  const current = projectGoalObjective(head, ["later-peer"])!;
  assert.deepEqual(current.peer_adoption, [
    {agent_id: "agent-a", revision: 1, current: true},
    {agent_id: "agent-b", revision: null, current: false},
    {agent_id: "later-peer", revision: null, current: false},
  ]);
  assert.deepEqual(projectGoalObjective(head, []), retained);
  assert.deepEqual(projectGoalObjective(head), retained, "callers without live membership keep the retained projection");
  assert.equal(goalObjectiveWorkGuard(head, "later-peer")?.allowed, false);
  const roster = Array.from({length: 130}, (_, index) => `peer-${index}`);
  assert.equal((projectGoalObjective(head, roster)!.peer_adoption as unknown[]).length, 132);
  assert.deepEqual(head, before, "reads cannot create revisions, membership or adoption");
});

test("current registration does not normalize or erase historical objective IDs", () => {
  const head = fixture();
  head.goal_objective = reviseGoalObjective(head, null, "Bootstrap the project", ["1Legacy", "Legacy.Owner"], document);
  const projected = projectGoalObjective(head, ["agent-a"])!;
  assert.deepEqual((projected.peer_adoption as JsonObject[]).map(peer => peer.agent_id),
    ["1Legacy", "Legacy.Owner", "agent-a"]);
  assert.deepEqual(readGoalObjective(head)!.registered_agents, ["1Legacy", "Legacy.Owner"]);
});

for (const roster of [null, "agent-a", [1], ["invalid/peer"], ["agent-a", "agent-a"]]) {
  test(`objective membership rejects malformed source facts: ${JSON.stringify(roster)}`, () => {
    const head = fixture();
    head.goal_objective = reviseGoalObjective(head, null, "Bootstrap the project", request.registered_agents, document);
    assert.throws(() => projectGoalObjective(head, roster), /registered_agents|public-safe agent/);
  });
}

for (const provider of ["file", "sqlite"] as const) test(`${provider}: shared revisions, competing writers, peer adoption and restart`, async t => {
  const root = await mkdtemp(join(tmpdir(), "goal-objective-")); t.after(() => rm(root, {recursive: true, force: true}));
  const open = () => provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const store = open();
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const base = await store.loadAuthority(); assert.equal(base.status, "loaded"); if (base.status !== "loaded") return;
  const revision = {...request, expected_provider_revision: base.provider_revision};
  const preview = await updateGoalObjective(store, {...revision, dry_run: true});
  assert.equal(preview.status, "planned");
  assert.deepEqual(await store.loadAuthority(), base);
  await assert.rejects(updateGoalObjective(store, {...revision, actor_agent_id: "agent-a"}), /trusted owner/);
  const results = await Promise.all([updateGoalObjective(store, revision), updateGoalObjective(open(), {...revision, operation_id: "competing", document: {...document, objective: "A competing direction"}})]);
  assert.equal(results.filter(r => r.status === "applied").length, 1);
  const winner = results[0].status === "applied" ? revision : {...revision, operation_id: "competing", document: {...document, objective: "A competing direction"}};
  assert.equal((await updateGoalObjective(open(), winner)).status, "replayed");
  const after = await store.loadAuthority(); assert.equal(after.status, "loaded"); if (after.status !== "loaded") return;
  assert.notEqual(goalAcceptanceWorkDigest(after.head, goal), goalAcceptanceWorkDigest(base.head, goal), "old verification cannot certify the new objective");
  assert.equal(goalObjectiveWorkGuard(after.head, "agent-a")?.allowed, false);
  assert.equal(goalObjectiveWorkGuard(after.head, "agent-b")?.allowed, false);
  assert.equal(goalObjectiveWorkGuard(after.head, "new-peer")?.allowed, false, "later peers do not inherit somebody else's adoption");
  const state = readGoalObjective(after.head)!;
  const current = state.revisions.at(-1)!;
  const adoption = {...request, operation: "adopt-objective", actor_agent_id: "agent-a", operation_id: "adopt-a",
    expected_provider_revision: after.provider_revision, document: {revision: current.revision, digest: current.digest,
      work_digest: goalObjectiveWorkDigest(after.head, "agent-a"), review_summary: "The existing delivery Todo remains necessary; its route is retained."}};
  await assert.rejects(updateGoalObjective(store, {...adoption, document: {...adoption.document, work_digest: "0".repeat(64)}}), /own work changed/);
  assert.equal((await updateGoalObjective(store, adoption)).status, "applied");
  let adopted = await store.loadAuthority(); if (adopted.status !== "loaded") return;
  const once = adopted;
  const repeated = await updateGoalObjective(store, {...adoption, operation_id: "redundant-adoption", expected_provider_revision: adopted.provider_revision});
  assert.equal(repeated.status, "no_change");
  assert.deepEqual(await store.loadAuthority(), once, "repeated adoption has no provider write");
  const ordinary = structuredClone(adopted.head);
  const todos = ordinary.todos as JsonObject[];
  todos[0].text = "Continue routine service work";
  await store.commitAuthority({operation_id: "ordinary-progress", expected_provider_revision: adopted.provider_revision, next_projection: {...ordinary, ...authorityProjectionFixture(goal, todos, [], "native")}, events: [], receipts: []});
  adopted = await store.loadAuthority(); if (adopted.status !== "loaded") return;
  assert.equal(readGoalObjective(adopted.head)!.revisions.length, 1, "work changes never revise the objective");
  assert.equal(goalObjectiveWorkGuard(adopted.head, "agent-a"), null);
  assert.equal(goalObjectiveWorkGuard(adopted.head, "agent-b")?.allowed, false, "one peer cannot acknowledge for another");
  assert.equal((await updateGoalObjective(open(), {...request, operation_id: "revision-2", expected_provider_revision: adopted.provider_revision,
    document: {...document, objective: "Deliver the service and its recovery path"}})).status, "applied");
  assert.equal((await updateGoalObjective(open(), adoption)).status, "replayed", "historical response recovery is still available");
  const latest = await store.loadAuthority(); if (latest.status !== "loaded") return;
  assert.equal(goalObjectiveWorkGuard(latest.head, "agent-a")?.allowed, false, "historical adoption replay never adopts the next revision");
  assert.deepEqual(latest.head.todos, ordinary.todos, "revisions do not rewrite work or leases");
  const inspection = await inspectGoalAcceptance(open(), goal, undefined, undefined, true, "agent-a");
  assert.equal(inspection.objective_work_digest, goalObjectiveWorkDigest(latest.head, "agent-a"));
  const core = projectCoreGoal({active_state_objective: "Outdated active file", registry_objective: "Old registry",
    acceptance_contract: projectGoalAcceptance(latest.head, goal)});
  assert.equal(core.objective, "Deliver the service and its recovery path");
  assert.equal(core.objective_source, "canonical_revision");
});

test("reviewed objective ignores unrelated writes but rejects changed source or revision", async t => {
  const root = await mkdtemp(join(tmpdir(), "objective-review-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = new FileAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const base = await store.loadAuthority(); if (base.status !== "loaded") return;
  const req = {...request, expected_provider_revision: base.provider_revision};
  const preview = await updateGoalObjective(store, {...req, dry_run: true});
  const digest = ((preview.goal_acceptance_contract as JsonObject).goal_objective as JsonObject).digest;
  const reviewed = {...req, review_objective_digest: digest};
  const changedSource = await updateGoalObjective(store, {...reviewed, source_objective: {registry_objective: "Another root commitment"}});
  assert.equal(changedSource.reason_code, "goal_acceptance_contract_stale");
  assert.deepEqual(await store.loadAuthority(), base);
  const other = {...base.head, observation_note: "unrelated work settled"};
  await store.commitAuthority({operation_id: "progress", expected_provider_revision: base.provider_revision, next_projection: other, events: [], receipts: []});
  assert.equal((await updateGoalObjective(store, reviewed)).status, "applied", "unrelated work does not demand another owner review");
  const stale = await updateGoalObjective(store, {...reviewed, operation_id: "stale-review", document: {...document, objective: "A different proposal"}});
  assert.equal(stale.reason_code, "goal_acceptance_contract_stale");
});

test("objective schema rejects hidden acceptance changes, corrupt history and retired inheritance", () => {
  const head = fixture();
  assert.deepEqual(projectGoalAcceptance(head, goal), {});
  assert.throws(() => reviseGoalObjective(head, null, "Original", ["agent-a"], {...document, acceptance: []}), /unsupported/);
  const state = reviseGoalObjective(head, null, "Original", ["agent-a"], document);
  assert.throws(() => reviseGoalObjective({...head, goal_objective: state}, null, "Original", ["agent-a"], {...document, objective: "Deliver a reliable  maintained service"}), /unchanged/);
  const revised: JsonObject = {...head, goal_objective: state};
  const corrupted = structuredClone(revised);
  (corrupted.goal_objective as typeof state).revisions[0].objective = "Tampered";
  assert.throws(() => readGoalObjective(corrupted), /digest mismatch/);
  revised.goal_acceptance_lifecycle = {schema_version: "loopx_goal_acceptance_lifecycle_v0", state: "active",
    goal_ref: {goal_id: goal, goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}};
  assert.equal(projectGoalAcceptance(revised, goal).goal_objective, undefined, "a successor lifetime cannot inherit legacy intent");
});
