import assert from "node:assert/strict";
import test from "node:test";
import {normalizeExploreResultAttachment, planExploreQuestionEvolution} from "../../loopx/control_plane/capabilities/explore_result_writeback.ts";

const attachment = {
  schema_version: "explore_result_attachment_v0", node_id: "tail-bound",
  question: "Does a finite prefix establish a tail bound?",
  applicability: "Finite prefix only", input_revision: "fixture-v1",
  observation: "A divergent tail shares the prefix.",
  interpretation: "Require a uniform estimate.", status: "refuted",
  evidence_refs: ["validation:prefix", "validation:tail"],
};

const execution = {schema_version: "explore_execution_observation_v0",
  candidate_sha256: "a".repeat(64), input_revision: attachment.input_revision,
  planned_method_id: "scheduler", executed_method_id: "mask-selection",
  change_reason: "The control identified mask selection as the first discriminating change.",
  elapsed_seconds: 0.2, limit_seconds: 30, search_iterations: 2,
  stop_reason: "process_exit:0; solver_report:search_cap", evidence_refs: attachment.evidence_refs};

test("execution observations bind resources and changed methods without implying adoption", () => {
  const normalized = normalizeExploreResultAttachment({attachment: {...attachment, execution_observation: execution}});
  assert.deepEqual(normalized.execution_observation, execution);
  assert.deepEqual(normalizeExploreResultAttachment({attachment}), attachment);
  const unknown = {...execution, elapsed_seconds: null, search_iterations: null, limit_seconds: null, stop_reason: null};
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...attachment, execution_observation: unknown}}).execution_observation, unknown);
  for (const mutation of [{change_reason: null}, {input_revision: "foreign-input"},
    {search_iterations: -1}, {search_iterations: 1.1}, {elapsed_seconds: NaN}, {limit_seconds: 0},
    {evidence_refs: ["foreign:probe"]}, {candidate_sha256: "finding-digest-is-not-code"},
    {result_ref: attachment.evidence_refs[0]},
    {result_ref: attachment.evidence_refs[0], result_candidate_sha256: "b".repeat(64)}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: {...attachment,
      execution_observation: {...execution, ...mutation}}}));
  }
  const result = {...execution, result_ref: attachment.evidence_refs[0], result_candidate_sha256: execution.candidate_sha256};
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...attachment, execution_observation: result}}).execution_observation, result);
});

test("execution observations reuse the finding's existing evidence references", () => {
  const refs = ["validation:run/1", "result:released/1"];
  const ordinary = {...attachment, evidence_refs: refs};
  const observed = {...execution, evidence_refs: refs, result_ref: refs[1],
    result_candidate_sha256: execution.candidate_sha256};
  assert.deepEqual(normalizeExploreResultAttachment({attachment: ordinary}), ordinary);
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...ordinary, execution_observation: observed}}),
    {...ordinary, execution_observation: {...observed, evidence_refs: [...refs].sort()}});
});

test("execution reference compatibility preserves attribution, text bounds and method identifiers", () => {
  const refs = ["validation:run/1", "result:released/1"];
  const ordinary = {...attachment, evidence_refs: refs};
  const observed = {...execution, evidence_refs: refs};
  const normalize = (mutation: Record<string, unknown>) => normalizeExploreResultAttachment({
    attachment: {...ordinary, execution_observation: {...observed, ...mutation}}});
  assert.throws(() => normalize({evidence_refs: ["validation:foreign/1"]}), /finding's input revision and evidence references/);
  for (const mutation of [
    {result_ref: "result:foreign/1", result_candidate_sha256: execution.candidate_sha256},
    {result_ref: refs[1], result_candidate_sha256: "b".repeat(64)},
  ]) assert.throws(() => normalize(mutation), /this executed candidate and an included evidence reference/);
  for (const field of ["planned_method_id", "executed_method_id"]) {
    assert.throws(() => normalize({[field]: "method/implementation"}), /opaque identifier/);
  }
  for (const invalid of ["validation:run/1\t", "v".repeat(129)]) {
    assert.throws(() => normalize({evidence_refs: [invalid]}), /bounded single-line text/);
    assert.throws(() => normalize({result_ref: invalid,
      result_candidate_sha256: execution.candidate_sha256}), /bounded single-line text/);
  }
  assert.throws(() => normalize({evidence_refs: ["/absolute/probe"]}), /opaque identifier/);
  const bounded = "v".repeat(128);
  const maximum = {...execution, evidence_refs: [bounded], result_ref: bounded,
    result_candidate_sha256: execution.candidate_sha256};
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...attachment,
    evidence_refs: [bounded], execution_observation: maximum}}).execution_observation, maximum);
});

test("question lifecycle is explicit, independent of finding status, and available on path capture", () => {
  const update = {status: "resolved"};
  const relations = [{to_node: "uniform-estimate", edge_type: "depends_on", summary: "Required before transfer."}];
  const input = {...attachment, question_update: update, relations};
  assert.equal(normalizeExploreResultAttachment({attachment: input}).status, "refuted");
  assert.deepEqual(normalizeExploreResultAttachment({attachment: input}).question_update, update);
  assert.equal(Object.hasOwn(normalizeExploreResultAttachment({attachment}), "question_update"), false);
  const result = normalizeExploreResultAttachment({attachment: {...scope, question_update: update, relations},
    vision_packet: {path_delta: delta}});
  assert.deepEqual(result.question_update, update);
  assert.deepEqual(result.relations, relations);
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...input,
    relations: [...relations, {to_node: "counterexample", edge_type: "supports", summary: "A separate bound."}]},
  other_attachment: {...input,
    relations: [{to_node: "counterexample", edge_type: "supports", summary: "A separate bound."}, ...relations]}}).question_update, update);
});

test("question updates reject malformed statuses, invented fields and unsupported relation intent", () => {
  for (const update of [null, {}, {status: "confirmed"}, {status: "blocked"},
    {status: "blocked", blocked_reason: ""}, {status: "open", blocked_reason: "Not blocked"},
    {status: "resolved", title: "Rewrite scope"}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: {...attachment, question_update: update}}));
  }
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...attachment,
    question_update: {status: "blocked", blocked_reason: "Need the uniform bound to continue."}}}).question_update,
  {status: "blocked", blocked_reason: "Need the uniform bound to continue."});
  const relation = {to_node: "bound", edge_type: "depends_on", summary: "Required prerequisite."};
  for (const relations of [null, [], [relation, relation], [{...relation, to_node: attachment.node_id}],
    [{...relation, edge_type: "happened_after"}], [{...relation, summary: ""}],
    [{...relation, from_node: "foreign"}]]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: {...attachment, relations}}));
  }
});

test("stateful question evolution uses its current owner, existing targets and acyclic prerequisites", () => {
  const node = {node_id: attachment.node_id, agent_id: "worker", node_kind: "question",
    title: attachment.question, summary: attachment.applicability};
  const relation = {to_node: "bound", edge_type: "depends_on", summary: "Required before transfer."};
  const request = {attachment: {...attachment, question_update: {status: "resolved"}, relations: [relation]},
    agent_id: "worker", nodes: [node, {node_id: "bound"}], edges: []};
  assert.deepEqual(planExploreQuestionEvolution(request), {existing_node: node});
  assert.throws(() => planExploreQuestionEvolution({...request, agent_id: "other"}), /owner/);
  assert.throws(() => planExploreQuestionEvolution({...request, nodes: [{...node, summary: "Changed scope"}, {node_id: "bound"}]}), /scope/);
  assert.throws(() => planExploreQuestionEvolution({...request, nodes: [node]}), /target/);
  assert.throws(() => planExploreQuestionEvolution({...request, edges: [{from_node: "bound", to_node: node.node_id,
    edge_type: "depends_on"}]}), /cycle/);
});

test("two explicit sources normalize and coalesce equivalent scoped evidence", () => {
  const other = {...attachment, observation: ` ${attachment.observation} `,
    evidence_refs: ["validation:tail", "validation:prefix", "validation:tail"]};
  assert.deepEqual(normalizeExploreResultAttachment({attachment, other_attachment: other}), attachment);
});

test("first capture derives stable scope identity without deriving evidence or merging explicit ids", () => {
  const {node_id: _id, ...first} = attachment;
  const result = normalizeExploreResultAttachment({attachment: first});
  assert.match(result.node_id as string, /^[A-Za-z][A-Za-z0-9_.:-]{0,95}$/);
  const later = normalizeExploreResultAttachment({attachment: {...first,
    input_revision: "fixture-v2", observation: "A second counterexample.", status: "tentative"}});
  assert.equal(result.node_id, later.node_id);
  assert.equal(later.status, "tentative");
  for (const different of [{question: "Does a uniform estimate suffice?"},
    {applicability: "Uniform tail estimate required"}]) {
    assert.notEqual(normalizeExploreResultAttachment({attachment: {...first, ...different}}).node_id, result.node_id);
  }
  assert.deepEqual(normalizeExploreResultAttachment({attachment: first,
    other_attachment: {...first, node_id: result.node_id}}), result);
  assert.throws(() => normalizeExploreResultAttachment({attachment: first,
    other_attachment: attachment}), /sources conflict/);
  for (const bad of [null, "", " ", "x".repeat(97), "../node"]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: {...first, node_id: bad}}), /omit node_id/);
  }
  for (const field of ["question", "applicability", "input_revision", "status"]) {
    const incomplete: Record<string, unknown> = {...first};
    delete incomplete[field];
    assert.throws(() => normalizeExploreResultAttachment({attachment: incomplete}));
  }
});

test("neither a conflicting nor malformed secondary source can be silently preferred", () => {
  for (const other of [
    {...attachment, applicability: "Uniform tail bound"},
    {...attachment, interpretation: "Transfer the bound unconditionally."},
    {...attachment, evidence_refs: ["validation:other"]},
  ]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment, other_attachment: other}), /sources conflict/);
  }
  for (const other of [null, [], {...attachment, evidence_refs: []}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment, other_attachment: other}));
  }
  assert.throws(() => normalizeExploreResultAttachment({attachment: null}));
});

const delta = {
  schema_version: "goal_path_delta_v0", outcome: "continue",
  prior_assumption: "A finite prefix might bound the tail.",
  observed_reality: attachment.observation,
  retained: ["Require a uniform estimate."], stopped: ["Transfer from a finite prefix."],
  evidence_refs: attachment.evidence_refs,
};
const scope = {
  schema_version: "explore_result_from_path_delta_v0", node_id: attachment.node_id,
  question: attachment.question, applicability: attachment.applicability,
  input_revision: attachment.input_revision, status: "tentative",
};
const resolved = {...attachment, status: "tentative",
  interpretation: 'Outcome: continue\nretained:\n- Require a uniform estimate.\nstopped:\n- Transfer from a finite prefix.'};

test("explicit scoped capture reuses the same typed path delta without inferring finding status", () => {
  const vision = {path_delta: delta};
  const before = structuredClone({scope, vision});
  assert.deepEqual(normalizeExploreResultAttachment({attachment: scope, vision_packet: vision}), resolved);
  assert.deepEqual({scope, vision}, before);
  // Stopping a route does not turn tentative evidence into a refutation.
  assert.equal(normalizeExploreResultAttachment({attachment: scope,
    vision_packet: {path_delta: {...delta, outcome: "stop"}}}).status, "tentative");
});

test("a first path capture and full capture derive the same scoped question identity", () => {
  const {node_id: _id, ...newScope} = scope;
  const {node_id: _other, ...full} = resolved;
  assert.deepEqual(normalizeExploreResultAttachment({attachment: newScope,
    vision_packet: {path_delta: delta}, other_attachment: full}),
  normalizeExploreResultAttachment({attachment: full}));
});

test("full and path-delta sources share canonical conflict and coalescing rules", () => {
  assert.deepEqual(normalizeExploreResultAttachment({attachment: scope, vision_packet: {path_delta: delta},
    other_attachment: resolved}), resolved);
  assert.throws(() => normalizeExploreResultAttachment({attachment: scope, vision_packet: {path_delta: delta},
    other_attachment: {...resolved, status: "refuted"}}), /sources conflict/);
});

test("scoped path capture cannot fall back to a missing, misplaced or invalid delta", () => {
  for (const vision of [null, {}, {vision_patch: {path_delta: delta}},
    {path_delta: {...delta, outcome: "invented"}},
    {path_delta: {...delta, retained: [], stopped: []}},
    {path_delta: {...delta, observed_reality: "/Users/private/results.log"}},
    {path_delta: {...delta, evidence_refs: []}},
  ]) assert.throws(() => normalizeExploreResultAttachment({attachment: scope, vision_packet: vision}));
  assert.throws(() => normalizeExploreResultAttachment({attachment: {...scope, observation: "Override"},
    vision_packet: {path_delta: delta}}), /unknown fields/);
});

test("same-packet ref selection excludes local pointers without inventing evidence", () => {
  const vision = {path_delta: {...delta, evidence_refs: [...delta.evidence_refs, ".loopx/evidence/probe.json"]}};
  assert.throws(() => normalizeExploreResultAttachment({attachment: scope, vision_packet: vision}), /opaque identifier/);
  assert.deepEqual(normalizeExploreResultAttachment({attachment: {...scope, evidence_refs: attachment.evidence_refs},
    vision_packet: vision, other_attachment: resolved}), resolved);
  for (const refs of [null, [], ["validation:foreign"], [".loopx/evidence/probe.json"]]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: {...scope, evidence_refs: refs}, vision_packet: vision}));
  }
});

test("legal wide path deltas preserve every route condition without JSON expansion", () => {
  const retained = ["a".repeat(120), "b".repeat(120), '"'.repeat(120)];
  const changed = ["c".repeat(120), "d".repeat(120), "e".repeat(120)];
  const stopped = ["f".repeat(120), "g".repeat(120), "Do not transfer without a uniform tail bound.".padStart(120, "h")];
  const observed = "o".repeat(320);
  const result = normalizeExploreResultAttachment({attachment: scope, vision_packet: {
    path_delta: {...delta, observed_reality: observed, retained, changed, stopped}}});
  assert.equal(result.observation, observed);
  for (const item of [...retained, ...changed, ...stopped]) assert.ok((result.interpretation as string).includes(item));
  assert.ok((result.interpretation as string).length <= 1200);
  assert.equal(result.status, "tentative");
});

test("reused text respects the owning Goal and finding limits", () => {
  assert.throws(() => normalizeExploreResultAttachment({attachment: scope,
    vision_packet: {path_delta: {...delta, observed_reality: "x".repeat(321)}}}), /observed_reality/);
  assert.throws(() => normalizeExploreResultAttachment({attachment: scope,
    vision_packet: {path_delta: {...delta, retained: ["x".repeat(121)]}}}), /retained/);
  assert.throws(() => normalizeExploreResultAttachment({attachment: {...attachment,
    interpretation: "x".repeat(1201)}}), /interpretation/);
  assert.throws(() => normalizeExploreResultAttachment({attachment: {...scope, applicability: ""},
    vision_packet: {path_delta: delta}}), /applicability/);
});

const linkedScope = {
  requested_node_refs: [attachment.node_id],
  nodes: [{node_id: attachment.node_id, node_kind: "question",
    title: attachment.question, summary: attachment.applicability}],
};
const linkedCapture = {
  schema_version: scope.schema_version, node_id: scope.node_id,
  input_revision: scope.input_revision, status: scope.status,
};

test("an explicit linked question reuses its entire scope without inferring revision or status", () => {
  assert.deepEqual(normalizeExploreResultAttachment({attachment: linkedCapture,
    vision_packet: {path_delta: delta}, linked_scope: linkedScope,
    other_attachment: resolved}), resolved);
  for (const field of ["input_revision", "status"]) {
    const incomplete = {...linkedCapture} as Record<string, unknown>;
    delete incomplete[field];
    assert.throws(() => normalizeExploreResultAttachment({attachment: incomplete,
      vision_packet: {path_delta: delta}, linked_scope: linkedScope}), new RegExp(field === "status" ? "finding status" : field));
  }
});

test("scope reuse rejects unknown, unlinked, non-question and incomplete scopes", () => {
  for (const linked_scope of [undefined, {...linkedScope, requested_node_refs: []},
    {...linkedScope, nodes: []}, {...linkedScope, nodes: [{...linkedScope.nodes[0], node_kind: "experiment"}]},
    {...linkedScope, nodes: [{...linkedScope.nodes[0], summary: ""}]}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: linkedCapture,
      vision_packet: {path_delta: delta}, linked_scope}));
  }
  for (const partial of [{...linkedCapture, question: attachment.question},
    {...linkedCapture, applicability: attachment.applicability}]) {
    assert.throws(() => normalizeExploreResultAttachment({attachment: partial,
      vision_packet: {path_delta: delta}, linked_scope: linkedScope}), /question and applicability/);
  }
  // Explicit blank or conflicting scope never silently substitutes a stored one.
  assert.throws(() => normalizeExploreResultAttachment({attachment: {...scope, question: ""},
    vision_packet: {path_delta: delta}, linked_scope: linkedScope}), /question/);
  assert.deepEqual(normalizeExploreResultAttachment({attachment: scope,
    vision_packet: {path_delta: delta}}), resolved);
});
