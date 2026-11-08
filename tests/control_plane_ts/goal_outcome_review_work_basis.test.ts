import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {refineGoalOutcome, projectGoalOutcomes} from "../../loopx/control_plane/goals/outcome_requirements.ts";
import {refineGoalAcceptanceOutcome, inspectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {appendTodoExploreReferences} from "../../loopx/control_plane/todos/work_requirements.ts";
import {planTodoFieldUpdate, TODO_FIELD_UPDATE_REQUEST_SCHEMA} from "../../loopx/control_plane/todos/field_update.ts";

const goal = "review-goal";
const todoId = "todo_export";
const node = "question_result";
const update = {todo_id: todoId, append_explore_result_node_refs: [node]};
const declaration = {outcome_id: "export", owner_agent_id: "author", objective: "Deliver a recoverable export",
  requirements: [{id: "content", description: "Preserve the requested content"}], todo_ids: [todoId, "todo_recovery"],
  source: {ref: "message:export", summary: "The owner requested a recoverable export"}};
const review = {outcome_id: "export", review: [{id: "content", conclusion: "met", evidence_refs: ["artifact:export"],
  note: "Read the content within its tested scope"}]};

function fixture(): JsonObject {
  const head = authorityProjectionFixture(goal, [todoId, "todo_recovery", "todo_unrelated"].map(todo_id => ({todo_id,
    role: "agent", status: "open", done: false, archive_state: "active", claimed_by: "author",
    task_class: "advancement_task", text: "Validate the declared export"})));
  head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", declaration);
  return head;
}
function withTodos(head: JsonObject, change: (row: JsonObject) => JsonObject): JsonObject {
  return authorityProjectionFixture(goal, (head.todos as JsonObject[]).map(change), [], "native", head);
}
function current(head: JsonObject): boolean {
  const outcome = (projectGoalOutcomes(head, goal, {})!.outcomes as JsonObject[])[0];
  const requirement = (outcome.requirements as JsonObject[])[0];
  assert.equal((requirement.agent_review as JsonObject).independently_verified, false);
  assert.equal(requirement.status, "unverified");
  return (requirement.agent_review as JsonObject).basis_current === true;
}
function linked(head: JsonObject): JsonObject {
  return withTodos(head, row => row.todo_id === todoId ? {...row, ...appendTodoExploreReferences(row, [node])} : row);
}

test("shared append preserves normalization, refusal details and validation precedence", () => {
  const plan = (refs: unknown, additions: unknown) => planTodoFieldUpdate({schema_version: TODO_FIELD_UPDATE_REQUEST_SCHEMA,
    todo: {todo_id: todoId, status: "open", explore_result_node_refs: refs},
    intent: {append_explore_result_node_refs: additions}, updated_at: "2026-01-01T00:00:00Z"});
  assert.deepEqual((plan(["prior"], [" next ", "next"]).metadata_updates as JsonObject).explore_result_node_refs, ["prior", "next"]);
  assert.deepEqual((plan(["prior"], []).metadata_updates as JsonObject).explore_result_node_refs, ["prior"]);
  for (const refs of [[], ["/invalid"]]) assert.throws(() => plan(refs, 5), {
    message: "explore_result_node_refs must be an array of strings"});
  assert.throws(() => plan(["/invalid"], ["valid"]), {message: "explore_result_node_refs requires valid Explore node ids"});
});

test("prospective review covers only the exact additive effect and never mutates the source", () => {
  const original = fixture(), retained = structuredClone(original);
  const reviewed = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", review, update)};
  assert.deepEqual(original, retained);
  assert.equal(current(reviewed), false, "a planned link is not a committed effect");
  assert.equal(current(linked(reviewed)), true);
  for (const [target, fields] of [
    [todoId, {explore_result_node_refs: [node, "question_unreviewed"]}],
    [todoId, {text: "Validate a different export"}],
    [todoId, {required_capabilities: ["shell"]}],
    ["todo_recovery", {text: "Changed linked recovery work"}],
  ] as [string, JsonObject][]) {
    const changed = withTodos(linked(reviewed), row => row.todo_id === target ? {...row, ...fields} : row);
    assert.equal(current(changed), false, "a concurrent semantic change cannot enter the reviewed basis");
  }
  assert.equal(current(withTodos(linked(reviewed), row => row.todo_id === "todo_unrelated"
    ? {...row, text: "Unrelated work can continue"} : row)), true);
  const ordinary = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", review)};
  assert.equal(current(ordinary), true);
  assert.equal(current(linked(ordinary)), false, "separately reviewed state must not adopt a later link");
});

test("reviewed updates cannot rebind old reviews, replace work or act on unlinked/foreign work", () => {
  const head = fixture();
  for (const invalid of [
    {...update, status: "done"}, {...update, todo_id: "todo_unrelated"},
    {...update, todo_id: "todo_missing"}, {...update, append_explore_result_node_refs: []},
    {...update, append_explore_result_node_refs: [node, "question_other"]},
    {...update, append_explore_result_node_refs: ["/local/path"]},
  ]) assert.throws(() => refineGoalOutcome(head, goal, null, "author", review, invalid));
  assert.throws(() => refineGoalOutcome(head, goal, null, null, review, update));
  assert.throws(() => refineGoalOutcome(head, goal, null, "other", review, update));
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "export"}, update));
  const foreign = withTodos(head, row => row.todo_id === todoId ? {...row, claimed_by: "other"} : row);
  assert.throws(() => refineGoalOutcome(foreign, goal, null, "author", review, update));
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: exact CAS and receipts retain anticipated basis across restart and lost replies`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-reviewed-work-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const open = () => provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const store = open();
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const before = await store.loadAuthority(); assert.equal(before.status, "loaded"); if (before.status !== "loaded") return;
  const request = {goal_id: goal, actor_agent_id: "author", operation_id: "review", expected_provider_revision: before.provider_revision,
    refinement: review, reviewed_work_update: update};
  assert.equal((await refineGoalAcceptanceOutcome(store, {...request, dry_run: true})).status, "planned");
  assert.deepEqual(await store.loadAuthority(), before);
  const commit = store.commitAuthority.bind(store);
  store.commitAuthority = async value => {await commit(value); throw new Error("reply lost after commit");};
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "recovered");
  const saved = await open().loadAuthority(); assert.equal(saved.status, "loaded"); if (saved.status !== "loaded") return;
  assert.equal(current(saved.head), false);
  assert.equal((await refineGoalAcceptanceOutcome(open(), request)).status, "replayed");
  assert.deepEqual(await open().loadAuthority(), saved);
  const changed = await refineGoalAcceptanceOutcome(open(), {...request,
    reviewed_work_update: {...update, append_explore_result_node_refs: ["question_different"]}});
  assert.equal(changed.reason_code, "coordination_operation_identity_mismatch");
  assert.deepEqual(await open().loadAuthority(), saved);
  // An unrelated authority commit never authorizes silently rebasing a fresh
  // operation. The original CAS is preserved even when its prospective link fits.
  const refused = await refineGoalAcceptanceOutcome(open(), {...request, operation_id: "stale-review"});
  assert.equal(refused.status, "conflict");
  assert.deepEqual(await open().loadAuthority(), saved);
  await open().commitAuthority({operation_id: "todo-link", expected_provider_revision: saved.provider_revision,
    next_projection: linked(saved.head), events: [], receipts: []});
  const final = await open().loadAuthority(); assert.equal(final.status, "loaded"); if (final.status !== "loaded") return;
  assert.equal(current(final.head), true);
  assert.equal((await refineGoalAcceptanceOutcome(open(), request)).status, "replayed");
  assert.deepEqual(await open().loadAuthority(), final);
  const contract = (await inspectGoalAcceptance(open(), goal)).goal_acceptance_contract as JsonObject;
  assert.equal((contract.outcome_requirements as JsonObject).revision, 2);
});
