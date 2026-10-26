import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {compactTodoContextSource, prepareContextDelivery, restoreContextDelivery} from "../../loopx/control_plane/quota/context_delivery.ts";

import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";

test("replacements and sparse edits own their values without mutating either capture", () => {
  const original = {registry: {body: "Retain every original requirement. ".repeat(400), changed: {value: 1}},
    current_control: {labels: ["original"]}};
  const current = {...structuredClone(original), current_control: {labels: ["current"]}};
  current.registry.changed.value = 2;
  const before = structuredClone(original), after = structuredClone(current);
  const initial = prepareContextDelivery({context: original, scope, capture_id: "original", surface: "quota_packet"});
  const args = {context: current, scope, capture_id: "current", surface: "quota_packet"};
  const delta = prepareContextDelivery({...args, baseline: initial, retained: {complete: true, target: initial.target}});
  assert.equal(delta.mode, "delta");
  assert.deepEqual(inline(delta, ["current_control"]), {labels: ["current"]});
  const restored = restoreContextDelivery({delivery: delta, baseline: initial, current: prepareContextDelivery(args)});
  assert.deepEqual(restored.context, after);
  // Publication must detach both a forced whole value and a sparse replacement.
  (inline(delta, ["current_control"]) as {labels: string[]}).labels.push("wire-only");
  (restored.context as typeof current).registry.changed.value = 3;
  assert.deepEqual(original, before);
  assert.deepEqual(initial.context, before);
  assert.deepEqual(current, after);
});

const scope = {goal_id: "goal", agent_id: "agent", session_id: "session", context_epoch: "epoch-1"};
const long = "Keep the original acceptance and stop before publication. ".repeat(400);
function context(): JsonObject {
  return {complete: true, instruction: "Read requirements before work.", sources: [
    {source: "goal_state", command: "read-goal", content: {state_file: "goal.md", revision: "revision-a",
      excluded_todo_regions: [{role: "agent", start_line: 4, end_line: 6, revision: "tasks-a"}], text: long}},
    {source: "selected_todo", command: "read-task", content: {ok: true, matched: true,
      authority_read: {provider_revision: "a"}, todo: {todo_id: "todo_work", text: long,
        note: "Keep the validation evidence.", status: "open", claimed_by: "agent"}}},
    {source: "goal_acceptance", content: {provider_revision: "a", goal_acceptance_contract: {
      outcome_requirements: {revision: 1, outcomes: [{id: "outcome-a", objective: long,
        requirements: [{id: "r1", description: long, status: "unproven", agent_review: {note: "No evidence yet."}}]}]}}}},
    {kind: "unknown_source", content: {large_mixed_control: long}},
  ]};
}
const source = (value: JsonObject, index: number): JsonObject => (value.sources as JsonObject[])[index].content as JsonObject;
const full = (value: JsonObject, capture_id = "capture-1", extraScope = scope): JsonObject =>
  prepareContextDelivery({context: value, scope: extraScope, capture_id});
function warm(value: JsonObject, baseline: JsonObject): JsonObject {
  return prepareContextDelivery({context: value, scope, capture_id: "capture-2", baseline,
    retained: {complete: true, target: baseline.target}});
}
const recover = (delivery: JsonObject, baseline: JsonObject, value: JsonObject): JsonObject =>
  restoreContextDelivery({delivery, baseline, current: full(value, "capture-2")});

// Read an inline value from the wire, without borrowing any baseline content.
// Tests use this independent walker to verify controls really reach each input.
function inline(delivery: JsonObject, path: (string | number)[]): unknown {
  let node = delivery.changes as JsonObject;
  for (let i = 0; ; i++) {
    if (!node) return undefined;
    if (Object.hasOwn(node, "value")) {
      let value = node.value;
      for (const key of path.slice(i)) value = (value as JsonObject)?.[key];
      return value;
    }
    if (i === path.length) return undefined;
    if (Object.hasOwn((node.values ?? {}) as JsonObject, path[i])) {
      let value = (node.values as Record<string | number, unknown>)[path[i]];
      for (const key of path.slice(i + 1)) value = (value as JsonObject)?.[key];
      return value;
    }
    node = ((node.changed ?? node.items) as Record<string | number, JsonObject>)?.[path[i]];
  }
}

test("full first; unsupported or uncertain retention never becomes an Agent gate", () => {
  const original = context(), saved = structuredClone(original), baseline = full(original);
  assert.equal(baseline.mode, "full");
  assert.deepEqual(baseline.context, original);
  for (const retained of [undefined, {complete: false, target: baseline.target}, {complete: true, target: {}}]) {
    const result = prepareContextDelivery({context: original, scope, capture_id: "capture-2", baseline, retained});
    assert.equal(result.mode, "full");
    assert.deepEqual(result.context, original);
  }
  assert.deepEqual(original, saved);
});

test("fresh control with exact warm bodies; changed source revisions do not resend requirements", () => {
  const original = context(), baseline = full(original), current = structuredClone(original);
  source(current, 0).revision = "revision-b";
  source(current, 1).authority_read = {provider_revision: "b"};
  (source(current, 1).todo as JsonObject).claimed_by = "different-agent";
  const requirements = (((source(current, 2).goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).outcomes as JsonObject[])[0].requirements as JsonObject[];
  requirements[0].status = "failed";
  requirements[0].agent_review = {note: "New failure invalidates the earlier plan."};
  const result = warm(current, baseline);
  assert.equal(result.mode, "delta");
  assert.equal(inline(result, ["sources", 0, "content", "revision"]), "revision-b");
  assert.equal(inline(result, ["sources", 1, "content", "todo", "claimed_by"]), "different-agent");
  assert.equal(inline(result, ["sources", 3, "content", "large_mixed_control"]), long);
  assert.deepEqual(recover(result, baseline, current).context, current);
  assert.ok(JSON.stringify(result).length < JSON.stringify(full(current)).length / 2);
  assert.deepEqual(baseline.context, original);
});

test("relocated reusable strings reference the exact base while current controls stay inline", () => {
  const original = {registry: {authoring: long}, current_control: {instruction: long}};
  const current = {registry: {attachment: {guidance: long}}, current_control: {instruction: long}};
  const initial = prepareContextDelivery({context: original, scope, capture_id: "before", surface: "quota_packet"});
  const args = {context: current, scope, capture_id: "after", surface: "quota_packet"};
  const delta = prepareContextDelivery({...args, baseline: initial, retained: {complete: true, target: initial.target}});
  assert.equal(delta.mode, "delta");
  const tree = delta.changes as {changed: {registry: {changed: {attachment: {changed: {guidance: unknown}}}}}};
  assert.deepEqual(tree.changed.registry.changed.attachment.changed.guidance, {from: ["registry", "authoring"]});
  assert.deepEqual(inline(delta, ["current_control"]), current.current_control);
  assert.deepEqual(restoreContextDelivery({delivery: delta, baseline: initial,
    current: prepareContextDelivery(args)}).context, current);
  assert.deepEqual(initial.context, original);
  assert.deepEqual(prepareContextDelivery(args).context, current); // cold delivery remains literal

  // References never read the patch being applied, inherited properties, or a
  // different string. Any damaged reference recovers this exact current capture.
  for (const reference of [["registry", "missing"], ["constructor", "name"], [], ["current_control"], ["registry", -1], "registry.authoring"]) {
    const damaged = structuredClone(delta);
    const node = (damaged.changes as typeof tree).changed.registry.changed.attachment.changed;
    node.guidance = {from: reference};
    const restored = restoreContextDelivery({delivery: damaged, baseline: initial, current: prepareContextDelivery(args)});
    assert.equal(restored.mode, "full");
    assert.deepEqual(restored.context, current);
  }
});

test("new reusable arrays can relocate exact strings without copying current edits", () => {
  const original = {registry: {text: long, other: long + " old"}};
  const current = {registry: {items: [long + " old", long, long + " new"]}};
  const initial = prepareContextDelivery({context: original, scope, capture_id: "before", surface: "quota_packet"});
  const args = {context: current, scope, capture_id: "after", surface: "quota_packet"};
  const delta = prepareContextDelivery({...args, baseline: initial, retained: {complete: true, target: initial.target}});
  assert.equal(delta.mode, "delta");
  assert.deepEqual(restoreContextDelivery({delivery: delta, baseline: initial,
    current: prepareContextDelivery(args)}).context, current);
  const wire = JSON.stringify(delta.changes);
  assert.match(wire, /"from"/);
  assert.match(wire, / new/); // changed requirements are literal, never fuzzy matches
  assert.ok(wire.length < JSON.stringify(current).length / 2);
});

test("exact-base source and requirement reorders preserve current positions", () => {
  const initial = context();
  const outcomes = ((source(initial, 2).goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).outcomes as JsonObject[];
  (outcomes[0].requirements as JsonObject[]).push({id: "r2", description: "Keep a distinct requirement", status: "unproven"});
  const baseline = full(initial), current = structuredClone(initial);
  const currentOutcomes = ((source(current, 2).goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).outcomes as JsonObject[];
  (currentOutcomes[0].requirements as JsonObject[]).reverse();
  (current.sources as JsonObject[]).reverse();
  const delta = warm(current, baseline);
  if (delta.mode === "full") {
    assert.equal(delta.reason, "delta_not_smaller");
    assert.deepEqual(delta.context, current);
  } else assert.deepEqual(recover(delta, baseline, current).context, current);
});

test("late constraints, null, empty and removed fields remain distinct", () => {
  for (const value of ["Late constraint: never delete the original source.", "", null, undefined]) {
    const initial = context(), baseline = full(initial), current = structuredClone(initial);
    const task = source(current, 1).todo as JsonObject;
    if (value === undefined) delete task.note;
    else task.note = value;
    const delta = warm(current, baseline);
    assert.equal(delta.mode, "delta");

    const restored = recover(delta, baseline, current);
    assert.equal(restored.mode, "delta");
    assert.deepEqual(restored.context, current);
  }
});

test("unexpected structured values in text-only adapters stay full", () => {
  const initial = context(), baseline = full(initial);
  for (const note of [{stop: true}, ["A", "B"], 42]) {
    const current = structuredClone(initial);
    (source(current, 1).todo as JsonObject).note = note;
    const result = warm(current, baseline);
    assert.equal(result.mode, "full");
    assert.deepEqual(result.context, current);
  }
});

test("scope/epoch/version, corruption, loss and ancestry automatically select full", () => {
  const initial = context(), baseline = full(initial);
  for (const key of ["goal_id", "agent_id", "session_id", "context_epoch", "projection_version"]) {
    const wrong = structuredClone(baseline); (wrong.target as JsonObject)[key] = "other";
    assert.equal(warm(initial, wrong).mode, "full", key);
  }
  const corrupt = structuredClone(baseline); (corrupt.context as JsonObject).instruction = "Changed after ACK";
  assert.equal(warm(initial, corrupt).mode, "full");
  assert.equal(warm(initial, {...baseline, ancestry: 32}).mode, "full");
  const tiny = {complete: true, sources: []};
  assert.equal(warm(tiny, full(tiny)).mode, "full");
  const failed = {...initial, complete: false};
  assert.equal(warm(failed, baseline).mode, "full");
  const duplicate = structuredClone(initial);
  (duplicate.sources as JsonObject[]).push(structuredClone((initial.sources as JsonObject[])[0]));
  assert.equal(warm(duplicate, baseline).mode, "full");
});

test("final assembly race, truncated or out-of-order delta uses same-capture full", () => {
  const initial = context(), baseline = full(initial), current = structuredClone(initial);
  source(current, 0).text = long + "NEW STOP CONDITION";
  const delta = warm(current, baseline), capture = full(current, "capture-2");
  const corruptions = [
    {...delta, changes: null}, {...delta, changes: {removed: ["sources"]}}, {...delta, ancestry: 20},
    {...delta, changes: {items: {"-1": {value: "malformed"}}, length: -1}},
    {...delta, base: {...baseline.target as JsonObject, capture_id: "lost"}},
    {...delta, changes: {value: {complete: true}}},
  ];
  for (const delivery of corruptions) {
    const restored = restoreContextDelivery({delivery, baseline, current: capture});
    assert.equal(restored.mode, "full");
    assert.deepEqual(restored.context, current);
  }
  // Epoch moves after preparation but before the final model request.
  const resetCapture = full(current, "capture-2", {...scope, context_epoch: "epoch-2"});
  const reset = restoreContextDelivery({delivery: delta, baseline, current: resetCapture});
  assert.equal(reset.mode, "full");
  assert.deepEqual(reset.context, current);
  // Missing acknowledgement leaves the previous baseline in place: a later
  // delta referring to another capture cannot advance it retroactively.
  const retained = {...delta, context: current};
  const next = warm(current, retained);
  assert.equal(recover(next, baseline, current).mode, "full");
});

test("cold-source title deduplication retains full body, distinct title and original capture", () => {
  const original = {todo: {text: long, title: long, note: "Keep this note", status: "open"}};
  const result = compactTodoContextSource(original);
  assert.deepEqual(result, {todo: {text: long, note: "Keep this note", status: "open"}});
  assert.equal(original.todo.title, long);
  const distinct = {todo: {...original.todo, title: "A meaningful short title"}};
  assert.strictEqual(compactTodoContextSource(distinct), distinct);
});

function evidenceContext(): JsonObject {
  return {complete: true, sources: [{source: "turn_start_capability_hook", kind: "explore_turn_context",
    hook_id: "explore.turn_context", capability_id: "explore", content: {
      ok: true, goal_id: "goal", agent_id: "agent", graph_enabled: true, harness_enabled: true,
      graph: {writeback_results: [{finding_id: "f1", status: "confirmed", summary: long,
        evidence_refs: ["artifact:source"], execution_observation: {scope: "candidate-a"}}],
        measurement_feedback: [{metric: "cost", value: 3, unit: "tokens", input_revision: "input-a"}],
        result_page: {revision: "r1", remaining: 0}, guidance: long,
        unknown_future_gate: {allowed: true}},
      harness: {orchestration_gate: {allowed: true}, selected_branches: [{todo_id: "task-a", status: "open"}],
        guidance: long}, boundary: {starts_agents: false}}}]};
}

test("registered structured evidence is reusable; live gates and unknown fields stay fresh", () => {
  const initial = evidenceContext(), baseline = full(initial), current = structuredClone(initial);
  const graph = source(current, 0).graph as JsonObject, harness = source(current, 0).harness as JsonObject;
  graph.result_page = {revision: "r2", remaining: 1, next_command: "read-next-page"};
  graph.unknown_future_gate = {allowed: false};
  harness.orchestration_gate = {allowed: false, reason: "stop"};
  harness.selected_branches = [];
  const delta = warm(current, baseline);
  assert.equal(delta.mode, "delta");
  assert.equal(inline(delta, ["sources", 0, "content", "graph", "writeback_results"]), undefined);
  assert.equal(inline(delta, ["sources", 0, "content", "graph", "result_page", "revision"]), "r2");
  assert.equal(inline(delta, ["sources", 0, "content", "graph", "unknown_future_gate", "allowed"]), false);
  assert.equal(inline(delta, ["sources", 0, "content", "harness", "orchestration_gate", "allowed"]), false);
  assert.deepEqual(inline(delta, ["sources", 0, "content", "harness", "selected_branches"]), []);
  assert.deepEqual(recover(delta, baseline, current).context, current);
});

test("changed evidence retains exact provenance, null and late refutation", () => {
  const initial = evidenceContext(), baseline = full(initial);
  for (const evidence of [[], null, [{finding_id: "f1", status: "refuted", summary: "A new counterexample.",
    evidence_refs: ["artifact:new-source"], execution_observation: {scope: "candidate-b"}}], undefined]) {
    const current = structuredClone(initial), graph = source(current, 0).graph as JsonObject;
    if (evidence === undefined) delete graph.writeback_results;
    else graph.writeback_results = evidence;
    const delta = warm(current, baseline);
    assert.equal(delta.mode, "delta");

    assert.deepEqual(recover(delta, baseline, current).context, current);
  }
});

test("unknown/failed hook and ambiguous scope never borrow a registered hook's evidence", () => {
  const initial = evidenceContext(), baseline = full(initial);
  for (const field of ["hook_id", "capability_id", "kind"]) {
    const current = structuredClone(initial);
    (current.sources as JsonObject[])[0][field] = "unknown";
    assert.equal(warm(current, baseline).mode, "full");
  }
  const failed = structuredClone(initial); source(failed, 0).ok = false;
  assert.equal(warm(failed, baseline).mode, "full");
  const duplicate = structuredClone(initial);
  (duplicate.sources as JsonObject[]).push(structuredClone((initial.sources as JsonObject[])[0]));
  assert.equal(warm(duplicate, baseline).mode, "full");
  const different = structuredClone(initial); source(different, 0).todo_id = "different-task";
  const delta = warm(different, baseline);
  assert.equal(inline(delta, ["sources", 0, "content", "todo_id"]), "different-task");
  assert.deepEqual(recover(delta, baseline, different).context, different);
});

function packet(): JsonObject {
  return {goal_id: "goal", agent_id: "agent", should_run: true, selected_todo: {todo_id: "todo_work", claimed_by: "agent"},
    goal_boundary: {write_scope: ["src/**"], approval: "pending", stop: "publication"},
    unknown_future_control: {allowed: false}, handoff_readiness: {ready: false},
    automation_liveness: {pause_allowed: false}, autonomous_replan_scope: {owner_agent_ids: ["agent"]}, planning_horizon: {work_items: [{id: "one", body: long}, {id: "two", body: long}]},
    interaction_contract: {mode: "bounded_delivery", user_channel: {notify: "quiet"},
      agent_channel: {delivery_allowed: true, must_attempt: true, work_context: context()},
      cli_channel: {spend_policy: "after_validation", settlement_plan: {identity: {turn: "turn-a"}, ordered_steps: [
        {kind: "validation", owner: "agent", precondition: long, command_template: "validate-a", command_condition: "todo_complete"},
        {kind: "writeback", owner: "agent", precondition: long, command_template: "writeback-a", optional_attachments: [{capability_id: "explore", guidance: long, required: false}]},
      ]}}}};
}
const packetFull = (value: JsonObject, capture_id = "capture-a", surface = "quota_packet") =>
  prepareContextDelivery({context: value, scope, capture_id, surface});
function packetWarm(value: JsonObject, baseline: JsonObject, surface = "quota_packet") {
  return prepareContextDelivery({context: value, scope, capture_id: "capture-b", surface, baseline,
    retained: {complete: true, target: baseline.target}});
}

test("grouped literals carry current controls without interpreting object data as patch operations", () => {
  const value = {registry: {body: long}, first: null, second: {value: 1, changed: {stop: true}},
    values: {items: [false, 0, ""], removed: ["literal-data"]}, last: "current"};
  const baseline = packetFull(value), delta = packetWarm(value, baseline);
  assert.equal(delta.mode, "delta");
  const literals = (delta.changes as JsonObject).values as JsonObject;
  assert.ok(Object.hasOwn(literals, "first"));
  assert.deepEqual(literals.values, value.values);
  assert.deepEqual(inline(delta, ["second"]), value.second);
  const capture = packetFull(value, "capture-b");
  assert.deepEqual(restoreContextDelivery({delivery: delta, baseline, current: capture}).context, value);
  const forged = structuredClone(delta);
  delete ((forged.changes as JsonObject).values as JsonObject).first;
  assert.equal(restoreContextDelivery({delivery: forged, baseline, current: capture}).mode, "full");
  literals.second = {value: "wire-only"};
  assert.deepEqual(value.second, {value: 1, changed: {stop: true}});
  assert.deepEqual((baseline.context as typeof value).second, value.second);
});

test("quota and Turn replay original Goal intent without replaying the complete acceptance tree", () => {
  for (const surface of ["quota_packet", "turn_envelope"]) {
    const horizon = {schema_version: "quota_planning_horizon_v0", goal_id: "goal", agent_id: "agent",
      core_goal: {objective: "Deliver the original Goal; stop before publication.", objective_source: "active_state",
        objective_missing: false, read_action: "read-current-goal", acceptance_contract: {requirements: [long]}},
      work_items: [{todo_id: "a", text: long}, {todo_id: "b", text: long}], relations: [],
      unknown_future_constraint: {stop: true}};
    const before: JsonObject = surface === "quota_packet" ? {planning_horizon: horizon}
      : {action: {delivery_allowed: true, planning_horizon: horizon}};
    const path = surface === "quota_packet" ? ["planning_horizon"] : ["action", "planning_horizon"];
    const baseline = packetFull(before, "capture-a", surface);
    const delta = packetWarm(before, baseline, surface);
    assert.equal(delta.mode, "delta");
    for (const [key, value] of Object.entries(horizon.core_goal)) if (key !== "acceptance_contract")
      assert.deepEqual(inline(delta, [...path, "core_goal", key]), value);
    assert.equal(inline(delta, [...path, "core_goal", "acceptance_contract"]), undefined);
    assert.equal(inline(delta, [...path, "work_items"]), undefined);
    assert.deepEqual(inline(delta, [...path, "unknown_future_constraint"]), {stop: true});
    const current = structuredClone(before);
    const next = (surface === "quota_packet" ? current.planning_horizon
      : (current.action as JsonObject).planning_horizon) as typeof horizon;
    next.core_goal.objective = "Keep the corrected original Goal and its new stop.";
    next.core_goal.acceptance_contract.requirements.push("A late cumulative requirement.");
    next.work_items.reverse();
    const changed = packetWarm(current, baseline, surface), capture = packetFull(current, "capture-b", surface);
    assert.deepEqual(inline(changed, [...path, "core_goal", "objective"]), next.core_goal.objective);
    assert.deepEqual(restoreContextDelivery({delivery: changed, baseline, current: capture}).context, current);
    assert.deepEqual(restoreContextDelivery({delivery: changed, current: capture}).context, current);
  }
});

test("Turn capsule reuses complete guidance but repeats current closure and unknown controls", () => {
  const value = {action: {delivery_allowed: true}, contract_capsule: {vision_continuation_audit: {
    required: true, required_before_closeout: [long], authoritative_evidence_kinds: ["source"],
    not_satisfied_by: ["a receipt"], unknown_future_gate: {allowed: false},
    vision_gap_judge: {done: false, decision: "continue", external_research_instruction: long,
      research_writeback_required_when_used: ["source", "finding"]}}}};
  const baseline = packetFull(value, "capture-a", "turn_envelope");
  const delta = packetWarm(value, baseline, "turn_envelope");
  assert.equal(delta.mode, "delta");
  const prefix = ["contract_capsule", "vision_continuation_audit"];
  assert.equal(inline(delta, [...prefix, "required"]), true);
  assert.equal(inline(delta, [...prefix, "vision_gap_judge", "done"]), false);
  assert.deepEqual(inline(delta, [...prefix, "unknown_future_gate"]), {allowed: false});
  assert.equal(inline(delta, [...prefix, "vision_gap_judge", "external_research_instruction"]), undefined);
  const current = structuredClone(value);
  current.contract_capsule.vision_continuation_audit.vision_gap_judge.decision = "stop";
  const changed = packetWarm(current, baseline, "turn_envelope");
  assert.equal(inline(changed, [...prefix, "vision_gap_judge", "decision"]), "stop");
  assert.deepEqual(restoreContextDelivery({delivery: changed, baseline,
    current: packetFull(current, "capture-b", "turn_envelope")}).context, current);
});

test("registered branch evidence can be reused without retaining selection or resume authority", () => {
  for (const surface of ["work_context", "quota_packet", "turn_envelope"]) {
    const work = evidenceContext(), harness = source(work, 0).harness as JsonObject;
    harness.selected_branches = [{todo_id: "a", selection_status: "selected", status: "open", resume_ready: true,
      typed_evidence_audit: {nodes: [{node_id: "node-a", title: long}], findings: [{status: "confirmed", finding: long}]}}];
    harness.link_evidence_template = ["loopx", "todo", "update", "--todo-id", "<todo-id>", "--evidence", long];
    const before: JsonObject = surface === "work_context" ? work
      : surface === "quota_packet" ? {interaction_contract: {agent_channel: {work_context: work}}} : {work_context: work};
    const root = surface === "work_context" ? [] : surface === "quota_packet"
      ? ["interaction_contract", "agent_channel", "work_context"] : ["work_context"];
    const path = [...root, "sources", 0, "content", "harness"];
    const baseline = packetFull(before, "capture-a", surface), delta = packetWarm(before, baseline, surface);
    assert.equal(delta.mode, "delta");
    assert.equal(inline(delta, [...path, "selected_branches", 0, "todo_id"]), "a");
    assert.equal(inline(delta, [...path, "selected_branches", 0, "resume_ready"]), true);
    assert.equal(inline(delta, [...path, "selected_branches", 0, "typed_evidence_audit"]), undefined);
    const current = structuredClone(before);
    let next: unknown = current;
    for (const key of [...path, "selected_branches", 0]) next = (next as Record<string | number, unknown>)[key];
    const branch = next as JsonObject;
    branch.selection_status = "rejected"; branch.resume_ready = false;
    branch.typed_evidence_audit = {findings: [{status: "refuted", finding: "New counterexample."}]};
    const changed = packetWarm(current, baseline, surface), capture = packetFull(current, "capture-b", surface);
    assert.equal(inline(changed, [...path, "selected_branches", 0, "resume_ready"]), false);
    assert.deepEqual(restoreContextDelivery({delivery: changed, baseline, current: capture}).context, current);
    const unchangedCapture = packetFull(before, "capture-b", surface);
    // Reconstructing the same data is insufficient if unchanged live controls
    // were omitted from this input rather than supplied in the current frame.
    assert.equal(restoreContextDelivery({delivery: {...delta, changes: null}, baseline,
      current: unchangedCapture}).mode, "full");
  }
});

test("whole packet reuses complete projections; live controls and unknown fields remain inline", () => {
  const before = packet(), baseline = packetFull(before), current = structuredClone(before);
  const cli = ((current.interaction_contract as JsonObject).cli_channel as JsonObject);
  const plan = cli.settlement_plan as JsonObject;
  plan.identity = {turn: "turn-b"};
  (plan.ordered_steps as JsonObject[])[1].command_template = "writeback-b";
  const delta = packetWarm(current, baseline);
  assert.equal(delta.mode, "delta");
  for (const [path, value] of [
    [["interaction_contract", "cli_channel", "settlement_plan", "ordered_steps", 1, "optional_attachments", 0, "required"], false],
    [["handoff_readiness", "ready"], false], [["automation_liveness", "pause_allowed"], false],
    [["autonomous_replan_scope", "owner_agent_ids"], ["agent"]], [["should_run"], true], [["unknown_future_control", "allowed"], false],
    [["selected_todo", "claimed_by"], "agent"], [["goal_boundary", "approval"], "pending"],
    [["interaction_contract", "agent_channel", "delivery_allowed"], true],
    [["interaction_contract", "cli_channel", "settlement_plan", "identity", "turn"], "turn-b"],
    [["interaction_contract", "cli_channel", "settlement_plan", "ordered_steps", 0, "command_condition"], "todo_complete"],
    [["interaction_contract", "cli_channel", "settlement_plan", "ordered_steps", 1, "command_template"], "writeback-b"],
  ] as [(string | number)[], unknown][]) assert.deepEqual(inline(delta, path), value, JSON.stringify(path));
  const recovered = restoreContextDelivery({delivery: delta, baseline, current: packetFull(current, "capture-b")});
  assert.equal(recovered.mode, "delta"); assert.deepEqual(recovered.context, current);
  assert.ok(JSON.stringify(delta).length < JSON.stringify(current).length / 3);
  assert.equal(inline(delta, ["planning_horizon"]), undefined);
  // A forged wire can reconstruct correctly yet omit an unchanged live gate.
  const broken = structuredClone(delta);
  const changes = broken.changes as JsonObject;
  delete ((changes.values as JsonObject)?.should_run !== undefined
    ? changes.values as JsonObject : changes.changed as JsonObject).should_run;
  assert.equal(restoreContextDelivery({delivery: broken, baseline, current: packetFull(current, "capture-b")}).mode, "full");
});

test("array edits preserve append, removal, reorder, duplicate entries and exact scalar types", () => {
  const before = packet(), baseline = packetFull(before);
  const old = (before.planning_horizon as JsonObject).work_items as JsonObject[];
  const cases: unknown[] = [[], [old[1]], [...old, {id: "three", body: long}], [old[1], old[0]],
    [old[0], old[0]], [null, "", false, 0], null, "", {items: old},
    [{...old[0], late_constraint: "STOP now", revision: "r2"}, old[1]]];
  for (const items of cases) {
    const current = {...before, planning_horizon: {work_items: items}};
    const delta = packetWarm(current, baseline);
    const capture = packetFull(current, "capture-b");
    const restored = delta.mode === "full" ? delta : restoreContextDelivery({delivery: delta, baseline, current: capture});
    assert.deepEqual(restored.context, current);
  }
  assert.deepEqual(before, packet());
});

test("JSON property names are data; prototype keys never become assignment paths", () => {
  const before = packet(), baseline = packetFull(before), current = structuredClone(before);
  current.planning_horizon = JSON.parse('{"__proto__":{"unsafe":true},"constructor":null,"removed":false}');
  const delta = packetWarm(current, baseline);
  const restored = restoreContextDelivery({delivery: delta, baseline, current: packetFull(current, "capture-b")});
  assert.deepEqual(restored.context, current);
  assert.equal(({} as JsonObject).unsafe, undefined);
});

test("quota and Turn use one codec but cannot borrow another surface's baseline", () => {
  const value = packet(), quota = packetFull(value), turn = {schema_version: "loopx_turn_envelope_v0",
    action: {kind: "run", allowed: true}, work_context: context(), writeback: {settlement_plan:
      ((value.interaction_contract as JsonObject).cli_channel as JsonObject).settlement_plan}};
  const baseline = packetFull(turn, "capture-a", "turn_envelope");
  assert.equal(packetWarm(turn, quota, "turn_envelope").mode, "full");
  const delta = packetWarm(turn, baseline, "turn_envelope");
  assert.equal(delta.mode, "delta");
  assert.equal(inline(delta, ["action", "allowed"]), true);
  assert.deepEqual(restoreContextDelivery({delivery: delta, baseline,
    current: packetFull(turn, "capture-b", "turn_envelope")}).context, turn);
});


test("legacy quota fields are bound by the existing adapter, never caller data", () => {
  const value = {...packet(), heartbeat_recommendation: {reason: long}};
  const baseline = evaluateTurnEnvelope({operation: "context_delivery", context: value,
    scope, capture_id: "capture-a", surface: "quota_packet"});
  const request = {context: value, scope, capture_id: "capture-b", surface: "quota_packet", baseline,
    retained: {complete: true, target: baseline.target}, legacyQuotaFields: ["unknown_future_control"]};
  const delta = evaluateTurnEnvelope({...request, operation: "context_delivery"});
  assert.equal(delta.mode, "delta");
  assert.equal(inline(delta, ["heartbeat_recommendation"]), undefined);
  assert.deepEqual(inline(delta, ["unknown_future_control"]), value.unknown_future_control);
  const current = evaluateTurnEnvelope({operation: "context_delivery", context: value,
    scope, capture_id: "capture-b", surface: "quota_packet"});
  const restored = evaluateTurnEnvelope({operation: "context_recovery", current, delivery: delta, baseline});
  assert.equal(restored.mode, "delta");
  assert.deepEqual(restored.context, value);
  // A direct codec call has no legacy binding and conservatively refreshes it.
  assert.deepEqual(inline(prepareContextDelivery(request), ["heartbeat_recommendation"]), value.heartbeat_recommendation);
});
