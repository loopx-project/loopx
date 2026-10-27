import assert from "node:assert/strict";
import test from "node:test";
import {mkdtemp, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {SqliteAuthorityStore} from "../../loopx/control_plane/coordination/sqlite_authority_store.ts";
import {readCanonicalSnapshotFromStore, CANONICAL_SNAPSHOT_PAGE_REQUEST} from "../../loopx/control_plane/coordination/canonical_snapshot_page.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {refineGoalOutcome, projectGoalOutcomes, outcomeContextReadPlan} from "../../loopx/control_plane/goals/outcome_requirements.ts";
import {refineGoalAcceptanceOutcome, inspectGoalAcceptance, configureGoalAcceptance, transitionGoalAcceptanceLifecycle} from "../../loopx/control_plane/goals/acceptance_authority.ts";
import {observeAcceptanceOracles} from "../../loopx/control_plane/goals/acceptance_oracle.ts";
import {projectGoalAcceptance} from "../../loopx/control_plane/goals/acceptance_contract.ts";
import {projectReplanSemantics} from "../../loopx/control_plane/work_items/replan_semantics.ts";
import {evaluateTurnEnvelope} from "../../loopx/control_plane/quota/turn_envelope.ts";
import {canonicalAuthoritySha256} from "../../loopx/control_plane/coordination/authority_store_codec.ts";
import {projectInteractionRequiredReads} from "../../loopx/control_plane/work_items/interaction_contract.ts";

const goal = "tour-goal";
const goalRef = {goal_id: goal, goal_instance_id: "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"};
function fixture() {return authorityProjectionFixture(goal, [{todo_id: "todo_tour", role: "agent", status: "done", done: true,
  archive_state: "active", text: "Produce the presentation", task_class: "advancement_task", claimed_by: "author"}], [], "native",
  {root_commitments: {objective: "Explain the product", acceptance: ["Clear and accurate"]}});}
function patch(extra: JsonObject = {}): JsonObject {return {outcome_id: "tour", owner_agent_id: "author", objective: "Deliver the tour",
  requirements: [{id: "opening", description: "Keep the opening"}], todo_ids: ["todo_tour"],
  source: {ref: "message:initial", summary: "The owner requested a tour with an opening"}, ...extra};}

function outcomeGaps(head: JsonObject, acceptance: JsonObject = {}): JsonObject[] {
  return projectGoalOutcomes(head, goal, acceptance)!.acceptance_gaps as JsonObject[];
}

function outcomeAck(gaps: JsonObject[]): JsonObject {
  return {recorded: true, agent_id: "author", generated_at: "2026-08-01T00:00:00Z",
    semantic_delta: {accepted: true, obligation_id: "replan-1111111111111111",
      outcomes: ["new_concrete_blocker"], satisfying_outcomes: ["new_concrete_blocker"],
      trigger_kinds: [...new Set(gaps.map(gap => gap.kind))],
      trigger_checkpoints: gaps.slice(0, 3).map(({kind, frontier_revision}) => ({kind, frontier_revision}))}};
}

function scopedOutcomes(changes: Record<string, JsonObject> = {}) {
  const items = ["own", "peer", "dependency", "independent"].map(name => ({
    todo_id: `todo_${name}`, role: "agent", status: "open", done: false,
    archive_state: "active", text: `Deliver ${name}`, task_class: "advancement_task",
    claimed_by: name === "own" ? "author" : "peer", ...changes[name],
  }));
  const head: JsonObject = authorityProjectionFixture(goal, items, [], "native");
  for (const name of ["own", "peer", "dependency", "independent"]) {
    const owner = name === "own" ? "author" : "peer";
    head.goal_outcomes = refineGoalOutcome(head, goal, null, owner,
      patch({outcome_id: name, owner_agent_id: owner, todo_ids: [`todo_${name}`],
        source: {ref: `message:${name}`, summary: "Independently requested work"}}));
  }
  return head;
}

test("outcome reading plan follows canonical cross-Agent closure without dropping any source", () => {
  const head = scopedOutcomes({peer: {unblocks_todo_id: "todo_own"},
    dependency: {unblocks_todo_id: "todo_peer"}});
  const acceptance = projectGoalAcceptance(head, goal), before = structuredClone(head);
  const plan = outcomeContextReadPlan(head, goal, "author", "todo_own", acceptance)!;
  assert.equal(plan.complete, true);
  assert.deepEqual(plan.required_outcome_ids, ["own", "peer", "dependency"]);
  assert.deepEqual(head, before);
  assert.equal((acceptance.outcome_requirements as JsonObject).outcomes instanceof Array, true);
  const separate = scopedOutcomes();
  assert.deepEqual(outcomeContextReadPlan(separate, goal, "author", "todo_own",
    projectGoalAcceptance(separate, goal))!.required_outcome_ids, ["own"]);
  for (const relation of [{successor_todo_ids: ["todo_own"]}, {superseded_by: "todo_own"},
    {resume_when: "todo_done:todo_own"}]) {
    const linked = scopedOutcomes({peer: relation});
    assert.deepEqual(outcomeContextReadPlan(linked, goal, "author", "todo_own",
      projectGoalAcceptance(linked, goal))!.required_outcome_ids, ["own", "peer"]);
  }
  const sharedWait = scopedOutcomes({own: {resume_when: "pr_merged:example/repo#123"},
    peer: {resume_when: "pr_merged:example/repo#123"}});
  assert.deepEqual(outcomeContextReadPlan(sharedWait, goal, "author", "todo_own",
    projectGoalAcceptance(sharedWait, goal))!.required_outcome_ids, ["own", "peer"]);
  const sharedSource = structuredClone(projectGoalAcceptance(separate, goal));
  const rows = (sharedSource.outcome_requirements as JsonObject).outcomes as JsonObject[];
  rows[1].sources = rows[0].sources;
  assert.deepEqual(outcomeContextReadPlan(separate, goal, "author", "todo_own", sharedSource)!
    .required_outcome_ids, ["own", "peer"]);
  const sharedEvidence = structuredClone(projectGoalAcceptance(separate, goal));
  const evidenceRows = (sharedEvidence.outcome_requirements as JsonObject).outcomes as JsonObject[];
  for (const [index, conclusion] of ["met", "unmet"].entries()) {
    (evidenceRows[index].requirements as JsonObject[])[0].agent_review = {
      conclusion, evidence_refs: ["artifact:shared"], independently_verified: false,
    };
  }
  assert.deepEqual(outcomeContextReadPlan(separate, goal, "author", "todo_own", sharedEvidence)!
    .required_outcome_ids, ["own", "peer"], "opposing reviews of the same evidence remain visible");
});

test("shared acceptance and failed evidence widen reading; missing relationships keep the full source", () => {
  const head = scopedOutcomes({dependency: {unblocks_todo_id: "todo_peer"}});
  const acceptance = projectGoalAcceptance(head, goal);
  const read = (checks: JsonObject, todo = "todo_own") => outcomeContextReadPlan(head, goal, "author", todo,
    {...acceptance, ...checks})!;
  assert.deepEqual(read({scope: {kind: "selected_work", todo_ids: ["todo_own", "todo_peer"]}})
    .required_outcome_ids, ["own", "peer", "dependency"]);
  const conflicting = structuredClone(acceptance);
  const rows = (conflicting.outcome_requirements as JsonObject).outcomes as JsonObject[];
  (rows[1].requirements as JsonObject[])[0].status = "failed";
  assert.deepEqual(read(conflicting).required_outcome_ids, ["own", "peer", "dependency"]);
  for (const plan of [read({criteria: [{id: "global"}]}), read({}, "todo_unknown"),
    read({outcome_requirements: {...acceptance.outcome_requirements as JsonObject, current_lifetime: false}}),
    (() => {const missing = scopedOutcomes({peer: {unblocks_todo_id: "todo_absent"}});
      return outcomeContextReadPlan(missing, goal, "author", "todo_own", projectGoalAcceptance(missing, goal))!;})()]) {
    assert.equal(plan.complete, false);
    assert.deepEqual(plan.required_outcome_ids, ["own", "peer", "dependency", "independent"]);
    assert.ok((plan.fallback_reasons as string[]).length > 0);
  }
});

test("the captured index consumes the same reading plan and refuses stale or narrower coverage", () => {
  const head = scopedOutcomes({peer: {unblocks_todo_id: "todo_own"}});
  const acceptance = projectGoalAcceptance(head, goal);
  const plan = {...outcomeContextReadPlan(head, goal, "author", "todo_own", acceptance), provider_revision: "file:original"};
  const content = {status: "loaded", provider_revision: "file:original", outcome_context: plan,
    goal_acceptance_contract: acceptance};
  const source: JsonObject = {ok: true, goal_id: goal, agent_identity: {agent_id: "author"},
    selected_todo: {todo_id: "todo_own"}, interaction_contract: {agent_channel: {work_context: {
      complete: true, sources: [{source: "goal_acceptance", content}],
    }}}};
  const read = (payload: JsonObject) => evaluateTurnEnvelope({operation: "captured_outcome", payload, outcome_ref: ""});
  const index = read(source);
  assert.equal((index.read_plan as JsonObject).complete, true);
  assert.equal((index.read_plan as JsonObject).scope_applied, false, "the ordinary full-read obligation remains unchanged");
  assert.deepEqual((index.outcomes as JsonObject[]).filter(o => o.required_in_scope).map(o => o.id), ["own", "peer"]);
  const adaptive = read({...source, selected_todo: {todo_id: "todo_independent"},
    task_orchestration_contract: {schema_version: "task_orchestration_contract_v2", mode: "adaptive", primary_todo_id: "todo_own"}});
  assert.equal((adaptive.read_plan as JsonObject).complete, true, "use the existing admitted-work identity owner");
  assert.deepEqual((adaptive.outcomes as JsonObject[]).filter(o => o.required_in_scope).map(o => o.id), ["own", "peer"]);
  for (const altered of [
    {...source, autonomous_replan_obligation: {obligation_id: "wide-review"}},
    {...source, selected_todo: {todo_id: "todo_independent"}},
    {...source, agent_identity: {agent_id: "other"}},
    {...source, agent_todo_summary: {goal_acceptance_contract: {
      outcome_requirements: {outcomes: [{id: "another-version", requirements: []}]}}}},
  ]) {
    const fallback = read(altered);
    assert.equal((fallback.read_plan as JsonObject).complete, false);
    assert.ok((fallback.outcomes as JsonObject[]).every(o => o.required_in_scope));
  }
  for (const stale of [{provider_revision: "file:changed"},
    {acceptance_digest: "0".repeat(64)}, {required_outcome_ids: ["peer"]},
    {outcome_revision: 999}, {complete: false}, {schema_version: "future"}]) {
    const fallback = read({...source, interaction_contract: {agent_channel: {work_context: {sources: [
      {source: "goal_acceptance", content: {...content, outcome_context: {...plan, ...stale}}},
    ]}}}});
    assert.equal((fallback.read_plan as JsonObject).complete, false);
    assert.ok((fallback.outcomes as JsonObject[]).every(o => o.required_in_scope));
  }
  for (const change of [{future_dependency: {known: false}}, {revision: 999}, {global_check: true}]) {
    const changed = structuredClone(acceptance);
    if (change.global_check) changed.scope = {kind: "all_advancement"};
    else Object.assign(((changed.outcome_requirements as JsonObject).outcomes as JsonObject[])[1], change);
    const fallback = read({...source, interaction_contract: {agent_channel: {work_context: {sources: [
      {source: "goal_acceptance", content: {...content, goal_acceptance_contract: changed}},
    ]}}}});
    assert.equal((fallback.read_plan as JsonObject).complete, false);
    assert.ok((fallback.outcomes as JsonObject[]).every(o => o.required_in_scope));
  }
});

function outcomeContextPacket(linkPeer = false): JsonObject {
  const head = scopedOutcomes(linkPeer ? {peer: {unblocks_todo_id: "todo_own"}} : {});
  const acceptance = projectGoalAcceptance(head, goal);
  const rows = (acceptance.outcome_requirements as JsonObject).outcomes as JsonObject[];
  ((rows[0].requirements as JsonObject[])[0]).description = "Complete own requirement. ".repeat(800);
  ((rows[1].requirements as JsonObject[])[0]).description = "Unique peer requirement that was not delivered earlier.";
  const reads = projectInteractionRequiredReads({arbitration_facts: {
    schema_version: "loopx_interaction_contract_v0", mode: "bounded_delivery",
    user_channel: {present: true, action_required: false},
    agent_channel: {present: true, must_attempt: true, delivery_allowed: true, quiet_noop_allowed: false},
    cli_channel: {}, agent_scope_frontier_actions: []}, required_reads: [], command_prefix: "loopx",
    goal_id: goal, goal_acceptance_checks_present: false, goal_outcomes_present: true,
    should_run: true, delivery_allowed: true, selection_required: false, has_replan: false,
    settlement_only: false}).required_reads as JsonObject[];
  return {ok: true, goal_id: goal, agent_identity: {agent_id: "author"}, selected_todo: {todo_id: "todo_own"},
    goal_boundary: {stop: "Preserve the global stop."}, interaction_contract: {agent_channel: {work_context: {
      complete: true, sources: [{...reads[0], content: {status: "loaded", provider_revision: "file:original",
        outcome_context: {...outcomeContextReadPlan(head, goal, "author", "todo_own", acceptance), provider_revision: "file:original"},
        goal_acceptance_contract: acceptance}}],
    }}}};
}

test("scoped model context retains canonical source, global controls and exact optional detail", () => {
  const payload = outcomeContextPacket(), before = structuredClone(payload);
  const source_capture = {path: "/capture/original.json", sha256: "a".repeat(64)};
  const context = evaluateTurnEnvelope({operation: "outcome_context", payload, source_capture});
  assert.equal(context.schema_version, "outcome_context_projection_v0");
  assert.equal((context.read_scope as JsonObject).scope_applied, true);
  assert.deepEqual((context.context as JsonObject).goal_boundary, payload.goal_boundary);
  const sources = ((((context.context as JsonObject).interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[];
  const rows = (((sources[0].content as JsonObject).goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).outcomes as JsonObject[];
  assert.deepEqual(rows.map(row => row.id), ["own"]);
  assert.match(String(sources[0].reason), /every outcome required by this capture's read_scope/);
  assert.match(String(sources[0].reason), /read their complete bodies before reliance/);
  assert.match(String(sources[0].reason), /Preserve the original Goal and earlier requirements/);
  assert.equal(JSON.stringify(context).includes("Unique peer requirement"), false);
  const index = evaluateTurnEnvelope({operation: "captured_outcome", payload: context, source_payload: payload, outcome_ref: ""});
  assert.equal((index.read_plan as JsonObject).scope_applied, true);
  const peer = (index.outcomes as JsonObject[]).find(row => row.id === "peer")!;
  const detail = evaluateTurnEnvelope({operation: "captured_outcome", payload: context, source_payload: payload, outcome_ref: peer.outcome_ref});
  assert.ok(JSON.stringify(detail).includes("Unique peer requirement"));
  assert.deepEqual(payload, before, "the canonical decision and all provenance stay intact");
  const altered = structuredClone(context); (altered.context as JsonObject).goal_boundary = {stop: "changed"};
  assert.throws(() => evaluateTurnEnvelope({operation: "captured_outcome", payload: altered, source_payload: payload, outcome_ref: ""}), /differs from its original source/);
  assert.throws(() => evaluateTurnEnvelope({operation: "captured_outcome", payload: context, outcome_ref: ""}), /original outcome source/);
  const broad = {...payload, autonomous_replan_obligation: {id: "whole-goal"}};
  assert.deepEqual(evaluateTurnEnvelope({operation: "outcome_context", payload: broad, source_capture}), broad);
  const unknown = outcomeContextPacket();
  const content = (((((unknown.interaction_contract as JsonObject).agent_channel as JsonObject).work_context as JsonObject).sources as JsonObject[])[0]).content as JsonObject;
  const acceptance = content.goal_acceptance_contract as JsonObject;
  (((acceptance.outcome_requirements as JsonObject).outcomes as JsonObject[])[1]).future_obligation = "Unknown cross-Agent constraint";
  // Even a plan bound to that complete source must not hide unknown semantics.
  (content.outcome_context as JsonObject).acceptance_digest = canonicalAuthoritySha256(acceptance);
  assert.deepEqual(evaluateTurnEnvelope({operation: "outcome_context", payload: unknown, source_capture}), unknown);
  const unread = outcomeContextPacket();
  (unread.interaction_contract as JsonObject).agent_channel.required_reads = [{source: "goal_acceptance", command: "pending-read"}];
  assert.deepEqual(evaluateTurnEnvelope({operation: "outcome_context", payload: unread, source_capture}), unread);
  const future = outcomeContextPacket();
  future.interaction_contract.agent_channel.work_context.sources[0].reason += " Retain a future instruction.";
  assert.deepEqual(evaluateTurnEnvelope({operation: "outcome_context", payload: future, source_capture}), future);
});

test("warm scope expansion delivers previously unread requirements; lost baseline recovers the same required input", () => {
  const project = (payload: JsonObject, letter: string) => evaluateTurnEnvelope({operation: "outcome_context", payload,
    source_capture: {path: `/capture/${letter}.json`, sha256: letter.repeat(64)}});
  const before = project(outcomeContextPacket(), "a"), after = project(outcomeContextPacket(true), "b");
  const scope = {goal_id: goal, agent_id: "author", session_id: "same-session", context_epoch: "same-epoch"};
  const baseline = evaluateTurnEnvelope({operation: "context_delivery", context: before, scope, capture_id: "first", surface: "quota_packet"});
  const delivery = evaluateTurnEnvelope({operation: "context_delivery", context: after, scope, capture_id: "second", surface: "quota_packet",
    baseline, retained: {complete: true, target: baseline.target}});
  assert.equal(delivery.mode, "delta");
  assert.ok(JSON.stringify(delivery).includes("Unique peer requirement"), "a machine-only canonical source is not retained model context");
  const current = evaluateTurnEnvelope({operation: "context_delivery", context: after, scope, capture_id: "second", surface: "quota_packet"});
  const restored = evaluateTurnEnvelope({operation: "context_recovery", delivery, baseline, current});
  assert.deepEqual(restored.context, after);
  const lost = evaluateTurnEnvelope({operation: "context_recovery", delivery, current});
  assert.equal(lost.mode, "full"); assert.deepEqual(lost.context, after);
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: reading scope and full acceptance share one canonical observation`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-reading-scope-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const head = scopedOutcomes({peer: {unblocks_todo_id: "todo_own"}});
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: head, events: [], receipts: []});
  const load = store.loadAuthority.bind(store); let reads = 0;
  store.loadAuthority = async () => {reads++; return load();};
  const scoped = await inspectGoalAcceptance(store, goal, "todo_own", undefined, false, undefined, ["author", "peer"], "author");
  assert.equal(reads, 1);
  assert.deepEqual(scoped.goal_acceptance_contract, projectGoalAcceptance(head, goal));
  assert.deepEqual((scoped.outcome_context as JsonObject).required_outcome_ids, ["own", "peer"]);
  assert.equal((scoped.outcome_context as JsonObject).provider_revision, scoped.provider_revision);
  const ordinary = await inspectGoalAcceptance(store, goal);
  assert.equal(ordinary.outcome_context, undefined, "ordinary inspect keeps the full existing format");
  assert.deepEqual(ordinary.goal_acceptance_contract, scoped.goal_acceptance_contract);
  const unknown = await inspectGoalAcceptance(store, goal, "todo_own", undefined, false, undefined, ["author", "peer"], "unknown");
  assert.equal((unknown.outcome_context as JsonObject).complete, false);
  assert.deepEqual((unknown.outcome_context as JsonObject).required_outcome_ids, ["own", "peer", "dependency", "independent"]);
  assert.deepEqual(unknown.goal_acceptance_contract, scoped.goal_acceptance_contract);
});

test("refinement retains original intent, accumulates requirements and cannot invent success", () => {
  const original = fixture();
  assert.deepEqual(projectGoalAcceptance(original, goal), {});
  const state = refineGoalOutcome(original, goal, null, "author", patch());
  const head = {...original, goal_outcomes: state};
  const next = refineGoalOutcome(head, goal, null, "author", patch({requirements: [{id: "order", description: "Show delegation before the task board"}],
    todo_ids: [], source: {ref: "message:correction", summary: "Reorder the functional sections"}}));
  assert.deepEqual(next.outcomes[0].requirements.map(r => r.id), ["opening", "order"]);
  assert.deepEqual(next.outcomes[0].todo_ids, ["todo_tour"]);
  assert.deepEqual(original.root_commitments, head.root_commitments);
  assert.equal(next.outcomes[0].objective, state.outcomes[0].objective);
  const projection = projectGoalAcceptance({...original, goal_outcomes: next}, goal);
  const outcomes = (projection.outcome_requirements as JsonObject).outcomes as JsonObject[];
  assert.equal(((projection.outcome_requirements as JsonObject).acceptance_gaps as JsonObject[])[0].agent_id, "author");
  for (const status of ["open", "blocked", "deferred"]) {
    const waiting = authorityProjectionFixture(goal, [{todo_id: "todo_tour", role: "agent", text: "Produce the presentation", task_class: "advancement_task", claimed_by: "author", archive_state: "active", status, done: status === "deferred"}], [], "native", {goal_outcomes: next});
    assert.deepEqual(projectGoalOutcomes(waiting, goal, {})!.acceptance_gaps, [], "real waits remain with their Todo owner");
  }
  assert.ok((outcomes[0].requirements as JsonObject[]).every(r => r.status === "unverified"), "a done Todo is not accepted requirements");
  for (const bad of [patch({objective: "Replace the original intent"}), patch({owner_agent_id: "other"}),
    patch({requirements: [{id: "opening", description: "Remove the opening"}]}), patch({todo_ids: ["todo_missing"]}),
    {...patch(), passed: true}]) assert.throws(() => refineGoalOutcome(head, goal, null, "author", bad));
  assert.throws(() => refineGoalOutcome(head, goal, null, "other", patch()));
  const bound = refineGoalOutcome(head, goal, goalRef, "author", patch());
  assert.deepEqual(bound.goal_ref, goalRef, "the first exact binding preserves the same legacy source");
  assert.throws(() => refineGoalOutcome({...head, goal_outcomes: bound}, goal,
    {...goalRef, goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}, "author", patch()), /lifetime/);
});

test("only matching current acceptance and work coverage can qualify requirements", () => {
  const head = fixture(); head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", patch());
  const accepted = {enabled: true, status: "accepted", criteria: [{id: "opening", description: "Keep the opening"}],
    tasks: [{todo_id: "todo_tour", state: "ready", criterion_ids: ["opening"]}]};
  const states = (checks: JsonObject) => ((projectGoalOutcomes(head, goal, checks)!.outcomes as JsonObject[])[0].requirements as JsonObject[]).map(r => r.status);
  assert.deepEqual(states(accepted), ["accepted"]);
  for (const status of ["stale", "failed", "partial", "held", "unverified"]) assert.deepEqual(states({...accepted, status}), [status]);
  assert.deepEqual(states({...accepted, tasks: []}), ["unverified"]);
  assert.deepEqual(states({...accepted, criteria: [{id: "opening", description: "Unrelated check"}]}), ["unverified"]);
  assert.deepEqual(states({...accepted, criteria: [{...accepted.criteria[0], oracle: {state: "recorded"}}]}), ["unverified"]);
  head.goal_acceptance_lifecycle = {schema_version: "loopx_goal_acceptance_lifecycle_v0", state: "active", goal_ref: goalRef};
  assert.deepEqual(states(accepted), ["unverified"], "legacy declarations never acquire a successor lifetime");
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: one canonical planning snapshot carries only configured oracle observation refs`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-oracle-snapshot-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const snapshot = () => readCanonicalSnapshotFromStore({schema_version: CANONICAL_SNAPSHOT_PAGE_REQUEST,
    runtime_root: root, goal_id: goal, include_leases: false, projection_readback: null, after: null}, store);
  const empty = await snapshot();
  assert.equal(empty.metadata.goal_acceptance_contract, undefined);
  assert.equal(empty.metadata.goal_acceptance_oracle_observation_basis, undefined);
  const basis = await inspectGoalAcceptance(store, goal);
  assert.equal((await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "outcome",
    expected_provider_revision: basis.provider_revision, refinement: patch()})).status, "applied");
  assert.equal((await snapshot()).metadata.goal_acceptance_oracle_observation_basis, undefined,
    "ordinary outcome declarations do not authorize host artifact reads");
  const current = await inspectGoalAcceptance(store, goal);
  const validation_files = [{path: "check.py", sha256: "a".repeat(64)}];
  const oracle = {kind: "owner_pinned_oracle", source_id: "tour-checker", input_refs: [], output_refs: ["tour.txt"]};
  assert.equal((await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null, operation_id: "checks",
    expected_provider_revision: current.provider_revision, document: {outcome_id: "tour", criteria: [{id: "opening",
      validation_argv: ["python", "check.py"], validation_files, oracle}]}})).status, "applied");
  const committed = await store.loadAuthority();
  const load = store.loadAuthority.bind(store); let sourceReads = 0;
  store.loadAuthority = async () => {sourceReads++; return load();};
  const observed = await snapshot();
  assert.equal(sourceReads, 1, "oracle refs and Todo/check projections use the same authority read");
  const contract = observed.metadata.goal_acceptance_contract;
  assert.equal(observed.snapshot.provider_revision, committed.provider_revision);
  assert.deepEqual(observed.metadata.goal_acceptance_oracle_observation_basis, {
    revision: contract.revision, digest: contract.digest, criteria: [{id: "opening", validation_files, oracle}],
  });
  assert.ok(!JSON.stringify(contract).includes("validation_argv"), "commands remain private host data");
  assert.equal(contract.goal_acceptance_oracle_observation_basis, undefined);
  assert.deepEqual(await store.loadAuthority(), committed, "observation basis reads cannot rewrite authority");
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: native CAS, replay and restart retain one declaration`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-refinement-")); t.after(() => rm(root, {recursive: true, force: true}));
  const open = () => provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const store = open();
  const seeded = await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  assert.equal(seeded.status, "applied");
  const loaded = await store.loadAuthority(); assert.equal(loaded.status, "loaded"); if (loaded.status !== "loaded") return;
  const request = {goal_id: goal, actor_agent_id: "author", operation_id: "first", expected_provider_revision: loaded.provider_revision, refinement: patch()};
  const preview = await refineGoalAcceptanceOutcome(store, {...request, dry_run: true});
  assert.equal(preview.status, "planned"); assert.deepEqual(await store.loadAuthority(), loaded);
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const committed = await store.loadAuthority();
  assert.equal((await refineGoalAcceptanceOutcome(open(), request)).status, "replayed");
  assert.deepEqual(await store.loadAuthority(), committed);
  assert.equal((await refineGoalAcceptanceOutcome(store, {...request, operation_id: "stale"})).status, "conflict");
  assert.equal((await refineGoalAcceptanceOutcome(store, {...request, refinement: patch({objective: "Different"})})).reason_code, "coordination_operation_identity_mismatch");
  const read = await inspectGoalAcceptance(open(), goal);
  assert.equal(((read.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).revision, 1);
  if (committed.status === "loaded") assert.deepEqual(committed.head.root_commitments, loaded.head.root_commitments);
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: first exact-lifetime outcome preserves legacy acceptance atomically`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-lifetime-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const basis = await inspectGoalAcceptance(store, goal);
  const configured = await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null, operation_id: "legacy-check",
    expected_provider_revision: basis.provider_revision, document: {scope: {kind: "selected_work", todo_ids: ["todo_tour"]},
      objective: "Deliver the tour", non_goals: [], criteria: [{id: "opening", description: "Keep the opening", validation_argv: ["true"]}],
      bindings: [{todo_id: "todo_tour", criterion_ids: ["opening"]}]}});
  assert.equal(configured.status, "applied");
  const before = await inspectGoalAcceptance(store, goal);
  const request = {goal_id: goal, goal_ref: goalRef, actor_agent_id: "author", operation_id: "first-exact",
    expected_provider_revision: before.provider_revision, refinement: patch()};
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const after = (await inspectGoalAcceptance(store, goal, undefined, goalRef)).goal_acceptance_contract as JsonObject;
  const {outcome_requirements, lifecycle_state, goal_ref, ...checks} = after;
  assert.deepEqual(checks, before.goal_acceptance_contract);
  assert.equal((outcome_requirements as JsonObject).current_lifetime, true);
  assert.deepEqual(goal_ref, goalRef); assert.equal(lifecycle_state, "active");
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "replayed");
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: recreated Goal accepts new outcomes without inheriting its predecessor`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-recreation-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const initial = await inspectGoalAcceptance(store, goal);
  const first = {goal_id: goal, goal_ref: goalRef, actor_agent_id: "author", operation_id: "first-lifetime-outcome",
    expected_provider_revision: initial.provider_revision, refinement: patch()};
  assert.equal((await refineGoalAcceptanceOutcome(store, first)).status, "applied");
  const successor = {...goalRef, goal_instance_id: "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"};
  assert.equal((await transitionGoalAcceptanceLifecycle(store, {goal_id: goal, actor_agent_id: null,
    operation_id: "retire", transition: {kind: "retire", goal_ref: goalRef}})).status, "applied");
  const retiring = await inspectGoalAcceptance(store, goal, undefined, goalRef);
  assert.equal((await refineGoalAcceptanceOutcome(store, {...first, operation_id: "during-retirement",
    expected_provider_revision: retiring.provider_revision})).reason_code, "goal_acceptance_goal_retiring");
  assert.equal((await transitionGoalAcceptanceLifecycle(store, {goal_id: goal, actor_agent_id: null,
    operation_id: "successor", transition: {kind: "activate_successor", retired_goal_ref: goalRef, goal_ref: successor}})).status, "applied");
  const current = await inspectGoalAcceptance(store, goal, undefined, successor);
  const request = {...first, operation_id: "successor-outcome", goal_ref: successor,
    expected_provider_revision: current.provider_revision,
    refinement: patch({requirements: [{id: "new-scope", description: "Meet the recreated Goal's requested scope"}]})};
  assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "applied");
  const after = await store.loadAuthority();
  const projected = (await inspectGoalAcceptance(store, goal, undefined, successor)).goal_acceptance_contract as JsonObject;
  const declarations = projected.outcome_requirements as JsonObject;
  assert.equal(declarations.current_lifetime, true);
  assert.deepEqual(declarations.outcomes[0].requirements.map((row: JsonObject) => row.id), ["new-scope"]);
  assert.equal((await refineGoalAcceptanceOutcome(store, first)).status, "replayed", "old receipt remains recoverable");
  assert.deepEqual(await store.loadAuthority(), after, "old replay cannot restore predecessor declarations");
  assert.equal((await refineGoalAcceptanceOutcome(store, {...first, operation_id: "late-predecessor",
    expected_provider_revision: after.provider_revision})).reason_code, "goal_acceptance_goal_instance_mismatch");
});

for (const provider of ["file", "sqlite"] as const) for (const bindBy of ["refine", "lifecycle", "configure"] as const)
  test(`${provider}: first exact ${bindBy} keeps existing legacy outcome requirements`, async t => {
    const root = await mkdtemp(join(tmpdir(), "outcome-first-bind-")); t.after(() => rm(root, {recursive: true, force: true}));
    const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
    await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
    const initial = await inspectGoalAcceptance(store, goal);
    const legacy = {goal_id: goal, actor_agent_id: "author", operation_id: "legacy-outcome",
      expected_provider_revision: initial.provider_revision, refinement: patch()};
    assert.equal((await refineGoalAcceptanceOutcome(store, legacy)).status, "applied");
    if (bindBy === "lifecycle") assert.equal((await transitionGoalAcceptanceLifecycle(store, {
      goal_id: goal, actor_agent_id: null, operation_id: "bind", transition: {kind: "bind_existing", goal_ref: goalRef},
    })).status, "applied");
    if (bindBy === "configure") {
      const basis = await inspectGoalAcceptance(store, goal);
      assert.equal((await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null,
        operation_id: "configure", expected_provider_revision: basis.provider_revision, goal_ref: goalRef,
        document: {scope: {kind: "selected_work", todo_ids: ["todo_tour"]}, objective: "Deliver the tour", non_goals: [],
          criteria: [{id: "opening", description: "Keep the opening", validation_argv: ["true"]}],
          bindings: [{todo_id: "todo_tour", criterion_ids: ["opening"]}]}})).status, "applied");
    }
    const basis = await inspectGoalAcceptance(store, goal, undefined, bindBy === "refine" ? undefined : goalRef);
    const applied = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", goal_ref: goalRef,
      operation_id: "current-outcome", expected_provider_revision: basis.provider_revision,
      refinement: {outcome_id: "tour", requirements: [{id: "appendix", description: "Keep the appendix"}],
        source: {ref: "message:appendix", summary: "The same Goal also requires an appendix"}}});
    assert.equal(applied.status, "applied");
    const current = (applied.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject;
    assert.equal(current.current_lifetime, true);
    assert.deepEqual(current.outcomes[0].requirements.map((row: JsonObject) => row.id), ["opening", "appendix"]);
  });


test("host oracle freshness recomputes outcome evidence and unmet coverage", () => {
  const head = fixture(); head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", patch());
  const evidence = {input_versions: [{ref: "input.txt", sha256: "a".repeat(64)}], output_versions: [{ref: "tour.txt", sha256: "b".repeat(64)}]};
  const checks = {enabled: true, status: "accepted", criteria: [{id: "opening", description: "Keep the opening", oracle: {state: "recorded"}}],
    tasks: [{todo_id: "todo_tour", state: "ready", criterion_ids: ["opening"]}], verification: {results: [{criterion_id: "opening", oracle_evidence: evidence}]}};
  const contract = {...checks, outcome_requirements: projectGoalOutcomes(head, goal, checks)!};
  for (const current of [true, false]) {
    const result = observeAcceptanceOracles({contract, observations: [{criterion_id: "opening", available: true, source_current: current, ...evidence}]});
    const outcomes = result.outcome_requirements as JsonObject;
    const requirement = ((outcomes.outcomes as JsonObject[])[0].requirements as JsonObject[])[0];
    assert.equal(requirement.status, current ? "accepted" : "unverified");
    assert.equal((outcomes.acceptance_gaps as unknown[]).length, current ? 0 : 1);
  }
});

test("outcome checkpoint keeps bookkeeping quiet and rearms changed completed work", () => {
  const original = fixture();
  const head = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", patch())};
  const basis = outcomeGaps(head)[0].frontier_revision;
  const noted = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "author", {
    outcome_id: "tour", source: {ref: "note:repeated", summary: "The same owner response is still pending"}})};
  assert.equal(outcomeGaps(noted)[0].frontier_revision, basis, "source bookkeeping cannot buy another review");
  const changed = authorityProjectionFixture(goal, [{todo_id: "todo_tour", role: "agent", status: "done", done: true,
    archive_state: "active", text: "Produce a different presentation with a formal proof", task_class: "advancement_task",
    claimed_by: "author"}], [], "native", {goal_outcomes: head.goal_outcomes});
  assert.notEqual(outcomeGaps(changed)[0].frontier_revision, basis,
    "unchanged Todo id/status cannot make changed work inherit an old blocker");
});

test("outcome checkpoint notices new default review evidence without treating its notes as evidence", () => {
  const original = fixture();
  const item = {id: "opening", conclusion: "unmet", evidence_refs: ["artifact:first-review"], note: "The introduction is absent"};
  const head = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", patch({review: [item]}))};
  const ack = outcomeAck(outcomeGaps(head));
  const accepts = (review: JsonObject) => {
    const revised = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "author", {
      outcome_id: "tour", source: {ref: "review:next", summary: "Record the latest review"}, review: [review]})};
    const gap = outcomeGaps(revised);
    const requirement = ((projectGoalOutcomes(revised, goal, {})!.outcomes as JsonObject[])[0].requirements as JsonObject[])[0];
    assert.equal(requirement.planning_satisfied, false);
    assert.equal(requirement.agent_review.independently_verified, false);
    return projectReplanSemantics({operation: "vision_ack", ack, acceptance_gaps: gap}).acknowledged;
  };
  assert.equal(accepts({...item, note: "The same opening is still absent"}), true);
  assert.equal(accepts({...item, evidence_refs: ["artifact:second-review"]}), false);
  assert.equal(accepts({...item, conclusion: "uncertain"}), false);
});

test("bounded replan checkpoints cover all same-agent outcomes and notice a fourth changed requirement", () => {
  let head: JsonObject = fixture();
  for (let index = 0; index < 5; index++) head = {...head,
    goal_outcomes: refineGoalOutcome(head, goal, null, "author", patch({outcome_id: `tour-${index}`}))};
  const gaps = outcomeGaps(head), ack = outcomeAck(gaps);
  const accepts = (current: JsonObject) => projectReplanSemantics({operation: "vision_ack", ack,
    acceptance_gaps: outcomeGaps(current).filter(gap => gap.agent_id === "author")}).acknowledged;
  assert.equal(accepts(head), true, "bounded display must not strand the remaining current outcomes");
  const peer = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "peer", patch({
    outcome_id: "peer-tour", owner_agent_id: "peer", todo_ids: []}))};
  assert.equal(accepts(peer), true, "a different Agent's declaration cannot reset this lane");
  const changed = {...head, goal_outcomes: refineGoalOutcome(head, goal, null, "author", {
    outcome_id: "tour-3", requirements: [{id: "appendix", description: "Retain the supporting appendix"}],
    source: {ref: "message:appendix", summary: "The fourth outcome needs an appendix"}})};
  assert.equal(accepts(changed), false, "a requirement outside three displayed triggers must rearm");
});

test("two stale oracle artifact versions remain distinct replan evidence", () => {
  const original = fixture();
  const head = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", patch())};
  const evidence = {input_versions: [], output_versions: [{ref: "tour.txt", sha256: "a".repeat(64)}]};
  const checks = {status: "accepted", revision: 1, digest: "d".repeat(64),
    criteria: [{id: "opening", description: "Keep the opening", oracle: {state: "recorded"}}],
    tasks: [{todo_id: "todo_tour", state: "ready", criterion_ids: ["opening"]}],
    verification: {results: [{criterion_id: "opening", oracle_evidence: evidence}]}};
  const contract = {...checks, outcome_requirements: projectGoalOutcomes(head, goal, checks)!};
  const observed = (sha256: string) => observeAcceptanceOracles({contract,
    observations: [{criterion_id: "opening", available: true, source_current: true,
      input_versions: [], output_versions: [{ref: "tour.txt", sha256}]}]});
  const stale = (value: JsonObject) => (value.outcome_requirements as JsonObject).acceptance_gaps as JsonObject[];
  const first = observed("b".repeat(64)), second = observed("c".repeat(64));
  assert.equal((first.criteria[0] as JsonObject).oracle.state, "stale");
  assert.equal((second.criteria[0] as JsonObject).oracle.state, "stale");
  const ack = outcomeAck(stale(first));
  assert.equal(projectReplanSemantics({operation: "vision_ack", ack, acceptance_gaps: stale(observed("b".repeat(64)))}).acknowledged, true);
  assert.equal(projectReplanSemantics({operation: "vision_ack", ack, acceptance_gaps: stale(second)}).acknowledged, false,
    "a stale status label cannot grant the next artifact the previous blocker checkpoint");
});


test("default Agent review closes planning gaps without claiming independent acceptance", () => {
  const original = fixture();
  const state = refineGoalOutcome(original, goal, null, "author", patch());
  const first = {...original, goal_outcomes: state};
  const item = {id: "opening", conclusion: "met", evidence_refs: ["tour.txt#introduction"], note: "Inspected the opening against the retained requirement"};
  for (const review of [[], [{...item, evidence_refs: []}], [{...item, id: "unknown"}], [{...item, passed: true}]]) {
    assert.throws(() => refineGoalOutcome(first, goal, null, "author", patch({requirements: [], review})));
  }
  const reviewed = {...original, goal_outcomes: refineGoalOutcome(first, goal, null, "author", patch({requirements: [], review: [item], source: {ref: "artifact:review", summary: "Reviewed the produced artifact"}}))};
  const read = projectGoalOutcomes(reviewed, goal, {})!;
  assert.deepEqual(read.acceptance_gaps, []);
  const row = ((read.outcomes as JsonObject[])[0].requirements as JsonObject[])[0];
  assert.equal(row.status, "unverified");
  assert.equal((row.agent_review as JsonObject).independently_verified, false);
  assert.equal(row.planning_satisfied, true);
  const failed = projectGoalOutcomes(reviewed, goal, {enabled: true, status: "failed", criteria: [{id: "opening", description: "Keep the opening"}], tasks: []})!;
  assert.equal((failed.acceptance_gaps as unknown[]).length, 1, "self-assessment never bypasses a configured check");
  const changed = {...reviewed, goal_outcomes: refineGoalOutcome(reviewed, goal, null, "author", patch({requirements: [{id: "order", description: "Delegation before board"}], source: {ref: "message:order", summary: "Add ordering requirement"}}))};
  const stale = projectGoalOutcomes(changed, goal, {})!;
  assert.equal((stale.acceptance_gaps as unknown[]).length, 1);
  assert.equal(((((stale.outcomes as JsonObject[])[0].requirements as JsonObject[])[0].agent_review) as JsonObject).basis_current, false);
});

test("delta-only refinement resolves immutable intent without permitting foreign writes", () => {
  const original = fixture();
  const head = {...original, goal_outcomes: refineGoalOutcome(original, goal, null, "author", patch())};
  const delta = {outcome_id: "tour", source: {ref: "message:delta", summary: "Add the ordering requirement"},
    requirements: [{id: "order", description: "Keep the correct order"}]};
  const result = refineGoalOutcome(head, goal, null, "author", delta);
  assert.equal(result.outcomes[0].objective, "Deliver the tour");
  assert.equal(result.outcomes[0].owner_agent_id, "author");
  assert.deepEqual(result.outcomes[0].todo_ids, ["todo_tour"]);
  assert.deepEqual(result.outcomes[0].requirements.map(r => r.id), ["opening", "order"]);
  assert.throws(() => refineGoalOutcome(original, goal, null, "author", delta), /outcome Agent/);
  assert.throws(() => refineGoalOutcome(head, goal, null, "other", delta), /own outcomes/);
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: repeated reviews retain full declaration provenance`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-review-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const original = fixture();
  let state = refineGoalOutcome(original, goal, null, "author", patch());
  for (let index = 1; index < 64; index++) state = refineGoalOutcome({...original, goal_outcomes: state}, goal, null, "author", {
    outcome_id: "tour", source: {ref: `message:origin-${index}`, summary: "Retained declaration provenance"},
  });
  const origins = structuredClone(state.outcomes[0].sources);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null,
    next_projection: {...original, goal_outcomes: state}, events: [], receipts: []});
  for (let index = 1; index <= 3; index++) {
    const before = await inspectGoalAcceptance(store, goal);
    const request = {goal_id: goal, actor_agent_id: "author", operation_id: `review-${index}`,
      expected_provider_revision: before.provider_revision, refinement: {outcome_id: "tour", review: [
        {id: "opening", conclusion: "met", evidence_refs: [`artifact:tour-${index}`], note: "Read the retained opening"},
      ]}};
    const applied = await refineGoalAcceptanceOutcome(store, request);
    assert.equal(applied.status, "applied");
    assert.equal((await refineGoalAcceptanceOutcome(store, request)).status, "replayed");
    const readback = await inspectGoalAcceptance(store, goal);
    const outcomes = ((readback.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).outcomes as JsonObject[];
    assert.deepEqual(outcomes[0].sources, origins);
    assert.equal(outcomes[0].revision, 64 + index);
    const requirement = (outcomes[0].requirements as JsonObject[])[0];
    assert.equal(requirement.description, "Keep the opening");
    assert.equal(requirement.status, "unverified");
    assert.deepEqual((requirement.agent_review as JsonObject).evidence_refs, [`artifact:tour-${index}`]);
    assert.equal((requirement.agent_review as JsonObject).independently_verified, false);
    assert.equal((await refineGoalAcceptanceOutcome(store, {...request, operation_id: `stale-${index}`})).status, "conflict");
    for (const refinement of [
      {outcome_id: "tour", requirements: [{id: "new", description: "A new obligation"}]},
      {outcome_id: "tour", source: {ref: "message:overflow", summary: "Another declaration origin"}},
      {outcome_id: "tour", source: {...origins[0], summary: "Rewrite old provenance"}},
    ]) await assert.rejects(() => refineGoalAcceptanceOutcome(store, {...request,
      operation_id: `rejected-${index}`, expected_provider_revision: readback.provider_revision, refinement}));
    assert.deepEqual(await inspectGoalAcceptance(store, goal), readback, "rejected declarations must not commit or prune origins");
  }
  const withoutSource = patch(); delete withoutSource.source;
  assert.throws(() => refineGoalOutcome(original, goal, null, "author", withoutSource), /require source provenance/);
  const head = {...original, goal_outcomes: state};
  assert.throws(() => refineGoalOutcome(head, goal, null, "other", {outcome_id: "tour", review: []}), /own outcomes/);
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "tour", review: []}), /every retained requirement/);
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "tour", source: null}), /outcome source/);
});

test("work-link maintenance preserves origins while new requirements still need provenance", () => {
  const original = authorityProjectionFixture(goal, [
    {todo_id: "todo_tour", role: "agent", status: "open", done: false, archive_state: "active", text: "Produce the tour", task_class: "advancement_task", claimed_by: "author"},
    {todo_id: "todo_next", role: "agent", status: "open", done: false, archive_state: "active", text: "Validate the tour", task_class: "advancement_task", claimed_by: "author"},
    {todo_id: "todo_peer", role: "agent", status: "open", done: false, archive_state: "active", text: "Peer work", task_class: "advancement_task", claimed_by: "peer"},
  ]);
  const first = refineGoalOutcome(original, goal, null, "author", patch({review: [
    {id: "opening", conclusion: "met", evidence_refs: ["artifact:tour-v1"], note: "Read the original tour"},
  ]}));
  const head = {...original, goal_outcomes: first};
  const linked = refineGoalOutcome(head, goal, null, "author", {outcome_id: "tour", todo_ids: ["todo_next"]});
  assert.deepEqual(linked.outcomes[0].todo_ids, ["todo_tour", "todo_next"]);
  assert.deepEqual(linked.outcomes[0].sources, first.outcomes[0].sources);
  assert.deepEqual(linked.outcomes[0].requirements, first.outcomes[0].requirements);
  const observed = (projectGoalAcceptance({...original, goal_outcomes: linked}, goal).outcome_requirements as JsonObject).outcomes as JsonObject[];
  const requirement = (observed[0].requirements as JsonObject[])[0];
  assert.equal((requirement.agent_review as JsonObject).basis_current, false, "new work cannot retain an old review's current basis");
  assert.equal(requirement.planning_satisfied, false);
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "tour", todo_ids: ["todo_peer"]}), /another Agent/);
  assert.throws(() => refineGoalOutcome(head, goal, null, "author", {outcome_id: "tour",
    requirements: [{id: "order", description: "Retain ordering"}]}), /require source provenance/);
});

for (const provider of ["file", "sqlite"] as const) test(`${provider}: referenced checks bind reviewed requirements, preserve disabled checks and replay`, async t => {
  const root = await mkdtemp(join(tmpdir(), "referenced-check-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = provider === "file" ? new FileAuthorityStore(root, goal) : new SqliteAuthorityStore(root, goal);
  const head = fixture(); head.goal_outcomes = refineGoalOutcome(head, goal, null, "author", patch());
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: head, events: [], receipts: []});
  const original = await inspectGoalAcceptance(store, goal);
  const document = {outcome_id: "tour", criteria: [{id: "opening", validation_argv: ["true"]}]};
  const request = {goal_id: goal, actor_agent_id: null, operation_id: "checks", expected_provider_revision: original.provider_revision, document};
  const preview = await configureGoalAcceptance(store, {...request, dry_run: true});
  const resolved = preview.resolved_document as JsonObject;
  assert.equal(resolved.objective, "Deliver the tour");
  assert.deepEqual(resolved.scope, {kind: "selected_work", todo_ids: ["todo_tour"]});
  assert.deepEqual(resolved.bindings, [{todo_id: "todo_tour", criterion_ids: ["opening"]}]);
  assert.equal((resolved.criteria as JsonObject[])[0].description, "Keep the opening");
  assert.deepEqual(await inspectGoalAcceptance(store, goal), original, "prepare never writes or executes");
  for (const document of [{outcome_id: "missing", criteria: request.document.criteria},
    {outcome_id: "tour", criteria: [{id: "missing", validation_argv: ["true"]}]},
    {...request.document, objective: "Replace the original"},
    {outcome_id: "tour", criteria: [{id: "opening", description: "Rewrite the requirement", validation_argv: ["true"]}]}]) {
    await assert.rejects(configureGoalAcceptance(store, {...request, document, dry_run: true}));
  }
  const reviewed = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "review",
    expected_provider_revision: original.provider_revision, refinement: {outcome_id: "tour", source: {ref: "review:1", summary: "Inspected output"},
      review: [{id: "opening", conclusion: "met", evidence_refs: ["tour.txt"], note: "Opening remains"}]}});
  const admitted = {...request, review_context_digest: preview.review_context_digest};
  assert.equal((await configureGoalAcceptance(store, admitted)).status, "applied", "review bookkeeping alone does not invalidate a prepared check");
  const configured = await inspectGoalAcceptance(store, goal);
  assert.equal((configured.goal_acceptance_contract as JsonObject).outcome_id, "tour");
  const next = await configureGoalAcceptance(store, {...request, operation_id: "next-preview", expected_provider_revision: configured.provider_revision, dry_run: true});
  const changed = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "requirement",
    expected_provider_revision: configured.provider_revision, refinement: {outcome_id: "tour", source: {ref: "message:2", summary: "Preserve ordering too"},
      requirements: [{id: "order", description: "Preserve ordering"}]}});
  const stale = await configureGoalAcceptance(store, {...request, operation_id: "stale-plan", expected_provider_revision: configured.provider_revision,
    review_context_digest: next.review_context_digest});
  assert.equal(stale.reason_code, "goal_acceptance_review_context_mismatch");
  assert.equal((await configureGoalAcceptance(store, admitted)).status, "replayed", "historical retry never re-resolves or reconfigures intent");
  const disabled = await configureGoalAcceptance(store, {...request, operation_id: "disable", expected_provider_revision: changed.provider_revision, document: null});
  const after = await refineGoalAcceptanceOutcome(store, {goal_id: goal, actor_agent_id: "author", operation_id: "more-evidence",
    expected_provider_revision: disabled.provider_revision, refinement: {outcome_id: "tour", source: {ref: "review:2", summary: "Continue polishing"}}});
  assert.equal((after.goal_acceptance_contract as JsonObject).criteria, undefined, "removing checks leaves default outcome review available");
  assert.equal(Object.hasOwn((await store.loadAuthority() as {head: JsonObject}).head.goal_acceptance as JsonObject, "enabled"), false);
  assert.equal(Object.hasOwn(reviewed.goal_acceptance_contract as JsonObject, "enabled"), false);
});

for (const checksConfigured of [false, true]) test(`upgrade: every Goal receives the same refinement/review loop with checks=${checksConfigured}`, async t => {
  const root = await mkdtemp(join(tmpdir(), "outcome-upgrade-")); t.after(() => rm(root, {recursive: true, force: true}));
  const store = new SqliteAuthorityStore(root, goal);
  await store.commitAuthority({operation_id: "seed", expected_provider_revision: null, next_projection: fixture(), events: [], receipts: []});
  const before = await inspectGoalAcceptance(store, goal);
  const document = {scope: {kind: "selected_work", todo_ids: ["todo_tour"]}, objective: "Deliver the tour", non_goals: [],
    criteria: [{id: "opening", description: "Keep the opening", validation_argv: ["true"]}], bindings: [{todo_id: "todo_tour", criterion_ids: ["opening"]}]};
  await configureGoalAcceptance(store, {goal_id: goal, actor_agent_id: null, operation_id: "initial-checks", document,
    expected_provider_revision: before.provider_revision});
  const current = await store.loadAuthority(); assert.equal(current.status, "loaded"); if (current.status !== "loaded") return;
  const {owner_goal_ref: _ref, ...state} = current.head.goal_acceptance as JsonObject;
  // An actual old persisted Goal; not a specially activated new fixture.
  await store.commitAuthority({operation_id: "previous-version", expected_provider_revision: current.provider_revision,
    next_projection: {...current.head, goal_acceptance: {...state, schema_version: "loopx_goal_acceptance_v0", enabled: checksConfigured}}, events: [], receipts: []});
  const old = await inspectGoalAcceptance(store, goal);
  const request = {goal_id: goal, goal_ref: goalRef, actor_agent_id: "author", operation_id: "upgrade-refinement",
    expected_provider_revision: old.provider_revision, refinement: patch()};
  const upgraded = await refineGoalAcceptanceOutcome(store, request);
  assert.equal(upgraded.status, "applied");
  const result = upgraded.goal_acceptance_contract as JsonObject;
  assert.equal(Object.hasOwn(result, "enabled"), false);
  assert.equal(Array.isArray(result.criteria), checksConfigured);
  assert.equal(((result.outcome_requirements as JsonObject).outcomes as JsonObject[]).length, 1);
  const canonical = await store.loadAuthority(); assert.equal(canonical.status, "loaded"); if (canonical.status !== "loaded") return;
  assert.equal((canonical.head.goal_acceptance as JsonObject).schema_version, "loopx_goal_acceptance_v2");
  assert.equal(Object.hasOwn(canonical.head.goal_acceptance as JsonObject, "enabled"), false);
  assert.deepEqual(canonical.head.root_commitments, current.head.root_commitments);
  const review = await refineGoalAcceptanceOutcome(store, {...request, operation_id: "upgrade-review", expected_provider_revision: upgraded.provider_revision,
    refinement: {outcome_id: "tour", source: {ref: "artifact:review", summary: "Inspected the introduction"},
      review: [{id: "opening", conclusion: "unmet", evidence_refs: ["tour.txt"], note: "Introduction still missing"}]}});
  assert.equal(((review.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).goal_completion_assessed, false);
  assert.equal((((review.goal_acceptance_contract as JsonObject).outcome_requirements as JsonObject).acceptance_gaps as JsonObject[]).length, 1);
});
