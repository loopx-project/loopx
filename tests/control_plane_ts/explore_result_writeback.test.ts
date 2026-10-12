import assert from "node:assert/strict";
import test from "node:test";
import {normalizeExploreResultAttachment} from "../../loopx/control_plane/capabilities/explore_result_writeback.ts";

const attachment = {
  schema_version: "explore_result_attachment_v0", node_id: "tail-bound",
  question: "Does a finite prefix establish a tail bound?",
  applicability: "Finite prefix only", input_revision: "fixture-v1",
  observation: "A divergent tail shares the prefix.",
  interpretation: "Require a uniform estimate.", status: "refuted",
  evidence_refs: ["validation:prefix", "validation:tail"],
};

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
