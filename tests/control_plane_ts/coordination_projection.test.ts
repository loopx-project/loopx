import assert from "node:assert/strict";
import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { canonicalAuthoritySha256 } from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import type { JsonObject } from "../../loopx/control_plane/effect_program.ts";
import {
  indexCoordinationProjection,
  indexCoordinationProjectionTodos,
  coordinationTodoReadModel,
  validateCoordinationTodoReadModel,
  prepareCoordinationProjectionCommit,
  reduceCoordinationProjection,
  TODO_CANONICAL_READ_RECORD_FIELDS,
  TODO_CANONICAL_READ_RECORD_SCHEMA,
} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import { FileAuthorityStore } from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA} from "../../loopx/control_plane/coordination/local_authority_runtime.ts";
import {listLocalCoordinationTodos} from "../../loopx/control_plane/coordination/local_authority_read.ts";
import {
  TODO_DOMAIN_ITEM_SCHEMA,
  TODO_DOMAIN_READ_RECORD_SCHEMA,
  TODO_DOMAIN_RECORD_CONTRACT,
} from "../../loopx/control_plane/coordination/coordination_state_contract.ts";

test("native provider Todo creation and archival need no Markdown address", async () => {
  const root = await mkdtemp(join(tmpdir(), "loopx-domain-todo-"));
  const store = new FileAuthorityStore(root, "goal-a");
  const initial = await store.commitAuthority({
    expected_provider_revision: null,
    operation_id: "bootstrap:domain",
    events: [{ schema_version: "bootstrap_v0" }],
    next_projection: {
      goal_id: "goal-a", todos: [], leases: [],
      todo_read_model: {
        schema_version: TODO_DOMAIN_READ_RECORD_SCHEMA,
        todo_count: 0,
        records_sha256: canonicalAuthoritySha256([]),
        contract_fields: [...TODO_DOMAIN_RECORD_CONTRACT.fields],
      },
    },
    receipts: [],
  });
  assert.equal(initial.status, "applied");
  if (initial.status !== "applied") return;
  const todo = {
    schema_version: TODO_DOMAIN_ITEM_SCHEMA,
    todo_id: "todo_native", role: "agent", status: "done", done: true,
    text: "Retain the archival decision independently of its rendered section",
    archive_state: "active", no_followup: true,
  };
  const input = {
    goal_id: "goal-a", operation_id: "create:domain",
    expected_provider_revision: initial.provider_revision,
    mutations: [{ kind: "todo_upsert" as const, todo }],
  };
  const seeded = await store.loadAuthority();
  assert.equal(seeded.status, "loaded");
  if (seeded.status !== "loaded") return;
  const create = prepareCoordinationProjectionCommit({
    ...input,
    projection: seeded.head,
  });
  assert.equal((await store.commitAuthority(create)).status, "applied");
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status !== "loaded") return;
  assert.deepEqual(head.head.todos, [todo]);
  assert.throws(() => reduceCoordinationProjection(head.head, "goal-a", [{
    kind: "todo_upsert", todo: { ...todo, source_section: "Agent Todo" },
  }]), /unversioned fields: source_section/);
  const { archive_state: _archive, ...missingArchive } = todo;
  assert.throws(() => reduceCoordinationProjection(head.head, "goal-a", [{
    kind: "todo_upsert", todo: missingArchive,
  }]), /omits existing fields: archive_state/);
  const archive = prepareCoordinationProjectionCommit({
    goal_id: "goal-a", operation_id: "archive:domain",
    expected_provider_revision: head.provider_revision,
    projection: head.head,
    mutations: [{ kind: "todo_upsert", todo: { ...todo, archive_state: "archive" } }],
  });
  assert.equal((await store.commitAuthority(archive)).status, "applied");
  const reopened = await new FileAuthorityStore(root, "goal-a").loadAuthority();
  assert.equal(reopened.status, "loaded");
  if (reopened.status === "loaded") {
    assert.deepEqual(reopened.head.todos, [{ ...todo, archive_state: "archive" }]);
  }
  const listed = await listLocalCoordinationTodos({
    schema_version: LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
    runtime_root: root, goal_id: "goal-a",
  }, { createStore: () => new FileAuthorityStore(root, "goal-a") });
  assert.equal(listed.status, "loaded");
  assert.equal(listed.legacy_fallback_used, false);
  assert.deepEqual(listed.todos, [{ ...todo, archive_state: "archive" }]);
});

test("coordination projection indexes exact Todo identities in stable order", () => {
  const first = { todo_id: "todo_b", status: "open" };
  const second = { todo_id: "todo_a", status: "done" };

  const index = indexCoordinationProjectionTodos(
    {
      schema_version: "loopx_coordination_runtime_shadow_projection_v0",
      goal_id: "goal-a",
      todos: [first, second],
      leases: [],
    },
    "goal-a",
  );

  assert.deepEqual(index.todo_ids, ["todo_a", "todo_b"]);
  assert.deepEqual(index.todos.get("todo_b"), first);
});

test("coordination projection fails closed on goal, shape, or identity drift", () => {
  assert.throws(
    () => indexCoordinationProjectionTodos({ goal_id: "goal-b", todos: [] }, "goal-a"),
    /goal mismatch/,
  );
  assert.throws(
    () => indexCoordinationProjectionTodos({ goal_id: "goal-a", todos: {} }, "goal-a"),
    /todos must be an array/,
  );
  assert.throws(
    () => indexCoordinationProjectionTodos({
      goal_id: "goal-a",
      todos: [{ todo_id: "todo_one" }, { todo_id: "todo_one" }],
    }, "goal-a"),
    /duplicate todo ids/,
  );
});

test("coordination projection indexes leases and fences orphan identities", () => {
  const index = indexCoordinationProjection({
    goal_id: "goal-a",
    todos: [{ todo_id: "todo_a", status: "open" }],
    leases: [{ todo_id: "todo_a", owner: "agent-a" }],
  }, "goal-a");
  assert.deepEqual(index.lease_todo_ids, ["todo_a"]);
  assert.equal(index.leases.get("todo_a")?.owner, "agent-a");

  assert.throws(() => indexCoordinationProjection({
    goal_id: "goal-a",
    todos: [],
    leases: [{ todo_id: "todo_absent", owner: "agent-a" }],
  }, "goal-a"), /unknown todo/);
});

test("coordination projection reducer applies one atomic Todo and lease batch", () => {
  const reduced = reduceCoordinationProjection({
    schema_version: "loopx_coordination_runtime_shadow_projection_v0",
    goal_id: "goal-a",
    source_authority: "file_v0",
    todos: [
      { todo_id: "todo_b", status: "open" },
      { todo_id: "todo_a", status: "open", claimed_by: "agent-old" },
    ],
    leases: [{ todo_id: "todo_a", owner: "agent-old", lease_epoch: 1 }],
  }, "goal-a", [
    {
      kind: "todo_upsert",
      todo: { todo_id: "todo_a", status: "done", claimed_by: "agent-new" },
    },
    {
      kind: "lease_upsert",
      lease: { todo_id: "todo_a", owner: "agent-new", lease_epoch: 2 },
    },
  ]);

  assert.deepEqual(reduced.todos, [
    { claimed_by: "agent-new", status: "done", todo_id: "todo_a" },
    { status: "open", todo_id: "todo_b" },
  ]);
  assert.deepEqual(reduced.leases, [
    { lease_epoch: 2, owner: "agent-new", todo_id: "todo_a" },
  ]);
  assert.equal(reduced.source_authority, "file_v0");
});

test("coordination projection reducer fails closed on partial or invalid batches", () => {
  const projection = {
    goal_id: "goal-a",
    todos: [{ todo_id: "todo_a", status: "open" }],
    leases: [{ todo_id: "todo_a", owner: "agent-a" }],
  };
  assert.throws(
    () => reduceCoordinationProjection(projection, "goal-a", []),
    /batch is empty/,
  );
  assert.throws(
    () => reduceCoordinationProjection(projection, "goal-a", [{
      kind: "todo_remove",
      todo_id: "todo_a",
    }]),
    /orphan lease/,
  );
  const removed = reduceCoordinationProjection(projection, "goal-a", [
    { kind: "lease_remove", todo_id: "todo_a" },
    { kind: "todo_remove", todo_id: "todo_a" },
  ]);
  assert.deepEqual(removed.todos, []);
  assert.deepEqual(removed.leases, []);
  assert.throws(
    () => reduceCoordinationProjection(projection, "goal-a", [{
      kind: "lease_upsert",
      lease: { todo_id: "todo_absent", owner: "agent-b" },
    }]),
    /orphan lease/,
  );
  assert.throws(
    () => reduceCoordinationProjection(projection, "goal-a", [
      { kind: "todo_upsert", todo: { todo_id: "todo_a", status: "done" } },
      { kind: "todo_remove", todo_id: "todo_a" },
    ]),
    /more than once/,
  );
});

test("canonical Todo mutation rejects a replacement that drops existing consumer fields", () => {
  const todo = {
    schema_version: "todo_item_v0",
    todo_id: "todo_a",
    role: "agent",
    status: "open",
    done: false,
    text: "Qualify provider cutover",
    archive_state: "active",
    source_section: "Agent Todo",
    priority: "P0",
    title: "Provider cutover",
    resume_when: "material_change:todo_monitor",
    evidence: "receipt:cqr_example",
  };
  const projection = {
    goal_id: "goal-a",
    todos: [todo],
    leases: [],
    todo_read_model: {
      schema_version: TODO_CANONICAL_READ_RECORD_SCHEMA,
      todo_count: 1,
      records_sha256: canonicalAuthoritySha256([todo]),
      contract_fields: [...TODO_CANONICAL_READ_RECORD_FIELDS],
    },
  };

  assert.throws(
    () => reduceCoordinationProjection(projection, "goal-a", [{
      kind: "todo_upsert",
      todo: {
        schema_version: "todo_item_v0",
        todo_id: "todo_a",
        role: "agent",
        status: "done",
        done: true,
        text: "Qualify provider cutover",
        archive_state: "active",
        source_section: "Agent Todo",
      },
    }]),
    /omits existing fields: evidence, priority, resume_when, title/,
  );

  const reduced = reduceCoordinationProjection(projection, "goal-a", [{
    kind: "todo_upsert",
    todo: { ...todo, status: "done", done: true },
  }]);
  assert.deepEqual((reduced.todos as JsonObject[])[0], {
    ...todo,
    status: "done",
    done: true,
  });

  const explicitlyCleared = reduceCoordinationProjection(projection, "goal-a", [{
    kind: "todo_upsert",
    todo: { ...todo, resume_when: null, evidence: null },
  }]);
  assert.deepEqual((explicitlyCleared.todos as JsonObject[])[0], {
    ...todo,
    resume_when: null,
    evidence: null,
  });

  const inserted = reduceCoordinationProjection(projection, "goal-a", [{
    kind: "todo_upsert",
    todo: {
      schema_version: "todo_item_v0",
      todo_id: "todo_b",
      role: "agent",
      status: "open",
      done: false,
      text: "Review cutover evidence",
      archive_state: "active",
      source_section: "Agent Todo",
    },
  }]);
  assert.deepEqual((inserted.todos as JsonObject[]).map((record) => record.todo_id), [
    "todo_a",
    "todo_b",
  ]);
});

test("coordination projection commit derives one auditable atomic transaction", () => {
  const projection = {
    schema_version: "loopx_coordination_runtime_shadow_projection_v0",
    goal_id: "goal-a",
    source_authority: "file_v0",
    todos: [{ todo_id: "todo_a", status: "open" }],
    leases: [],
  };
  const commit = prepareCoordinationProjectionCommit({
    goal_id: "goal-a",
    operation_id: "todo:goal-a:todo_a:claim:1",
    expected_provider_revision: "file:revision-1",
    projection,
    mutations: [
      {
        kind: "todo_upsert",
        todo: { todo_id: "todo_a", status: "open", claimed_by: "agent-a" },
      },
      {
        kind: "lease_upsert",
        lease: { todo_id: "todo_a", owner: "agent-a", lease_epoch: 1 },
      },
    ],
  });

  assert.equal(commit.expected_provider_revision, "file:revision-1");
  assert.equal(commit.operation_id, "todo:goal-a:todo_a:claim:1");
  assert.deepEqual(commit.events[0]?.mutation_kinds, ["lease_upsert", "todo_upsert"]);
  assert.deepEqual(commit.events[0]?.targets, ["lease:todo_a", "todo:todo_a"]);
  assert.equal(
    commit.events[0]?.next_projection_sha256,
    commit.receipts[0]?.next_projection_sha256,
  );
  assert.equal(
    commit.events[0]?.mutation_sha256,
    commit.receipts[0]?.mutation_sha256,
  );
  assert.equal(
    commit.events[0]?.previous_projection_sha256,
    commit.receipts[0]?.previous_projection_sha256,
  );
  assert.deepEqual(commit.next_projection.leases, [
    { lease_epoch: 1, owner: "agent-a", todo_id: "todo_a" },
  ]);
});


// Frozen from the persisted pre-validator-revision contract, not from the
// implementation under test: future unversioned field changes must fail here.
const historicalFields = JSON.parse(await readFile(new URL(
  "../fixtures/coordination/todo-pre-validator-revision-fields.json", import.meta.url,
), "utf8"));
for (const native of [false, true]) {
  test(`historical ${native ? "native" : "canonical"} Todo head survives upgrade and next mutation`, async () => {
    const fields: string[] = historicalFields.canonical_fields.filter((field: string) =>
      !native || !historicalFields.projection_metadata_fields.includes(field));
    const todo: JsonObject = {
      schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : "todo_item_v0",
      todo_id: "todo_upgrade", role: "agent", status: "open", done: false,
      text: "Read previously accepted work after an upgrade", archive_state: "active",
      ...(native ? {} : {source_section: "Agent Todo"}),
    };
    const schema = native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA;
    const head = {
      goal_id: "goal-upgrade", todos: [todo], leases: [],
      todo_read_model: {schema_version: schema, contract_fields: fields,
        todo_count: 1, records_sha256: canonicalAuthoritySha256([todo])},
    };
    const original = structuredClone(head);
    assert.deepEqual(validateCoordinationTodoReadModel(head, head.goal_id), head.todo_read_model);
    assert.deepEqual(head, original, "reading must not rewrite a historical head");
    for (const invalid of [
      fields.filter(field => field !== "status"),
      [...fields, "completion_validation_revision"],
      [...fields, "unexpected_field"],
      [...fields].reverse(),
      [...fields, fields[0]],
    ]) {
      assert.throws(() => validateCoordinationTodoReadModel({...head,
        todo_read_model: {...head.todo_read_model, contract_fields: invalid}}, head.goal_id), /field contract mismatch/);
    }
    for (const field of ["completion_validation_revision", "completion_validation_revision_history"]) {
      const undeclared = {...todo, [field]: field.endsWith("history") ? [] : 1};
      assert.throws(() => validateCoordinationTodoReadModel({...head, todos: [undeclared],
        todo_read_model: {...head.todo_read_model, records_sha256: canonicalAuthoritySha256([undeclared])}}, head.goal_id),
      /exceeds its historical field contract/);
    }
    assert.throws(() => validateCoordinationTodoReadModel({...head,
      todo_read_model: {...head.todo_read_model, records_sha256: "0".repeat(64)}}, head.goal_id), /digest mismatch/);
    assert.throws(() => validateCoordinationTodoReadModel({...head,
      todo_read_model: {...head.todo_read_model, todo_count: 2}}, head.goal_id), /count mismatch/);
    const root = await mkdtemp(join(tmpdir(), "loopx-upgrade-todo-"));
    const store = new FileAuthorityStore(root, head.goal_id);
    const initial = await store.commitAuthority({expected_provider_revision: null,
      operation_id: "historical-head", events: [{schema_version: "bootstrap_v0"}],
      next_projection: head, receipts: []});
    assert.equal(initial.status, "applied");
    if (initial.status !== "applied") return;
    const request = {schema_version: LOCAL_COORDINATION_TODO_LIST_REQUEST_SCHEMA,
      runtime_root: root, goal_id: head.goal_id};
    const createStore = () => new FileAuthorityStore(root, head.goal_id);
    const listed = await listLocalCoordinationTodos(request, {createStore});
    assert.equal(listed.status, "loaded");
    assert.equal(listed.legacy_fallback_used, false);
    assert.deepEqual(listed.todos, [todo]);
    const reopened = await createStore().loadAuthority();
    assert.equal(reopened.status, "loaded");
    if (reopened.status !== "loaded") return;
    assert.deepEqual(reopened.head, original);
    const changed = {...todo, note: "New work after upgrade"};
    const commit = prepareCoordinationProjectionCommit({goal_id: head.goal_id,
      operation_id: "next-mutation", expected_provider_revision: initial.provider_revision,
      projection: reopened.head, mutations: [{kind: "todo_upsert", todo: changed}]});
    assert.deepEqual(commit.next_projection.todo_read_model, coordinationTodoReadModel([changed], schema));
    assert.equal((await store.commitAuthority(commit)).status, "applied");
    const updated = await listLocalCoordinationTodos(request, {createStore});
    assert.equal(updated.status, "loaded");
    assert.deepEqual(updated.todos, [changed]);
  });
}

// The release that added the validator revision fields wrote exactly the
// frozen manifest with those two fields restored; upstream inserted both
// directly after completion_validation_sha256. Its persisted heads must stay
// readable once a later release adds another additive field, otherwise an
// ordinary upgrade loses every Goal's Todo list.
const previousReleaseFields: string[] = [...historicalFields.canonical_fields];
previousReleaseFields.splice(
  previousReleaseFields.indexOf("completion_validation_sha256") + 1, 0,
  "completion_validation_revision", "completion_validation_revision_history");

for (const native of [false, true]) {
  test(`Todo head written before a later additive field stays readable (${native ? "native" : "canonical"})`, () => {
    const fields = previousReleaseFields.filter((field: string) =>
      !native || !historicalFields.projection_metadata_fields.includes(field));
    assert.equal(fields.includes("completion_validation_revision"), true);
    assert.equal(fields.includes("completion_result"), false,
      "the previous release did not declare the later additive field");
    const todo: JsonObject = {
      schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : "todo_item_v0",
      todo_id: "todo_previous_release", role: "agent", status: "open", done: false,
      text: "Read work accepted before the next additive field", archive_state: "active",
      ...(native ? {} : {source_section: "Agent Todo"}),
    };
    const schema = native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA;
    const head = {
      goal_id: "goal-previous-release", todos: [todo], leases: [],
      todo_read_model: {schema_version: schema, contract_fields: fields,
        todo_count: 1, records_sha256: canonicalAuthoritySha256([todo])},
    };
    assert.deepEqual(validateCoordinationTodoReadModel(head, head.goal_id), head.todo_read_model);
    const withResult = {...todo, completion_result: {
      schema_version: "loopx_completion_result_v0", sha256: "a".repeat(64),
      producer_agent_id: "agent-a"}};
    assert.throws(() => validateCoordinationTodoReadModel({...head, todos: [withResult],
      todo_read_model: {...head.todo_read_model, records_sha256: canonicalAuthoritySha256([withResult])}},
    head.goal_id), /exceeds its historical field contract/);
  });
}

for (const native of [false, true]) {
  test(`Todo carrying a completion result reads under the current contract (${native ? "native" : "canonical"})`, () => {
    const contract = native ? TODO_DOMAIN_RECORD_CONTRACT.fields : TODO_CANONICAL_READ_RECORD_FIELDS;
    assert.equal(contract.includes("completion_result"), true);
    const todo: JsonObject = {
      schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : "todo_item_v0",
      todo_id: "todo_completion_result", role: "agent", status: "done", done: true,
      text: "Accepted managed report", archive_state: "archive",
      completion_result: {schema_version: "loopx_completion_result_v0", sha256: "b".repeat(64),
        producer_agent_id: "agent-a", source_name: "report.md"},
      ...(native ? {} : {source_section: "Agent Todo"}),
    };
    const schema = native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA;
    const model = coordinationTodoReadModel([todo], schema);
    assert.equal((model.contract_fields as string[]).includes("completion_result"), true);
    const head = {goal_id: "goal-completion-result", todos: [todo], leases: [],
      todo_read_model: model};
    assert.deepEqual(validateCoordinationTodoReadModel(head, head.goal_id), model);
  });
}

// Reconstruct the exact released manifest from the frozen pre-revision
// fixture and its registered additions, independently of today's field list.
const preReceiptFields = [...previousReleaseFields];
preReceiptFields.splice(preReceiptFields.indexOf("completion_turn_key") + 1, 0, "completion_result");
for (const native of [false, true]) {
  test(`pre-receipt ${native ? "domain" : "canonical"} head remains readable and upgrades on commit`, () => {
    const fields = preReceiptFields.filter(field =>
      !native || !historicalFields.projection_metadata_fields.includes(field));
    const todo = {schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : "todo_item_v0",
      todo_id: "todo_receipt", role: "agent", status: "done", done: true,
      text: "Retain the committed completion", archive_state: "active",
      ...(native ? {} : {source_section: "Agent Todo"})};
    const head = {goal_id: "receipt-goal", todos: [todo], leases: [], todo_read_model: {
      schema_version: native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA,
      todo_count: 1, records_sha256: canonicalAuthoritySha256([todo]), contract_fields: fields}};
    const before = structuredClone(head);
    validateCoordinationTodoReadModel(head, head.goal_id);
    assert.deepEqual(head, before);
    const updated = {...todo, completion_receipt_id: `tcw_${"a".repeat(64)}`};
    assert.throws(() => validateCoordinationTodoReadModel({...head, todos: [updated],
      todo_read_model: {...head.todo_read_model, records_sha256: canonicalAuthoritySha256([updated])}}, head.goal_id),
      /exceeds its historical field contract/);
    const commit = prepareCoordinationProjectionCommit({goal_id: head.goal_id,
      operation_id: "receipt-upgrade", expected_provider_revision: "file:old", projection: head,
      mutations: [{kind: "todo_upsert", todo: updated}]});
    validateCoordinationTodoReadModel(commit.next_projection, head.goal_id);
    assert.equal(((commit.next_projection.todo_read_model as JsonObject).contract_fields as string[])
      .includes("completion_receipt_id"), true);
  });
}

for (const native of [false, true]) {
  test(`read-model order uses unique Unicode identities without weakening ${native ? "domain" : "canonical"} content validation`, () => {
    // U+E000 precedes U+10000 in persisted Unicode code-point order, but not
    // in JavaScript's default UTF-16 sort order. Include an archived dependency.
    const ids = ["todo_a", "todo_\uE000", "todo_\u{10000}"];
    const todos: JsonObject[] = ids.map((todo_id, index) => ({
      schema_version: native ? TODO_DOMAIN_ITEM_SCHEMA : "todo_item_v0",
      todo_id, role: "agent", status: index === 0 ? "done" : "open", done: index === 0,
      text: "Preserve the complete retained record", archive_state: index === 0 ? "archive" : "active",
      completion_result: {nested: [null, false, {label: "original"}]},
      ...(native ? {} : {source_section: "Agent Todo"}),
    }));
    const schema = native ? TODO_DOMAIN_READ_RECORD_SCHEMA : TODO_CANONICAL_READ_RECORD_SCHEMA;
    const head = {goal_id: "order-goal", todos, leases: [], todo_read_model: coordinationTodoReadModel(todos, schema)};
    const before = structuredClone(head);
    assert.deepEqual(validateCoordinationTodoReadModel(head, head.goal_id), head.todo_read_model);
    assert.deepEqual(head, before);
    for (const order of [[1, 0, 2], [0, 2, 1], [2, 1, 0]]) {
      const reordered = order.map(index => todos[index]!);
      // Even a matching digest cannot legalize a noncanonical record order.
      assert.throws(() => validateCoordinationTodoReadModel({...head, todos: reordered,
        todo_read_model: {...head.todo_read_model, records_sha256: canonicalAuthoritySha256(reordered)}}, head.goal_id),
      /deterministic todo_id order/);
    }
    assert.throws(() => validateCoordinationTodoReadModel({...head, todos: [todos[0]!, todos[0]!, todos[2]!]}, head.goal_id),
      /duplicate todo ids/);
    const altered = structuredClone(todos);
    altered[0]!.completion_result = {nested: [null, false, {label: "tampered archive"}]};
    assert.throws(() => validateCoordinationTodoReadModel({...head, todos: altered}, head.goal_id), /digest mismatch/);
    for (const invalid of [undefined, Number.NaN, new Date()]) {
      const malformed = structuredClone(todos);
      malformed[1]!.completion_result = {invalid} as unknown as JsonObject;
      assert.throws(() => validateCoordinationTodoReadModel({...head, todos: malformed}, head.goal_id));
    }
    const unknownField = todos.map(todo => ({...todo, metadata: {unversioned: true}}));
    assert.throws(() => validateCoordinationTodoReadModel({...head, todos: unknownField,
      todo_read_model: {...head.todo_read_model, records_sha256: canonicalAuthoritySha256(unknownField)}}, head.goal_id),
    /unversioned fields: metadata/);
    assert.throws(() => validateCoordinationTodoReadModel(head, "foreign-goal"), /goal mismatch/);
    assert.deepEqual(validateCoordinationTodoReadModel({...head, todos: [],
      todo_read_model: coordinationTodoReadModel([], schema)}, head.goal_id), coordinationTodoReadModel([], schema));
  });
}
