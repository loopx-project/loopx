import test from "node:test";
import assert from "node:assert/strict";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {canonicalTaskValidationPlan} from "../../loopx/control_plane/goals/task_validation_plan.ts";
import {delegationValidationPlan} from "../../loopx/control_plane/collaboration/delegation.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";

const declaration = {validation_command: null, validation_command_argv: ["node", "check.mjs"],
  validation_label: "Check", validation_timeout_seconds: 5};
// Independent canonical-JSON vector: sorted keys, compact UTF-8, bare SHA-256.
const declarationSha256 = "c7fe398d608e3fa26ac76304e63f2ae0cc29ac5f192d03fd77ab1aa1ec563a22";
const todo = {todo_id: "todo_check", text: "Deliver checked output", status: "open", done: false,
  claimed_by: "agent", role: "agent", archive_state: "active", completion_validation_required: true,
  completion_validation_sha256: declarationSha256, task_repository: "git:example.invalid/project"};
const criteria = [{id: "result", description: "Result matches its contract", validation_argv: ["node", "owner.mjs"],
  validation_timeout_seconds: 5, validation_files: [{path: "owner.mjs", sha256: "a".repeat(64)}]}];
const requirements = {todo_id: todo.todo_id, contract_revision: 3, contract_digest: "b".repeat(64),
  todo_semantic_digest: "c".repeat(64), criterion_ids: ["result"], criteria};
const basis = {status: "loaded", provider_revision: "sqlite:1", todo, completion_requirements: requirements};
const input = {todo_id: todo.todo_id, basis, declaration};
const observation = (plan: JsonObject) => plan.observation as JsonObject;

test("canonical Goal and required Todo checks are cumulative without any delegation binding", () => {
  const before = structuredClone(input);
  const combined = canonicalTaskValidationPlan(input);
  const goalOnly = canonicalTaskValidationPlan({...input, declaration: null,
    basis: {...basis, todo: {todo_id: todo.todo_id, status: "open", done: false}}});
  const todoOnly = canonicalTaskValidationPlan({...input, basis: {...basis, completion_requirements: null}});
  assert.deepEqual((combined.effects as JsonObject[]).map(effect => effect.validation_argv),
    [["node", "owner.mjs"], ["node", "check.mjs"]]);
  assert.deepEqual((goalOnly.effects as JsonObject[]).map(effect => effect.validation_argv), [["node", "owner.mjs"]]);
  assert.deepEqual((todoOnly.effects as JsonObject[]).map(effect => effect.validation_argv), [["node", "check.mjs"]]);
  assert.equal(combined.source, "goal_acceptance");
  assert.equal(goalOnly.source, "goal_acceptance");
  assert.equal(todoOnly.source, "todo_validation");
  assert.equal(observation(combined).check_count, 2);
  assert.equal(observation(combined).pinned_file_count, 1);
  assert.equal(combined.canonical_done, false);
  assert.deepEqual(input, before, "planning cannot rewrite canonical input");
  assert.equal(Object.hasOwn(combined, "binding"), false);
  assert.equal(Object.hasOwn(combined, "execution_allowed"), false);
});

test("full definition and work/claim/lifecycle hashes match independent canonical vectors", () => {
  const plan = canonicalTaskValidationPlan(input);
  assert.equal(observation(plan).basis_sha256, "1c562801d51c28829aa8e8cf5fad728a7841406a70aae47a896af86f3e0b5480");
  assert.equal(plan.task_basis_sha256, "fac35dba171637f39235e8f441da527336f9887923d1835371fbb78d553f7676");
  for (const delta of [{contract_revision: 4}, {contract_digest: "d".repeat(64)},
    {todo_semantic_digest: "e".repeat(64)}, {owner_requirement_extension: "New definition"},
    {criteria: [{...criteria[0], description: "Another result boundary"}]},
    {criteria: [{...criteria[0], validation_files: [{path: "owner.mjs", sha256: "f".repeat(64)}]}]}]) {
    assert.notEqual(observation(canonicalTaskValidationPlan({...input,
      basis: {...basis, completion_requirements: {...requirements, ...delta}}})).basis_sha256,
    observation(plan).basis_sha256, "complete requirements, including unknown definition fields, stay hashed");
  }
  for (const delta of [{note: "Observed progress"}, {priority: "P1"}, {evidence: "Checked"},
    {updated_at: "2026-01-01T00:00:00Z"}]) {
    assert.deepEqual(canonicalTaskValidationPlan({...input,
      basis: {...basis, provider_revision: "sqlite:99", todo: {...todo, ...delta}}}), plan);
  }
  for (const delta of [{future_work_field: "New obligation"}, {text: "Other work"},
    {claimed_by: "another-agent"}, {role: "user"}, {archive_state: "archived"},
    {status: "blocked"}, {done: true}, {task_repository: "git:example.invalid/other-project"}]) {
    assert.notEqual(canonicalTaskValidationPlan({...input, basis: {...basis, todo: {...todo, ...delta}}}).task_basis_sha256,
      plan.task_basis_sha256, "work and current claim/lifecycle fence the attempt");
  }
});

test("required private Todo declarations cannot be omitted or repaired by Goal checks", () => {
  for (const completion_requirements of [requirements, null]) {
    const current = {...input, basis: {...basis, completion_requirements}};
    assert.deepEqual(canonicalTaskValidationPlan({...current, declaration: null}), {
      todo_id: todo.todo_id, state: "unbound", source: null,
      reason: "completion_validation_declaration_unavailable", effects: [], canonical_done: false});
    for (const corrupt of [{}, {...declaration, extra_authority: true},
      {...declaration, validation_command_argv: ["node", "other.mjs"]},
      {...declaration, validation_timeout_seconds: "5"}, {...declaration, validation_command_argv: []}]) {
      const rejected = canonicalTaskValidationPlan({...current, declaration: corrupt});
      assert.equal(rejected.reason, "completion_validation_declaration_mismatch");
      assert.deepEqual(rejected.effects, []);
    }
    for (const declaration of [undefined, []])
      assert.throws(() => canonicalTaskValidationPlan({...current, declaration}), /private validation declaration/);
  }
});

test("missing, unavailable, foreign or applicable unbound Goal basis cannot fall back", () => {
  const {completion_requirements: _scope, ...missingScope} = basis;
  for (const corruptBasis of [missingScope, {...basis, completion_requirements: undefined},
    {...basis, completion_requirements: {}}, {...basis, completion_requirements: []},
    {...basis, completion_requirements: {...requirements, todo_id: "other"}},
    {...basis, completion_requirements: {...requirements, criteria: []}},
    {...basis, completion_requirements: {...requirements, criteria: [null]}},
    {...basis, status: "unavailable"}, {...basis, provider_revision: null},
    {...basis, todo: {...todo, todo_id: "other"}}, {...basis, todo: null}]) {
    assert.throws(() => canonicalTaskValidationPlan({...input, basis: corruptBasis}));
  }
  for (const todo_id of [undefined, null, "", "other"])
    assert.throws(() => canonicalTaskValidationPlan({...input, todo_id}), /matching canonical Todo/);
  assert.throws(() => canonicalTaskValidationPlan({basis: {...basis, todo: {}, completion_requirements: null}, declaration: null}));
});

test("only explicit null can resolve absent checks; shared no_declaration retains delegation parity", () => {
  const noChecks = {...input, declaration: null,
    basis: {...basis, completion_requirements: null, todo: {todo_id: todo.todo_id, status: "done", done: true}}};
  assert.deepEqual(canonicalTaskValidationPlan(noChecks), {todo_id: todo.todo_id, state: "unbound",
    source: null, reason: "no_declaration", effects: [], canonical_done: false});
  assert.deepEqual(delegationValidationPlan({...noChecks, binding: {todo_id: todo.todo_id}}),
    {...canonicalTaskValidationPlan(noChecks), reason: "independent_delegation_validation_required"});
  for (const declaration of [undefined, {}, input.declaration])
    assert.throws(() => canonicalTaskValidationPlan({...noChecks, declaration}), /without canonical validation authority/);
  assert.throws(() => canonicalTaskValidationPlan({...noChecks,
    basis: {...noChecks.basis, todo: {...noChecks.basis.todo, completion_validation_sha256: declarationSha256}}}));
});

test("shared observation stays definition-only while delegation retains current oracle provenance", () => {
  const evidence = {check_id: "current", output_versions: [{ref: "output.json", sha256: "d".repeat(64)}]};
  const current = {...input, basis: {...basis, goal_acceptance_contract: {
    criteria: [{id: "result", oracle: {state: "current"}}],
    verification: {results: [{criterion_id: "result", oracle_evidence: evidence}]}}}};
  const shared = canonicalTaskValidationPlan(current);
  assert.deepEqual(Object.keys(observation(shared)).sort(), ["basis_sha256", "check_count", "pinned_file_count", "source"]);
  assert.deepEqual(delegationValidationPlan({...current, binding: {todo_id: todo.todo_id}}), {
    ...shared, observation: {...observation(shared), oracle_checks: [{criterion_id: "result", ...evidence}]}});
  assert.equal(canonicalTaskValidationPlan({...input, basis: {...basis, todo: {...todo, done: true}}}).canonical_done, false);
  assert.equal(canonicalTaskValidationPlan({...input, basis: {...basis, todo: {...todo, status: "done", done: true}}}).canonical_done, true);
});

test("internal runtime dispatch serves the shared plan and rejects corrupt basis without IO", async () => {
  let shutdown = false;
  const handlers = createEffectRuntimeHandlers({fingerprint: "canonical-task-test", requestShutdown() { shutdown = true; }});
  assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "goal.task.validation_plan", input), canonicalTaskValidationPlan(input));
  await assert.rejects(() => dispatchEffectRuntimeMethod(handlers, "goal.task.validation_plan", {
    ...input, basis: {...basis, completion_requirements: undefined}}), /canonical acceptance requirements/);
  assert.equal(shutdown, false);
});
