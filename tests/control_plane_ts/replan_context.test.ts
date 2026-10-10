import assert from "node:assert/strict";
import test from "node:test";
import {createHash} from "node:crypto";
import {projectReplanContext, validateReplanContext} from "../../loopx/control_plane/work_items/replan_context.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const progress = {schema_version: "typed_progress_observation_v0", result_class: "unchanged",
  surface_id: "surface-a", work_item_id: "todo-a", fingerprint: "same-work"};
function row(time: number, agent = "agent-a"): JsonObject {
  return {goal_id: "goal-a", agent_id: agent, generated_at: new Date(time * 1000).toISOString(),
    observed_at: time, progress_observation: progress, health_check: "Probe did not improve the result."};
}
const request = {operation: "project", goal_id: "goal-a", agent_id: "agent-a",
  obligation: {obligation_id: "replan-a", triggers: []}, rows: [] as JsonObject[]};

test("scope, chronology, replay deduplication, and coverage are owned by code", () => {
  const context = projectReplanContext({...request,
    rows: [row(2), row(3, "agent-b"), row(1), row(2), {...row(4), goal_id: "goal-b"}]});
  const evidence = context.evidence as JsonObject[];
  assert.equal(evidence.length, 1);
  assert.deepEqual(evidence.map(item => item.generated_at), [row(2).generated_at]);
  assert.equal((context.coverage_ledger as JsonObject[]).length, 1);
  assert.equal(context.evidence_count, 2);
  assert.equal(evidence[0].occurrences, 2);
  assert.equal(evidence[0].first_observed_at, row(1).generated_at);
  assert.equal(context.coverage_count, 1);
  assert.match(String(evidence[0].read_action), /history --goal-id goal-a --agent-id agent-a --evidence-ref replan-evidence-/);
});

test("context is bounded with explicit omitted coverage, not a completeness claim", () => {
  const rows = Array.from({length: 30}, (_, i) => ({...row(i),
    progress_observation: {...progress, fingerprint: "work-" + i}}));
  const context = projectReplanContext({...request, rows});
  assert.equal((context.evidence as unknown[]).length, 24);
  assert.equal((context.coverage_ledger as unknown[]).length, 24);
  assert.equal(context.evidence_truncated, true);
  assert.equal(context.coverage_truncated, true);
  assert.equal(context.coverage_count, 30);
  assert.equal(validateReplanContext(context), context);
});

test("older different results survive many recent attempts, while repeated records retain their span", () => {
  const old = {...row(1), progress_observation: {...progress, result_class: "advanced", fingerprint: "old-best"}};
  const repeated = Array.from({length: 100}, (_, i) => row(i + 100));
  const context = projectReplanContext({...request, rows: [old, ...repeated]});
  const evidence = context.evidence as JsonObject[];
  assert.equal(evidence.length, 2);
  assert.equal(evidence[0].occurrences, 100);
  assert.equal(evidence[0].first_observed_at, row(100).generated_at);
  assert.equal(evidence[1].coverage_ref, "old-best");
  assert.equal(context.evidence_truncated, false);
  const manyRoutes = Array.from({length: 40}, (_, i) => ({...row(i + 10),
    progress_observation: {...progress, probe_kind: "probe-" + i, fingerprint: "work-" + i}}));
  const bounded = projectReplanContext({...request, rows: [old, ...manyRoutes]});
  assert.equal((bounded.evidence as JsonObject[]).length, 24);
  assert.ok((bounded.coverage_ledger as JsonObject[]).some(item => item.fingerprint === "old-best"));
  assert.equal((bounded.omitted_evidence as JsonObject).distinct_observation_count, 17);
});

test("core Goal is independent of work-scoped acceptance and recent evidence", () => {
  const acceptance = {scope: {kind: "selected_work", todo_ids: ["todo-a"]}, objective: "Check one route"};
  const context = projectReplanContext({...request, goal_facts: {registry_objective: "Registered objective",
    active_state_objective: "Deliver the complete accepted outcome", acceptance_contract: acceptance}, rows: [row(1)]});
  assert.deepEqual(context.core_goal, {objective: "Deliver the complete accepted outcome",
    objective_source: "active_state", objective_missing: false, acceptance_contract: acceptance});
  const absent = projectReplanContext({...request, goal_facts: {acceptance_contract: acceptance}, rows: [row(1)]});
  assert.equal((absent.core_goal as JsonObject).objective, null);
  assert.equal((absent.core_goal as JsonObject).objective_missing, true);
  assert.throws(() => validateReplanContext({...context, core_goal: null}));
});

test("an empty source is legal; a missing or unsupported contract is not empty", () => {
  const empty = projectReplanContext(request);
  assert.deepEqual(empty.evidence, []);
  assert.deepEqual(empty.coverage_ledger, []);
  for (const bad of [undefined, {}, {...empty, schema_version: "future"},
    {...empty, coverage_ledger: null}, {...empty, evidence: undefined},
    {...empty, uncovered_frontier: null}]) {
    assert.throws(() => validateReplanContext(bad));
  }
  assert.throws(() => projectReplanContext({...request, rows: undefined}), /rows/);
  assert.throws(() => projectReplanContext({...request, rows: [{...row(1),
    progress_observation: {...progress, schema_version: "future"}}]}), /schema_version/);
});

test("references resolve only the exact immutable row within the same Goal and Agent", () => {
  const context = projectReplanContext({...request, rows: [row(1)]});
  const ref = (context.evidence as JsonObject[])[0].evidence_ref;
  const resolve = {...request, operation: "resolve", evidence_ref: ref, rows: [row(1)]};
  assert.equal((projectReplanContext(resolve).evidence as JsonObject).health_check, row(1).health_check);
  for (const invalid of [{agent_id: "agent-b"}, {goal_id: "goal-b"}, {rows: []},
    {rows: [{...row(1), health_check: "Replaced observation"}]}, {evidence_ref: "missing"}]) {
    assert.throws(() => projectReplanContext({...resolve, ...invalid}), /unavailable/);
  }
});

function pair(time = 1): [JsonObject, JsonObject] {
  const identity = {schema_version: "quota_settlement_identity_v0", goal_id: "goal-a", agent_id: "agent-a",
    todo_id: "todo-a", turn_instance_id: "turn-" + time, effect_id: "settlement-" + time};
  const hash = (text: string) => createHash("sha256").update(text).digest("hex");
  const work = {...row(time), todo_id: "todo-a", classification: "bounded_probe", recommended_action: "Try the next route.",
    _source_facts: {...identity, settlement_identity: identity, quota_spend_commit: null,
      recommended_action_digest: hash("Try the next route."), health_check_digest: hash(String(row(time).health_check))}};
  const accounting = {...work, ...row(time + 0.1), progress_observation: null, classification: "quota_slot_spent",
    _source_facts: {...work._source_facts, quota_spend_commit: {
      schema_version: "quota_spend_commit_receipt_v0", effect_id: identity.effect_id + "#quota_spend"}}};
  return [work, accounting];
}

test("reduction preserves raw counts, negative coverage, obligation and immutable resolution", () => {
  const [work, accounting] = pair();
  const legacy = [work, accounting].map(({_source_facts: _source, ...row}) => row);
  const before = projectReplanContext({...request, rows: legacy});
  const after = projectReplanContext({...request, rows: [work, accounting]});
  assert.equal((after.evidence as JsonObject[]).length, 1);
  for (const key of ["evidence_count", "coverage_ledger", "coverage_count", "uncovered_frontier", "obligation_id", "core_goal"])
    assert.deepEqual(after[key], before[key]);
  for (const row of before.evidence as JsonObject[]) {
    const resolved = projectReplanContext({...request, rows: [work, accounting], operation: "resolve", evidence_ref: row.evidence_ref});
    assert.equal((resolved.evidence as JsonObject).recommended_action, work.recommended_action);
    assert.ok(!("_source_facts" in (resolved.evidence as JsonObject)));
  }
});

test("an unpaired grouped occurrence keeps the group's full continuation", () => {
  const [work, accounting] = pair();
  const {_source_facts: _source, ...unpaired} = {...accounting, ...row(3), progress_observation: null};
  const context = projectReplanContext({...request, rows: [work, accounting, unpaired]});
  const evidence = context.evidence as JsonObject[];
  assert.equal(evidence.length, 2);
  assert.equal(evidence[0].occurrences, 2);
  assert.ok(String(evidence[0].summary).includes(String(work.recommended_action)));
});

test("a display cap cannot remove a continuation whose work is not displayed", () => {
  const [work, accounting] = pair();
  const others = Array.from({length: 30}, (_, i) => ({...row(i + 10),
    health_check: "Independent validation " + i}));
  const latestAccounting = {...accounting, ...row(100), progress_observation: null};
  const context = projectReplanContext({...request, rows: [work, latestAccounting, ...others]});
  // Result/route diversity selects another work row; recency admits this
  // accounting row while its exact work row is outside the cap.
  assert.equal((context.evidence as JsonObject[]).length, 24);
  assert.equal(context.evidence_count, 32);
  assert.equal(context.coverage_count, 1);
  assert.ok((context.evidence as JsonObject[]).some(item => String(item.summary).includes(String(work.recommended_action))));
});

test("independent accounting, mismatched identities and actual progress are never hidden", () => {
  const [work, accounting] = pair();
  const source = accounting._source_facts as JsonObject;
  for (const altered of [accounting, {...accounting, progress_observation: progress},
    {...accounting, _source_facts: {...source, turn_instance_id: "another-turn"}},
    {...accounting, _source_facts: {...source, health_check_digest: null}, health_check: "Validation failed"}]) {
    const context = projectReplanContext({...request, rows: altered === accounting ? [altered] : [work, altered]});
    assert.equal((context.evidence as JsonObject[]).length, altered === accounting ? 1 : 2);
  }
});

test("invalid source digest shapes cannot authorize accounting reduction", () => {
  const hex = "b".repeat(64);
  for (const bad of [hex.slice(1), hex + "b", hex.toUpperCase(), "g".repeat(64), `sha256:${hex}`]) {
    for (const field of ["recommended_action_digest", "delivery_outcome_digest"]) {
      const rows = pair().map(item => ({...item,
        ...(field === "delivery_outcome_digest" ? {delivery_outcome: "verified result"} : {}),
        _source_facts: {...item._source_facts as JsonObject, [field]: bad}}));
      const context = projectReplanContext({...request, rows});
      assert.equal((context.evidence as JsonObject[]).length, 2, `${field}: ${bad}`);
      assert.ok((context.evidence as JsonObject[]).every(item =>
        String(item.summary).includes(String(rows[0].recommended_action))));
    }
  }
});
