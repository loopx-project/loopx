import assert from "node:assert/strict";
import test from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {normalizeResearchObservation, projectResearchFrontier, researchCompositionGaps} from "../../loopx/control_plane/capabilities/explore_research.ts";
import {validateResearchExecution, normalizeResearchCompositionPolicy, projectResearchComposition,
  researchCompositionFacts, qualifyResearchCompositionWriteback,
  validateResearchCompositionSuccessor, qualifyResearchCompletion} from "../../loopx/control_plane/capabilities/explore_research_execution.ts";

function fixture(): JsonObject {
  const observation = (node: string, target?: string): JsonObject => ({
    schema_version: "typed_research_observation_v0", explore_node_id: node,
    progress: {schema_version: "typed_progress_observation_v0", work_item_id: `todo_${node}`,
      result_class: "exploration_exhausted", coverage_scope_id: `scope-${node}`,
      coverage_complete: true, evidence_ids: [`ev-${node}`]},
    closure_basis: {schema_version: "research_closure_basis_v0", disposition: "bounded",
      constraints: [{kind: "invariant", id: "boundary", role: "decisive"}], evidence_ids: [`ev-${node}`]},
    composition_candidates: target ? [{target_node_id: target, basis: "explicit",
      interaction_kind: "state_interference", evidence_ids: [`ev-${node}`, `ev-${target}`]}] : [],
  });
  const nodes = ["a", "b"].map(node_id => ({node_id, node_kind: "hypothesis", status: "resolved",
    research_observation: normalizeResearchObservation({observation: observation(node_id, node_id === "a" ? "b" : undefined)})}));
  const params: JsonObject = {goal_id: "fixture", agent_id: "fixture-agent", nodes: [
    ...nodes, {node_id: "joint", node_kind: "experiment", status: "open", agent_id: "fixture-agent"}],
    edges: ["a", "b"].map(to_node => ({from_node: "joint", to_node, edge_type: "depends_on"}))};
  const gap = researchCompositionGaps(params)[0];
  params.observation = {...observation("joint"), input_observations: gap.input_observations,
    progress: {...observation("joint").progress as JsonObject, work_item_id: "todo_joint"},
    execution_lineage: {schema_version: "research_execution_lineage_v0", goal_id: "fixture",
      gap_id: gap.gap_id, replan_obligation_id: "replan-0123456789abcdef", successor_todo_id: "todo_joint", agent_id: "fixture-agent"}};
  params.todo = {todo_id: "todo_joint", replan_obligation_id: "replan-0123456789abcdef",
    claimed_by: "fixture-agent", task_class: "advancement_task", action_kind: "joint_probe",
    explore_result_node_refs: ["joint"], target_key: "joint", actionable_open: true};
  return params;
}

function liveFixture(): JsonObject {
  const params = fixture();
  params.harness = {enabled: true, composition_mode: "explicit_only", composition_scope_id: "scope-joint"};
  params.todo = {...params.todo as JsonObject, status: "open"};
  const facts = researchCompositionFacts(params);
  params.bindings = (facts.gaps as JsonObject[]).map(gap => ({gap_id: gap.gap_id,
    obligation: {obligation_id: "replan-0123456789abcdef"}}));
  params.todos = [];
  const raw = params.observation as JsonObject;
  params.observation = {...raw, progress: {...raw.progress as JsonObject, fingerprint: "progress-joint"}};
  return params;
}

test("live policy is explicit, scoped and default off", () => {
  assert.equal(normalizeResearchCompositionPolicy({harness: {enabled: true}}).enabled, false);
  assert.equal(normalizeResearchCompositionPolicy({harness: {enabled: "true", composition_mode: "explicit_only", composition_scope_id: "scope"}}).enabled, false);
  assert.throws(() => normalizeResearchCompositionPolicy({harness: {enabled: true, composition_mode: "explicit_only"}}), /scope/);
  assert.throws(() => normalizeResearchCompositionPolicy({harness: {enabled: true, composition_mode: "infer"}}), /composition_mode/);
  const params = liveFixture();
  params.harness = {enabled: true};
  assert.equal(projectResearchComposition(params).state, "disabled");
});

test("only the exact runnable experiment successor schedules the live gap", () => {
  const params = liveFixture();
  assert.equal(projectResearchComposition(params).pending_count, 1);
  params.todos = [params.todo];
  const scheduled = projectResearchComposition(params);
  assert.equal(scheduled.scheduled_count, 1);
  assert.equal(scheduled.observed_count, 0);
  for (const patch of [{claimed_by: "another-agent"}, {replan_obligation_id: "replan-fedcba9876543210"},
    {actionable_open: false, status: "deferred"}, {explore_result_node_refs: ["other"]}, {action_kind: "read"}]) {
    assert.equal(projectResearchComposition({...params, todos: [{...params.todo as JsonObject, ...patch}]}).pending_count, 1);
  }
  const pending = projectResearchComposition({...params, todos: []});
  assert.equal(validateResearchCompositionSuccessor({frontier: pending, todo: params.todo, agent_id: params.agent_id}).accepted, true);
  assert.throws(() => validateResearchCompositionSuccessor({frontier: pending,
    todo: {...params.todo as JsonObject, status: "deferred", resume_when: "capacity_available:fixture"}, agent_id: params.agent_id}), /no deferral/);
});

test("live observation needs execution lineage and cannot be replaced by generic progress or a replay", () => {
  const params = liveFixture();
  const pending = projectResearchComposition(params);
  const gap = (pending.lineage_gaps as JsonObject[])[0];
  const guard = {schema_version: "semantic_replan_capability_guard_v0", capability_id: "explore",
    gap_id: gap.gap_id, frontier_revision: gap.frontier_revision};
  const qualify = (frontier: JsonObject, progress: JsonObject, repeated: string[] = []) => qualifyResearchCompositionWriteback({
    frontier, capability_guard: guard, obligation_id: "replan-0123456789abcdef", progress_observation: progress,
    claimed_progress_fingerprints: repeated});
  assert.equal(qualify(pending, {fingerprint: "unrelated", result_class: "advanced"}).accepted, false);
  const nodes = params.nodes as JsonObject[], raw = params.observation as JsonObject;
  const unbound = {...raw}; delete unbound.execution_lineage;
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "resolved",
    research_observation: normalizeResearchObservation({observation: unbound})}];
  params.todos = [params.todo];
  assert.equal(projectResearchComposition(params).observed_count, 0);
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "resolved",
    research_observation: normalizeResearchObservation({observation: raw})}];
  const observed = projectResearchComposition(params), progress = raw.progress as JsonObject;
  assert.equal(observed.observed_count, 1);
  assert.equal(qualify(observed, progress).capability_outcome, "composition_experiment_observed");
  assert.equal(qualify(observed, progress, ["progress-joint"]).accepted, false);
  assert.equal(qualify(observed, {...progress, evidence_ids: []}).accepted, false);
  assert.equal(qualify(observed, {...progress, work_item_id: "todo_other"}).accepted, false);
  params.nodes = [{...nodes[0], status: "open"}, nodes[1], (params.nodes as JsonObject[])[2]];
  const invalidated = projectResearchComposition(params);
  assert.equal(invalidated.ineligible_count, 1);
  assert.equal(qualify(invalidated, progress).accepted, false);
});

test("Todo closeout needs this task's current terminal experiment result", () => {
  const params = liveFixture();
  params.todos = [params.todo];
  const request = (frontier: JsonObject, todo = params.todo) => qualifyResearchCompletion({
    frontier, todo, actor_agent_id: params.agent_id, goal_id: params.goal_id});
  assert.equal(request(projectResearchComposition(params)).allowed, false);
  const nodes = params.nodes as JsonObject[];
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "resolved",
    research_observation: normalizeResearchObservation({observation: params.observation})}];
  const observed = projectResearchComposition(params);
  const accepted = request(observed);
  assert.equal(accepted.allowed, true);
  assert.equal((accepted.evidence as JsonObject).todo_id, "todo_joint");
  assert.equal((accepted.evidence as JsonObject).experiment_node_id, "joint");
  assert.equal(request(observed, {...params.todo as JsonObject, todo_id: "todo_other"}).allowed, false);
  params.nodes = [{...nodes[0], status: "open"}, nodes[1], (params.nodes as JsonObject[])[2]];
  assert.equal(request(projectResearchComposition(params)).allowed, false);
  assert.equal(qualifyResearchCompletion({frontier: {enabled: false}, todo: params.todo}).required, false);
});

test("execution evidence binds Goal, gap, obligation, Todo, actor, experiment and current inputs", () => {
  const params = fixture();
  const canonical = validateResearchExecution(params);
  assert.deepEqual(canonical.execution_lineage, (params.observation as JsonObject).execution_lineage);
  const unbound = {...params.observation as JsonObject};
  delete unbound.execution_lineage;
  assert.notEqual(canonical.fingerprint, normalizeResearchObservation({observation: unbound}).fingerprint);
  // The receipt and cold projection retain diagnostic semantics. No execution,
  // scientific truth or Goal completion is inferred from validation alone.
  assert.equal(projectResearchFrontier(params).mode, "read_only_shadow");
  assert.equal(researchCompositionGaps(params)[0].state, "pending");
});

test("completed archive lineage retains evidence without making archived work runnable", () => {
  const params = liveFixture(), nodes = params.nodes as JsonObject[];
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "resolved",
    research_observation: normalizeResearchObservation({observation: params.observation})}];
  const retained = {...params.todo as JsonObject, status: "done", archive_state: "archive", actionable_open: false};
  for (const todo of [retained, {...retained, claimed_by: null}]) {
    const frontier = projectResearchComposition({...params, todos: [todo]});
    assert.equal(frontier.observed_count, 1);
    assert.equal(frontier.scheduled_count, 0);
    assert.equal((frontier.lineage_gaps as JsonObject[])[0].observed_todo_id, "todo_joint");
  }
  for (const patch of [{status: "open"}, {replan_obligation_id: "replan-fedcba9876543210"},
    {explore_result_node_refs: ["other"]}, {claimed_by: "other-agent"}]) {
    assert.equal(projectResearchComposition({...params, todos: [{...retained, ...patch}]}).observed_count, 0);
  }
  assert.equal(projectResearchComposition({...params, todos: []}).observed_count, 0);
  assert.equal(projectResearchComposition({...params, todos: [retained],
    nodes: [{...nodes[0], status: "open"}, nodes[1], (params.nodes as JsonObject[])[2]]}).observed_count, 0);
  assert.throws(() => validateResearchExecution({...params, todo: retained}), /runnable/);
});

test("evidence-backed candidate dismissal retires work without claiming an experiment outcome", () => {
  const params = liveFixture(), nodes = params.nodes as JsonObject[];
  const raw = params.observation as JsonObject;
  const dismissed = {...raw, progress: {...raw.progress as JsonObject, result_class: "no_followup"},
    closure_basis: {...raw.closure_basis as JsonObject, disposition: "no_followup"},
    composition_resolution: {schema_version: "research_composition_resolution_v0", disposition: "dismissed",
      basis: "outside_scope", evidence_ids: ["ev-joint"]}};
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "dead_end",
    research_observation: normalizeResearchObservation({observation: dismissed})}];
  params.todos = [params.todo];
  const frontier = projectResearchComposition(params), gap = (frontier.lineage_gaps as JsonObject[])[0];
  assert.equal(frontier.dismissed_count, 1);
  assert.equal(frontier.observed_count, 0);
  assert.equal(projectResearchFrontier(params).observed_count, 0);
  const guard = {capability_id: "explore", gap_id: gap.gap_id, frontier_revision: gap.frontier_revision};
  const qualified = qualifyResearchCompositionWriteback({frontier, capability_guard: guard,
    obligation_id: gap.obligation_id, progress_observation: dismissed.progress, claimed_progress_fingerprints: []});
  assert.equal(qualified.capability_outcome, "composition_candidate_dismissed");
  assert.equal(qualifyResearchCompletion({frontier, todo: params.todo, goal_id: params.goal_id,
    actor_agent_id: params.agent_id}).allowed, true);
  for (const patch of [{basis: "not_promising"}, {evidence_ids: []}, {evidence_ids: ["unrelated"]}]) {
    assert.throws(() => normalizeResearchObservation({observation: {...dismissed,
      composition_resolution: {...dismissed.composition_resolution, ...patch}}}));
  }
});

test("temporary deferral needs a fresh exact blocker and the common resume contract", () => {
  const params = liveFixture(), nodes = params.nodes as JsonObject[];
  const raw = params.observation as JsonObject;
  const blocked = {...raw, progress: {...raw.progress as JsonObject, result_class: "blocked",
    coverage_complete: false, blocker_id: "todo_dependency"}, closure_basis: null,
    composition_resolution: {schema_version: "research_composition_resolution_v0", disposition: "deferred",
      evidence_ids: ["ev-joint"]}};
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "blocked",
    research_observation: normalizeResearchObservation({observation: blocked})}];
  const task = {...params.todo as JsonObject, status: "deferred", actionable_open: false,
    resume_when: "todo_done:todo_dependency"};
  const blocker = {todo_id: "todo_dependency", role: "agent", task_class: "blocker", status: "open",
    claimed_by: "fixture-agent", archive_state: "active", unblocks_todo_id: "todo_joint"};
  const deferred = projectResearchComposition({...params, todos: [task, blocker]});
  assert.equal(deferred.deferred_count, 1);
  assert.equal(deferred.observed_count, 0);
  assert.equal(deferred.pending_count, 0);
  assert.equal(qualifyResearchCompletion({frontier: deferred, todo: task}).allowed, false);
  const gap = (deferred.lineage_gaps as JsonObject[])[0];
  const request = {frontier: deferred, capability_guard: {capability_id: "explore", gap_id: gap.gap_id,
    frontier_revision: gap.frontier_revision}, obligation_id: gap.obligation_id,
    progress_observation: blocked.progress, claimed_progress_fingerprints: [], claimed_blocker_ids: []};
  assert.equal(qualifyResearchCompositionWriteback(request).capability_outcome, "composition_temporarily_deferred");
  assert.equal(qualifyResearchCompositionWriteback({...request, claimed_blocker_ids: ["todo_dependency"]}).accepted, false);
  for (const patch of [{status: "done"}, {status: "blocked"}, {claimed_by: "other-agent"}, {task_class: "continuous_monitor"},
    {unblocks_todo_id: "todo_other"}, {archive_state: "archive"}]) {
    assert.equal(projectResearchComposition({...params, todos: [task, {...blocker, ...patch}]}).deferred_count, 0);
  }
  for (const resume_when of ["capacity_available:fixture", "todo_done:todo_unknown", null]) {
    assert.equal(projectResearchComposition({...params, todos: [{...task, resume_when}, blocker]}).deferred_count, 0);
  }
  // Resolving the prerequisite exposes the same open gap; it grants no lease
  // and cannot turn the deferred declaration into an experimental outcome.
  const resumed = projectResearchComposition({...params, todos: [task, {...blocker, status: "done"}]});
  assert.equal(resumed.pending_count, 1);
  assert.equal(resumed.observed_count, 0);
});

test("a changed evidence duty needs explicit retirement facts and never closes live work", () => {
  const params = liveFixture(), initial = projectResearchComposition(params);
  const selected = (initial.lineage_gaps as JsonObject[])[0];
  const guard = {schema_version: "semantic_replan_capability_guard_v0", capability_id: "explore",
    gap_id: selected.gap_id, frontier_revision: selected.frontier_revision};
  const nodes = params.nodes as JsonObject[];
  const current = projectResearchComposition({...params, nodes: [{...nodes[0], status: "open"}, ...nodes.slice(1)]});
  const request = {frontier: current, capability_guard: guard, obligation_id: selected.obligation_id};
  const rejected = qualifyResearchCompositionWriteback(request);
  assert.equal(rejected.accepted, false);
  const contract = rejected.retirement_contract as JsonObject;
  assert.equal(contract.disposition, "invalidated");
  const accepted = qualifyResearchCompositionWriteback({...request,
    progress_observation: {...contract.progress_observation as JsonObject, fingerprint: "retirement-proof"}});
  assert.equal(accepted.capability_outcome, "composition_duty_invalidated");
  assert.deepEqual(accepted.outcomes, ["capability_duty_retired"]);
  assert.equal(qualifyResearchCompositionWriteback({...request, frontier: initial,
    progress_observation: contract.progress_observation}).accepted, false);
  for (const patch of [{work_item_id: "replan-fedcba9876543210"}, {blocker_id: "unrelated"},
    {result_class: "advanced"}, {evidence_ids: ["unrelated"]}]) {
    assert.equal(qualifyResearchCompositionWriteback({...request,
      progress_observation: {...contract.progress_observation as JsonObject, ...patch}}).accepted, false);
  }
  const blocked = projectResearchComposition({...params, nodes: [{...nodes[0], status: "open"}, ...nodes.slice(1)], todos: [params.todo]});
  assert.equal(qualifyResearchCompositionWriteback({...request, frontier: blocked,
    progress_observation: contract.progress_observation}).accepted, false);
  assert.equal(qualifyResearchCompositionWriteback({...request, frontier: {schema_version: "unavailable"},
    progress_observation: contract.progress_observation}).retirement_contract, undefined);
});

test("rejected, deferred, wrong-agent and unrelated successors cannot attribute an execution result", () => {
  for (const patch of [
    {actionable_open: false}, {claimed_by: "another-agent"}, {task_class: "continuous_monitor"},
    {action_kind: "inspect"}, {todo_id: "todo_other"}, {replan_obligation_id: "replan-fedcba9876543210"},
    {explore_result_node_refs: ["other"]}, {explore_result_node_refs: ["joint", "other"]},
    {target_key: "other"}, {archive_state: "archive"}, {excluded_agents: ["fixture-agent"]},
  ]) {
    const params = fixture();
    params.todo = {...params.todo as JsonObject, ...patch};
    assert.throws(() => validateResearchExecution(params), /research execution/);
  }
  for (const patch of [{agent_id: "another-agent"}, {goal_id: "other-goal"}, {gap_id: "research-composition-0000000000000000"}]) {
    const params = fixture(), raw = params.observation as JsonObject;
    params.observation = {...raw, execution_lineage: {...raw.execution_lineage as JsonObject, ...patch}};
    assert.throws(() => validateResearchExecution(params), /research execution/);
  }
});

test("stale fingerprints, reads, malformed lineage and already-observed inputs fail closed", () => {
  const params = fixture(), raw = params.observation as JsonObject;
  assert.throws(() => validateResearchExecution({...params, observation: {...raw,
    input_observations: [{node_id: "a", fingerprint: "stale"}, ...(raw.input_observations as JsonObject[]).slice(1)]}}), /exact current/);
  for (const progress of [{...raw.progress as JsonObject, result_class: "unchanged"},
    {...raw.progress as JsonObject, result_class: "advanced", evidence_ids: []}]) {
    assert.throws(() => validateResearchExecution({...params, observation: {...raw, progress, closure_basis: null}}), /read or ACK/);
  }
  assert.throws(() => normalizeResearchObservation({observation: {...raw,
    execution_lineage: {...raw.execution_lineage as JsonObject, successor_todo_id: "todo_other"}}}), /exact gap/);
  const nodes = params.nodes as JsonObject[];
  params.nodes = [...nodes.slice(0, 2), {...nodes[2], status: "resolved", research_observation: normalizeResearchObservation({observation: raw})}];
  assert.equal(researchCompositionGaps(params)[0].state, "observed");
  assert.throws(() => validateResearchExecution(params), /pending gap/);
});

test("cold terminal diagnostics cannot discharge or block a live execution duty", () => {
  const params = liveFixture(), nodes = params.nodes as JsonObject[], raw = params.observation as JsonObject;
  const diagnostic: JsonObject = {...raw, explore_node_id: "diagnostic",
    progress: {...raw.progress as JsonObject, work_item_id: "todo_diagnostic"}};
  delete diagnostic.execution_lineage;
  params.nodes = [...nodes, {node_id: "diagnostic", node_kind: "experiment", status: "resolved",
    research_observation: normalizeResearchObservation({observation: diagnostic})}];
  params.edges = [...params.edges as JsonObject[], ...["a", "b"].map(to_node =>
    ({from_node: "diagnostic", to_node, edge_type: "depends_on"}))];
  params.todos = [params.todo];
  assert.equal(researchCompositionGaps(params)[0].state, "observed");
  const scheduled = projectResearchComposition(params);
  assert.equal(scheduled.scheduled_count, 1);
  assert.equal(scheduled.observed_count, 0);
  assert.doesNotThrow(() => validateResearchExecution({...params, frontier: scheduled}));
  assert.throws(() => validateResearchExecution({...params, frontier: {...scheduled, agent_id: "another-agent"}}), /Goal and actor/);
  assert.throws(() => validateResearchExecution({...params, frontier: {...scheduled,
    policy: {...scheduled.policy as JsonObject, coverage_scope_id: "other-scope"}}}), /pending gap/);
  params.nodes = [...(params.nodes as JsonObject[]).filter(node => node.node_id !== "joint"),
    {...nodes[2], status: "resolved", research_observation: normalizeResearchObservation({observation: raw})}];
  const observed = projectResearchComposition(params);
  assert.equal(observed.observed_count, 1);
  assert.throws(() => validateResearchExecution({...params, frontier: observed}), /pending gap/);
  const retained = {...params.todo as JsonObject, status: "done", claimed_by: null, archive_state: "archive", actionable_open: false};
  const retry = {...params.todo as JsonObject, todo_id: "todo_retry", explore_result_node_refs: ["retry"], target_key: "retry"};
  params.nodes = [...params.nodes as JsonObject[], {node_id: "retry", node_kind: "experiment", status: "resolved"}];
  params.edges = [...params.edges as JsonObject[], ...["a", "b"].map(to_node =>
    ({from_node: "retry", to_node, edge_type: "depends_on"}))];
  const retryRaw = {...raw, explore_node_id: "retry", progress: {...raw.progress as JsonObject, work_item_id: "todo_retry"},
    execution_lineage: {...raw.execution_lineage as JsonObject, successor_todo_id: "todo_retry"}};
  assert.throws(() => validateResearchExecution({...params, todo: retry, observation: retryRaw,
    frontier: projectResearchComposition({...params, todos: [retained, retry]})}), /pending gap/);
});

test("the three-card presentation budget cannot hide execution attribution", () => {
  const params = fixture(), nodes = params.nodes as JsonObject[];
  const template = nodes[0].research_observation as JsonObject;
  for (const [source, target] of [["c", "d"], ["e", "f"], ["g", "h"]]) {
    for (const node_id of [source, target]) {
      const raw = {...template, explore_node_id: node_id,
        progress: {...template.progress as JsonObject, work_item_id: `todo_${node_id}`, evidence_ids: [`ev-${node_id}`]},
        closure_basis: {...template.closure_basis as JsonObject, evidence_ids: [`ev-${node_id}`]},
        composition_candidates: node_id === source ? [{target_node_id: target, basis: "explicit",
          interaction_kind: "state_interference", evidence_ids: [`ev-${source}`, `ev-${target}`]}] : []};
      nodes.push({node_id, node_kind: "hypothesis", status: "resolved",
        research_observation: normalizeResearchObservation({observation: raw})});
    }
  }
  const all = researchCompositionGaps(params), compact = projectResearchFrontier(params);
  assert.equal(all.length, 4);
  assert.equal((compact.gaps as JsonObject[]).length, 3);
  const omitted = all[3];
  params.edges = (omitted.input_node_ids as string[]).map(to_node => ({from_node: "joint", to_node, edge_type: "depends_on"}));
  const raw = params.observation as JsonObject;
  params.observation = {...raw, input_observations: omitted.input_observations,
    execution_lineage: {...raw.execution_lineage as JsonObject, gap_id: omitted.gap_id}};
  assert.doesNotThrow(() => validateResearchExecution(params));
});
