import assert from "node:assert/strict";
import test from "node:test";
import {deflateSync} from "node:zlib";
import {projectAdvancementFrontier, evaluateLongTodoChain} from "../../loopx/control_plane/todos/frontier_revision.ts";

function row(id: string, claim: string | null = null, excluded: string[] = []) {
  return {id, claim, excluded, advancement: true, updated: "2026-09-01T00:00:00.000001Z",
    serialized: JSON.stringify({task_class: "advancement_task", todo_id: id,
      ...(claim ? {claimed_by: claim} : {})})};
}
function project(rows: ReturnType<typeof row>[], agent_id: string | null = null) {
  return projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "select", rows, agent_id}).checkpoint as Record<string, unknown>;
}
function observe(overrides: Record<string, unknown> = {}) {
  return evaluateLongTodoChain({schema_version: "long_todo_chain_request_v0", operation: "observe",
    agent_id: "worker-a", summary: {current_agent_claimed_open_count: 15, unclaimed_open_count: 0},
    frontier_counts: {current_agent_claimed_advancement_count: 15, unclaimed_advancement_count: 0},
    rows: [row("todo_a", "worker-a")], ...overrides});
}

test("lossless compressed rows preserve index and ACK semantics and reject malformed transport", () => {
  const rows = [row("todo_a", "worker-a"), row("todo_b", null, ["worker-a"])];
  const compressed = {encoding: "deflate-base64-json-v0",
    data: deflateSync(JSON.stringify(rows)).toString("base64")};
  for (const operation of ["index", "select"]) {
    const request = {schema_version: "todo_frontier_revision_request_v0", operation, agent_id: "worker-a"};
    assert.deepEqual(projectAdvancementFrontier({...request, rows: compressed}),
      projectAdvancementFrontier({...request, rows}));
  }
  assert.deepEqual(observe({rows: compressed}), observe({rows}));
  for (const invalid of [
    {...compressed, encoding: "unknown"}, {...compressed, data: "!"},
    {...compressed, data: "AAAA"},
    {...compressed, data: deflateSync("{}").toString("base64")},
    {...compressed, data: deflateSync("x".repeat(64 * 1024 * 1024 + 1)).toString("base64")},
  ]) {
    assert.throws(() => projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
      operation: "index", rows: invalid}));
  }
});

test("revision is order-independent and maintenance timestamps do not change material identity", () => {
  const rows = [row("todo_b"), row("todo_a")];
  assert.deepEqual(project(rows), project([...rows].reverse()));
  const changed = [{...rows[0], updated: "2026-09-02T00:00:00Z"}, rows[1]];
  assert.equal(project(rows).frontier_revision, project(changed).frontier_revision);
  assert.notEqual(project(rows).frontier_updated_at, project(changed).frontier_updated_at);
});

test("excluded-only agents receive an explicit checkpoint instead of the unclaimed fallback", () => {
  const rows = [row("todo_a"), row("todo_b", null, ["worker-a"])];
  const index = projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "index", rows}).index;
  const read = projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "read", index, agent_id: "worker-a"}).checkpoint;
  assert.deepEqual(read, project([rows[0]], "worker-a"));
  assert.notDeepEqual(read, project(rows));
});

test("duplicate identities and malformed or absent revision facts cannot authorize an ACK", () => {
  for (const rows of [[], [row("todo_a"), row("todo_a")], [{...row("todo_a"), updated: "bad"}]]) {
    assert.deepEqual(project(rows), {complete: false});
  }
  const result = observe({summary: {advancement_frontier_revision_index: {schema_version: "bad"}}});
  assert.equal((result.observation as Record<string, unknown>).frontier_revision_complete, false);
  assert.deepEqual(result.decision, {acknowledged: false, rearmed_after_obligation_id: null});
});

test("lane thresholds count 15 claimed advancement or 20 claimed open with advancement", () => {
  assert.notEqual(observe().observation, null);
  assert.equal(observe({frontier_counts: {current_agent_claimed_advancement_count: 14}}).observation, null);
  const open = {current_agent_claimed_open_count: 20, unclaimed_open_count: 0};
  const result = observe({summary: open, frontier_counts: {current_agent_claimed_advancement_count: 1}});
  assert.equal((result.observation as Record<string, unknown>).count_kind, "claimed_open_todos");
  assert.equal((result.observation as Record<string, unknown>).trigger_count, 20);
  assert.equal(observe({summary: open, frontier_counts: {}}).observation, null);
  assert.equal(observe({summary: open, frontier_counts: {unclaimed_advancement_count: 100}}).observation, null);
  for (const current of [0, 14]) {
    assert.equal(observe({summary: {current_agent_claimed_open_count: current, unclaimed_open_count: 100},
      frontier_counts: {current_agent_claimed_advancement_count: current, unclaimed_advancement_count: 100}}).observation, null);
  }
  // Unscoped overview keeps the existing selectable-chain contract.
  assert.notEqual(observe({agent_id: null, summary: open,
    frontier_counts: {unclaimed_advancement_count: 1}}).observation, null);
});

test("only exact accepted checkpoint suppresses a repeated trigger; material change rearms", () => {
  const observation = observe().observation as Record<string, unknown>;
  const ack = {recorded: true, semantic_delta: {accepted: true, obligation_id: "replan-0123456789abcdef",
    trigger_kinds: ["long_todo_chain"], trigger_checkpoints: [{kind: "long_todo_chain",
      frontier_revision: observation.frontier_revision}]}};
  assert.deepEqual(observe({ack}).decision, {acknowledged: true, rearmed_after_obligation_id: null});
  assert.deepEqual(observe({ack, rows: [row("todo_new", "worker-a")]}).decision,
    {acknowledged: false, rearmed_after_obligation_id: "replan-0123456789abcdef"});
  for (const invalid of [null, {...ack, recorded: false}, {...ack, semantic_delta: {...ack.semantic_delta,
    accepted: false}}, {...ack, semantic_delta: {...ack.semantic_delta, trigger_kinds: "long_todo_chain"}}]) {
    assert.equal((observe({ack: invalid}).decision as Record<string, unknown>).acknowledged, false);
  }
});

test("duplicate agent checkpoints fail closed instead of selecting the first receipt", () => {
  const entry = {agent_id: "worker-a", ...project([row("todo_a")])};
  const index = {schema_version: "todo_frontier_revision_index_v0", by_agent: [entry, entry]};
  assert.deepEqual(projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "read", index, agent_id: "worker-a"}).checkpoint, {complete: false});
});

test("the typed transport rejects unsupported operations and missing source codec facts", () => {
  assert.throws(() => projectAdvancementFrontier({schema_version: "old"}));
  assert.throws(() => projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "index", rows: [{}]}));
  assert.throws(() => evaluateLongTodoChain({schema_version: "long_todo_chain_request_v0", operation: "unknown"}));
});

test("another lane taking over an unclaimed row does not re-arm this lane's ACK", () => {
  // The selectable set counts rows nobody has claimed yet, so a busy goal moves
  // this lane's revision whenever another lane claims or edits that shared work.
  const before = [row("todo_a", "worker-a"), row("todo_b")];
  const observation = observe({rows: before}).observation as Record<string, unknown>;
  assert.equal(typeof observation.frontier_owned_identity, "string");
  const ack = {recorded: true, semantic_delta: {accepted: true, obligation_id: "replan-0123456789abcdef",
    trigger_kinds: ["long_todo_chain"], trigger_checkpoints: [{kind: "long_todo_chain",
      frontier_revision: observation.frontier_revision,
      frontier_owned_identity: observation.frontier_owned_identity}]}};

  const claimedElsewhere = observe({ack, rows: [row("todo_a", "worker-a"), row("todo_b", "worker-b")]});
  assert.notEqual((claimedElsewhere.observation as Record<string, unknown>).frontier_revision,
    observation.frontier_revision);
  assert.deepEqual(claimedElsewhere.decision, {acknowledged: true, rearmed_after_obligation_id: null});

  // This agent's own selectable rows changed, so the replan is owed again.
  const ownChange = observe({ack, rows: [row("todo_a", "worker-a"), row("todo_b"),
    row("todo_c", "worker-a")]});
  assert.deepEqual(ownChange.decision,
    {acknowledged: false, rearmed_after_obligation_id: "replan-0123456789abcdef"});
  assert.equal((observe({ack, rows: [row("todo_a"), row("todo_b")]}).decision as
    Record<string, unknown>).acknowledged, false);

  // An ACK recorded before the owned identity existed still matches on revision.
  const legacy = {...ack, semantic_delta: {...ack.semantic_delta, trigger_checkpoints: [
    {kind: "long_todo_chain", frontier_revision: observation.frontier_revision}]}};
  assert.deepEqual(observe({ack: legacy, rows: before}).decision,
    {acknowledged: true, rearmed_after_obligation_id: null});
});

test("observation, source and writeback preserve one complete checkpoint", () => {
  const rows = [row("todo_a", "worker-a"), row("todo_shared")];
  const result = observe({rows}).observation as Record<string, unknown>;
  const request = {schema_version: "todo_frontier_revision_request_v0", agent_id: "worker-a"};
  const source = projectAdvancementFrontier({...request, operation: "successor_checkpoints", rows})
    .source_checkpoint as Record<string, unknown>;
  const writeback = projectAdvancementFrontier({...request, operation: "trigger_checkpoints",
    triggers: [result.trigger]}).trigger_checkpoints;
  assert.deepEqual(writeback, source.trigger_checkpoints);
  const replaced = projectAdvancementFrontier({...request, operation: "successor_checkpoints", rows,
    triggers: [{kind: "other", frontier_revision: "r"},
      {kind: "long_todo_chain", frontier_revision: "old"}, result.trigger]}).source_checkpoint as
        Record<string, unknown>;
  assert.deepEqual(replaced.trigger_checkpoints, [
    {kind: "other", frontier_revision: "r"}, ...(source.trigger_checkpoints as object[])]);
  assert.equal((source.trigger_checkpoints as Record<string, unknown>[])[0].frontier_owned_identity,
    result.frontier_owned_identity);
  const index = projectAdvancementFrontier({...request, operation: "index", rows}).index;
  assert.deepEqual(projectAdvancementFrontier({...request, operation: "successor_checkpoints", index})
    .source_checkpoint, source);
  // A present incomplete index is authoritative; a valid fallback cannot heal it.
  assert.equal(projectAdvancementFrontier({...request, operation: "successor_checkpoints", rows,
    index: {schema_version: "invalid"}}).source_checkpoint, null);
});

test("checkpoint transport preserves legacy revisions and rejects incomplete identity authority", () => {
  const checkpoints = projectAdvancementFrontier({schema_version: "todo_frontier_revision_request_v0",
    operation: "trigger_checkpoints", triggers: [null, {},
      {kind: "long_todo_chain", frontier_owned_identity: "owned"},
      {kind: "long_todo_chain", frontier_revision: "r", frontier_revision_complete: false,
        frontier_owned_identity: "owned"},
      {kind: " long_todo_chain ", frontier_revision: " r ", frontier_owned_identity: " owned "},
      {kind: "long_todo_chain", frontier_revision: "legacy"},
      {kind: "other", frontier_revision: "other-revision", frontier_owned_identity: "owned"},
    ]}).trigger_checkpoints;
  assert.deepEqual(checkpoints, [
    {kind: "long_todo_chain", frontier_revision: "r", frontier_owned_identity: "owned"},
    {kind: "long_todo_chain", frontier_revision: "legacy"},
    {kind: "other", frontier_revision: "other-revision"},
  ]);
  const observation = observe().observation as Record<string, unknown>;
  const ack = {recorded: true, semantic_delta: {accepted: true, obligation_id: "replan-0123456789abcdef",
    trigger_kinds: ["long_todo_chain"], trigger_checkpoints: [{kind: "long_todo_chain",
      frontier_owned_identity: observation.frontier_owned_identity}]}};
  assert.equal((observe({ack}).decision as Record<string, unknown>).acknowledged, false);
  const incomplete = observe({rows: null}).observation as Record<string, unknown>;
  assert.equal((incomplete.trigger as Record<string, unknown>).frontier_owned_identity, undefined);
  assert.equal((incomplete.trigger as Record<string, unknown>).frontier_revision, undefined);
});

test("an entirely unclaimed chain cannot borrow the owned-work exemption", () => {
  const observation = observe({rows: [row("todo_unclaimed")]}).observation as Record<string, unknown>;
  const ack = {recorded: true, semantic_delta: {accepted: true, obligation_id: "replan-0123456789abcdef",
    trigger_kinds: ["long_todo_chain"], trigger_checkpoints: [observation.trigger]}};
  assert.equal(observation.frontier_owned_identity, null);
  assert.equal((observe({ack, rows: [row("todo_replacement")]}).decision as Record<string, unknown>)
    .acknowledged, false);
});

test("successor reconstruction requires a complete matching source and an already-long predecessor", () => {
  const rows = Array.from({length: 16}, (_, i) => row(`todo_${i}`, "worker-a"));
  rows[15].updated = "2026-09-02T00:00:00Z";
  const current = project(rows, "worker-a");
  const request = {schema_version: "todo_frontier_revision_request_v0", operation: "successor_checkpoints",
    agent_id: "worker-a", rows, obligation_id: "replan-current",
    candidates: [{todo_id: "todo_15", updated_at: rows[15].updated, origin_obligation_id: "replan-prior"}],
    triggers: [{kind: "long_todo_chain", ...current,
      current_agent_claimed_advancement_count: 16, current_agent_claimed_open_count: 16}]};
  const result = projectAdvancementFrontier(request).source_checkpoint as Record<string, unknown>;
  assert.deepEqual(result.bindings, [{kind: "predecessor", todo_id: "todo_15",
    frontier_revision: project(rows.slice(0, 15), "worker-a").frontier_revision,
    obligation_identity_revision: project(rows.slice(0, 15), "worker-a").frontier_owned_identity}]);
  for (const triggers of [
    [{...request.triggers[0], current_agent_claimed_advancement_count: 15, current_agent_claimed_open_count: 15}],
    [{...request.triggers[0], frontier_revision: "different-current-source"}],
    [...request.triggers, {kind: "periodic_review"}],
  ]) {
    assert.deepEqual((projectAdvancementFrontier({...request, triggers}).source_checkpoint as Record<string, unknown>).bindings, []);
  }
});
