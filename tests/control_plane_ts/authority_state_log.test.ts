import assert from "node:assert/strict";
import test from "node:test";

import {
  AUTHORITY_STATE_CHECKPOINT_INTERVAL,
  AUTHORITY_STATE_DELTA_SCHEMA,
  applyAuthorityStateDelta,
  authorityStateCheckpointCursor,
  authorityStateDelta,
  authorityStateDeltaReconstructs,
  authorityStateDigest,
  authorityStateReplayBudget,
  decodeAuthorityStateDelta,
  isAuthorityStateCheckpoint,
} from "../../loopx/control_plane/coordination/authority_state_log.ts";
import {canonicalAuthorityBytes} from
  "../../loopx/control_plane/coordination/authority_store_codec.ts";

test("authority state deltas reconstruct every committed projection exactly", () => {
  const previous = {
    authority_revision: 3,
    todos: [{todo_id: "a", status: "open"}, {todo_id: "b", status: "open"},
      {todo_id: "c", status: "done"}],
    leases: [],
    nested: {scope: {key: "goal", depth: {value: 1}}},
  };
  const next = {
    authority_revision: 4,
    todos: [{todo_id: "a", status: "done"}, {todo_id: "b", status: "open"},
      {todo_id: "c", status: "done"}, {todo_id: "d", status: "open"}],
    leases: [{todo_id: "a", version: 2}],
    nested: {scope: {key: "goal"}},
  };
  const delta = authorityStateDelta(previous, next);
  assert.equal(delta.schema_version, AUTHORITY_STATE_DELTA_SCHEMA);
  assert.deepEqual(applyAuthorityStateDelta(previous, delta), next);
  assert.deepEqual(authorityStateDelta(next, next).operations, []);
  assert.deepEqual(applyAuthorityStateDelta(next, authorityStateDelta(next, next)), next);
  // Field edits recurse, so an unrelated field never rewrites whole subtrees.
  const fieldEdit = authorityStateDelta(previous, {...previous, authority_revision: 9});
  assert.deepEqual(fieldEdit.operations, [{op: "set", path: ["authority_revision"], value: 9}]);
  // One changed record inside a record array stays one bounded splice.
  const large = {todos: Array.from({length: 64}, (_unused, index) => ({todo_id: `t-${index}`, step: 0}))};
  const added = {...large, todos: large.todos.map((todo, index) =>
    index === 20 ? {...todo, step: 1} : todo)};
  const spliced = authorityStateDelta(large, added);
  assert.equal(spliced.operations.length, 1);
  assert.equal(spliced.operations[0]!.op, "splice");
  assert.deepEqual(applyAuthorityStateDelta(large, spliced), added);
});

test("authority state delta decoding fails closed at the storage boundary", () => {
  const previous = {a: {b: 1}, list: [1, 2, 3]};
  const delta = (operations: unknown): Record<string, unknown> =>
    ({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations});
  const rejected: unknown[] = [
    {schema_version: "authority_state_delta_v1", operations: []},
    {...delta([]), extra: true},
    {...delta([]), schema_version: 7},
    delta([{op: "merge", path: ["a"]}]),
    delta([{op: "set", path: [], value: 1}]),
    delta([{op: "set", path: ["a", 0], value: 1}]),
    delta([{op: "set", path: ["a"], value: 1, extra: 2}]),
    delta([{op: "splice", path: ["list"], index: -1, remove: 0, insert: []}]),
    delta([{op: "splice", path: ["list"], index: 0, remove: 0}]),
    delta(["not-an-operation"]),
    {schema_version: AUTHORITY_STATE_DELTA_SCHEMA},
  ];
  for (const value of rejected) {
    assert.throws(() => decodeAuthorityStateDelta(value), /delta|operation|path|splice/u,
      `delta ${JSON.stringify(value)} was accepted`);
    assert.throws(() => applyAuthorityStateDelta(previous, value as never), /delta|operation|path|splice/u);
  }
  // Structurally valid deltas that do not describe this state are refused when
  // they are applied instead of silently producing a partial projection.
  const removable = decodeAuthorityStateDelta(delta([{op: "remove", path: ["a"]}]));
  assert.deepEqual(applyAuthorityStateDelta(previous, removable), {list: [1, 2, 3]});
  // One past the end is a legal append, not an out-of-range splice.
  assert.deepEqual(applyAuthorityStateDelta(previous,
    decodeAuthorityStateDelta(delta([{op: "splice", path: ["list"], index: 3, remove: 0, insert: [4]}]))),
  {a: {b: 1}, list: [1, 2, 3, 4]});
  const inapplicable: unknown[] = [
    delta([{op: "remove", path: ["absent"]}]),
    delta([{op: "splice", path: ["a"], index: 0, remove: 0, insert: []}]),
    delta([{op: "splice", path: ["list"], index: 2, remove: 2, insert: []}]),
    delta([{op: "splice", path: ["list"], index: 4, remove: 0, insert: [4]}]),
    delta([{op: "set", path: ["missing", "leaf"], value: 1}]),
  ];
  for (const value of inapplicable) {
    const decoded = decodeAuthorityStateDelta(value);
    assert.throws(() => applyAuthorityStateDelta(previous, decoded), /path|splice|never stored/u);
    assert.throws(() => applyAuthorityStateDelta({other: 1}, decoded), /path|splice|never stored/u);
  }
});

test("the reconstruction rule answers for every JSON object key and a broken delta", () => {
  // One owner decides "this delta rebuilds exactly this projection" for both
  // the live writer and the V1 migration, so this test is about the rule's own
  // contract: it answers for a projection keyed with `""` or `__proto__`, and a
  // delta that cannot be decoded or applied is a failed reconstruction rather
  // than a thrown error or a partial state.
  const special = JSON.parse(
    '{"": {"marker": "empty"}, "__proto__": {"marker": "proto"}, "todos": [{"id": "a"}]}',
  ) as Record<string, unknown>;
  const nested = JSON.parse('{"scope": {"": {"__proto__": {"depth": 1}}}}') as Record<string, unknown>;
  for (const projection of [{}, special, nested]) {
    assert.equal(authorityStateDeltaReconstructs({}, authorityStateDelta({}, projection), projection), true);
  }
  // A delta that decodes but describes a different projection is a failed
  // reconstruction, not a different outcome the caller has to interpret.
  const mismatched = decodeAuthorityStateDelta({schema_version: AUTHORITY_STATE_DELTA_SCHEMA,
    operations: [{op: "set", path: ["a"], value: 2}]});
  assert.equal(authorityStateDeltaReconstructs({}, mismatched, {a: 1}), false);
  assert.equal(authorityStateDeltaReconstructs({}, mismatched, {a: 2}), true);
  assert.equal(authorityStateDeltaReconstructs({}, mismatched, {}), false);
  // An undecodable delta, and a delta whose path leaves the previous state,
  // both fail closed through the same answer instead of propagating.
  const undecodable = {schema_version: AUTHORITY_STATE_DELTA_SCHEMA,
    operations: [{op: "set", path: [], value: 1}]} as never;
  assert.equal(authorityStateDeltaReconstructs({}, undecodable, {}), false);
  const missingPath = decodeAuthorityStateDelta({schema_version: AUTHORITY_STATE_DELTA_SCHEMA,
    operations: [{op: "splice", path: ["absent"], index: 0, remove: 0, insert: []}]});
  assert.equal(authorityStateDeltaReconstructs({}, missingPath, {}), false);
});

test("authority state digests and checkpoint windows are stable and bounded", () => {
  const left = {b: 2, a: [1, {z: 1, y: 2}]};
  const right = {a: [1, {y: 2, z: 1}], b: 2};
  assert.equal(authorityStateDigest(left), authorityStateDigest(right));
  assert.notEqual(authorityStateDigest(left), authorityStateDigest({...left, b: 3}));
  assert.equal(AUTHORITY_STATE_CHECKPOINT_INTERVAL, 64);
  assert.equal(authorityStateReplayBudget(), AUTHORITY_STATE_CHECKPOINT_INTERVAL - 1);
  // Every commit in one window resumes from the same published checkpoint.
  for (let cursor = 1; cursor <= AUTHORITY_STATE_CHECKPOINT_INTERVAL; cursor += 1) {
    assert.equal(authorityStateCheckpointCursor(BigInt(cursor)), 1n);
  }
  assert.equal(authorityStateCheckpointCursor(65n), 65n);
  assert.equal(authorityStateCheckpointCursor(128n), 65n);
  assert.equal(authorityStateCheckpointCursor(129n), 129n);
  assert.equal(isAuthorityStateCheckpoint(1n), true);
  assert.equal(isAuthorityStateCheckpoint(64n), false);
  assert.throws(() => authorityStateCheckpointCursor(0n), /checkpoint cursor/u);
});

test("authority state deltas keep every JSON key the stored projection could carry", () => {
  // V1 retained a whole projection per commit, so every JSON object key was
  // legal legacy input. The delta codec inherits that contract: an empty key
  // is an ordinary key, and a `__proto__` key is stored data instead of the
  // inherited accessor. Keys that only existed in V1 data would otherwise be
  // copied into the new format and fail on the first read.
  const text = (value: unknown): string => canonicalAuthorityBytes(value).toString("utf8");
  const stored = (previous: unknown, next: unknown): unknown => JSON.parse(
    JSON.stringify(authorityStateDelta(previous as never, next as never))) as unknown;

  const previous = JSON.parse('{"": {"empty": true}, "__proto__": {"own": 1}, "todos": []}') as
    Record<string, unknown>;
  const next = JSON.parse('{"": {"empty": false}, "__proto__": {"own": 2},' +
    ' "nested": {"__proto__": {"deep": true}}, "todos": [{"todo_id": "todo-0"}]}') as
    Record<string, unknown>;
  const applied = applyAuthorityStateDelta(previous, decodeAuthorityStateDelta(stored(previous, next)));
  assert.equal(text(applied), text(next));
  // The reconstruction is an ordinary JSON object: `__proto__` stayed a key.
  assert.equal(Object.getPrototypeOf(applied), Object.prototype);
  assert.deepEqual(Object.keys(applied).sort(), ["", "__proto__", "nested", "todos"]);
  assert.deepEqual(applied[""], {empty: false});
  assert.deepEqual(applied["__proto__"], {own: 2});
  assert.deepEqual(applied["nested"], {["__proto__"]: {deep: true}});

  // Removing a legacy key is legal too, including the empty key and a key that
  // the previous state only carried inside a nested object.
  const trimmed = JSON.parse('{"nested": {}, "todos": []}') as Record<string, unknown>;
  const removal = applyAuthorityStateDelta(next, decodeAuthorityStateDelta(stored(next, trimmed)));
  assert.equal(text(removal), text(trimmed));
  assert.deepEqual(Object.keys(removal).sort(), ["nested", "todos"]);
  assert.equal(Object.hasOwn(removal, "__proto__"), false);

  // A legacy key the delta has to create is an own data property as well.
  const legacyText = '{"__proto__": {"own": 1}, "": 0}';
  const created = applyAuthorityStateDelta({},
    decodeAuthorityStateDelta(stored({}, JSON.parse(legacyText) as Record<string, unknown>)));
  assert.equal(text(created), text(JSON.parse(legacyText)));
  assert.equal(Object.getPrototypeOf(created), Object.prototype);
});

test("delta batches preserve input ownership and apply operations in order", () => {
  const previous = {nested: {metadata: {attempt: 1}}, list: [{value: 1}, {value: 2}]};
  const operations = [
    {op: "set", path: ["nested", "metadata", "attempt"], value: 2},
    {op: "splice", path: ["list"], index: 1, remove: 1, insert: [{value: 3}]},
    {op: "set", path: ["new"], value: {branch: 1}},
    {op: "remove", path: ["new", "branch"]},
  ];
  const delta = {schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations} as never;
  const result = applyAuthorityStateDelta(previous, delta);
  assert.deepEqual(result, {nested: {metadata: {attempt: 2}}, list: [{value: 1}, {value: 3}], new: {}});
  assert.deepEqual(previous, {nested: {metadata: {attempt: 1}}, list: [{value: 1}, {value: 2}]});
  (result.nested as {metadata: {attempt: number}}).metadata.attempt = 99;
  assert.equal(previous.nested.metadata.attempt, 1);
  assert.throws(() => applyAuthorityStateDelta(previous, {schema_version: AUTHORITY_STATE_DELTA_SCHEMA,
    operations: [...operations, {op: "remove", path: ["absent"]}]} as never));
  assert.equal(previous.nested.metadata.attempt, 1);
  assert.deepEqual(applyAuthorityStateDelta({negative: -0, list: Array(2)},
    {schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: []}), {negative: -0, list: Array(2)});
});

test("private replay keeps exact proofs while copying only changed paths", async () => {
  const {AuthorityStateReplay} = await import("../../loopx/control_plane/coordination/authority_state_log.ts");
  const {canonicalAuthoritySha256} = await import("../../loopx/control_plane/coordination/authority_store_codec.ts");
  const initial = {todos: [{id: "a", metadata: {attempt: 1}}, {id: "b"}], nested: {value: 1}};
  const replay = new AuthorityStateReplay(initial);
  initial.todos[0]!.metadata!.attempt = 100; // Caller never owns the replay's nodes.
  const before = replay.canonicalJson();
  const beforeDigest = replay.stateDigest();
  assert.equal(before, '{"nested":{"value":1},"todos":[{"id":"a","metadata":{"attempt":1}},{"id":"b"}]}');
  const insert = {id: "a", metadata: {attempt: 2}};
  replay.apply({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [
    {op: "splice", path: ["todos"], index: 0, remove: 1, insert: [insert]},
    {op: "set", path: ["nested", "value"], value: 2},
  ]});
  insert.metadata.attempt = 200;
  const expected = {nested: {value: 2}, todos: [{id: "a", metadata: {attempt: 2}}, {id: "b"}]};
  assert.deepEqual(replay.snapshot(), expected);
  assert.notEqual(replay.stateDigest(), beforeDigest);
  assert.equal(replay.stateDigest(), canonicalAuthoritySha256(expected));
  const fields = {expected_provider_revision: "store:1", operation_id: "op-2", events: [{step: 2}], receipts: [{done: true}]};
  assert.equal(replay.commitDigest(fields), canonicalAuthoritySha256({...fields, next_projection: expected}));
  fields.receipts[0]!.done = false;
  assert.equal(replay.commitDigest(fields), canonicalAuthoritySha256({...fields, next_projection: expected}));
  const copy = replay.snapshot(); (copy.nested as {value: number}).value = 999;
  const durableCopy = JSON.parse(replay.canonicalJson()); durableCopy.todos[1].id = "changed";
  assert.deepEqual(replay.snapshot(), expected);
  const stable = replay.canonicalJson();
  assert.throws(() => replay.apply({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [
    {op: "set", path: ["nested", "value"], value: 3}, {op: "remove", path: ["missing"]},
  ]}));
  assert.equal(replay.canonicalJson(), stable, "failed batch must not advance replay");
  replay.apply({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: []});
  assert.equal(replay.canonicalJson(), stable);
});

test("replay snapshots isolate mutable JSON while retaining primitive edge cases", async () => {
  const {AuthorityStateReplay} = await import("../../loopx/control_plane/coordination/authority_state_log.ts");
  const initial = {
    padding: "p".repeat(1024 * 1024),
    negative: -0,
    holes: Array(2),
    unicode: "\ud800🎛",
    ["__proto__"]: {marker: "data"},
    nested: {rows: [{value: 1}, {value: 2}]},
  };
  const replay = new AuthorityStateReplay(initial);
  const first = replay.snapshot(), second = replay.snapshot();
  assert.deepEqual(first, initial);
  assert.equal(Object.is(first.negative, -0), true);
  assert.equal(0 in (first.holes as unknown[]), false);
  assert.equal(Object.hasOwn(first, "__proto__"), true);
  assert.equal(Object.getPrototypeOf(first), Object.prototype);
  const rows = (value: Record<string, unknown>) =>
    (value.nested as {rows: {value: number}[]}).rows;
  rows(first)[0]!.value = 99;
  rows(first).push({value: 3});
  (first["__proto__"] as {marker: string}).marker = "edited";
  assert.deepEqual(rows(second), [{value: 1}, {value: 2}]);
  assert.deepEqual(second["__proto__"], {marker: "data"});
  assert.deepEqual(replay.snapshot(), initial);
  replay.apply({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [
    {op: "set", path: ["nested", "rows"], value: [{value: 4}]},
  ]});
  assert.deepEqual(rows(second), [{value: 1}, {value: 2}]);
  assert.deepEqual(rows(replay.snapshot()), [{value: 4}]);
  assert.equal(second.padding, initial.padding);
  assert.equal(({} as Record<string, unknown>).marker, undefined);
});

test("replay encoding preserves canonical Unicode, sparse values, numeric keys and strict boundaries", async () => {
  const {AuthorityStateReplay} = await import("../../loopx/control_plane/coordination/authority_state_log.ts");
  const {createHash} = await import("node:crypto");
  const hash = (bytes: string) => createHash("sha256").update(bytes).digest("hex");
  const value = JSON.parse('{"10":1,"2":2,"__proto__":{"own":true},"":0,"Ω":"🙂"}');
  const replay = new AuthorityStateReplay(value);
  assert.equal(replay.stateDigest(), hash('{"2":2,"10":1,"":0,"__proto__":{"own":true},"Ω":"🙂"}'));
  replay.apply({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [
    {op: "set", path: ["1"], value: "first"}, {op: "remove", path: ["__proto__", "own"]},
  ]});
  assert.equal(replay.canonicalJson(), '{"1":"first","2":2,"10":1,"":0,"__proto__":{},"Ω":"🙂"}');
  const sparse = new AuthorityStateReplay({items: Array(2)});
  assert.equal(sparse.canonicalJson(), '{"items":[null,null]}');
  const cycle: unknown[] = []; cycle.push(cycle);
  for (const bad of [undefined, NaN, Infinity, 1n, new Date(), {bad: undefined}, {cycle}]) {
    assert.throws(() => new AuthorityStateReplay(bad));
  }
  for (const delta of [
    {schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: Array(1)},
    {schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [{op: "set", path: Array(1), value: 1}]},
    {schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [{op: "splice", path: ["items"], index: 0, remove: 0, insert: Array(1)}]},
  ]) assert.throws(() => sparse.apply(delta as never));
  for (const text of ['"\\\n'.repeat(1200), "\ud800".repeat(1200), "🙂".repeat(600), "x".repeat(3 * 1024 ** 2),
    ...Array.from({length: 8}, (_, i) => String(i).repeat(1024 ** 2))]) {
    replay.apply({schema_version: AUTHORITY_STATE_DELTA_SCHEMA, operations: [{op: "set", path: ["payload"], value: text}]});
    assert.equal(replay.stateDigest(), hash(canonicalAuthorityBytes(replay.snapshot()).toString("utf8")));
  }
});
