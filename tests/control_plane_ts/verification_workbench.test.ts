import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import test from "node:test";
import {normalizeExploreExecutionObservation} from "../../loopx/control_plane/capabilities/explore_execution_observation.ts";
import {verifiedHostDepth} from "../../loopx/control_plane/capabilities/explore_verification_evidence.ts";
import {parseExploreHistoryPage} from "../../loopx/control_plane/presentation/explore_history.ts";
import {projectVerificationWorkbench} from "../../loopx/control_plane/presentation/verification_workbench.ts";
import {projectWorkYardstickPresentations} from "../../loopx/control_plane/work_items/yardstick_source.ts";

const baseline = {ref: "fixtures:baseline", sha256: "a".repeat(64)};
const candidate = "b".repeat(64);
const external = {criterion_id: "external", ref: "receipts:external", sha256: "c".repeat(64)};
const warm = {criterion_id: "warm", ref: "receipts:warm", sha256: "d".repeat(64)};
const hooks = {criterion_id: "hooks", ref: "receipts:hooks", sha256: "e".repeat(64)};
const compaction = {criterion_id: "compaction", ref: "receipts:compaction", sha256: "f".repeat(64)};
const receipts = [external, warm, hooks, compaction];
const evidenceRefs = [baseline, ...receipts].map(item => item.ref);
const execution = {
  schema_version: "explore_execution_observation_v0", candidate_sha256: candidate, input_revision: "inputs-v1",
  planned_method_id: "screen", executed_method_id: "screen", change_reason: null,
  elapsed_seconds: 10, limit_seconds: null, search_iterations: null, stop_reason: null, evidence_refs: evidenceRefs,
};
const exact = {metric: "loss", unit: "points", higher_is_better: false,
  comparison_scope: "scope:matched", input_revision: "inputs-v1", evaluation_ref: "check:exact"};
const proxy = {...exact, metric: "cost", evaluation_ref: "check:proxy"};
const measure = (profile: typeof exact, sha: string, value: number, ref: string) =>
  ({...profile, candidate_ref: `sha256:${sha}`, value, evidence_ref: ref});
const finding = (id: string, fields: Record<string, unknown> = {}) => parseExploreHistoryPage({
  ok: true, goal_id: "goal", total: 1, next_cursor: null,
  items: [{schema_version: "loopx_explore_result_event_v0", event_kind: "finding", event_id: id, goal_id: "goal",
    result_id: id, title: id, agent_id: "worker", recorded_at: "2026-11-21T12:00:00Z", node_id: "question",
    status: "confirmed", ...fields}],
}, "goal").items[0]!;

test("legacy execution and generic CLI retain unknown costs and tier, while measured zero survives", () => {
  assert.equal(normalizeExploreExecutionObservation({observation: execution}).verification_evidence, undefined);
  const readback = normalizeExploreExecutionObservation({observation: {...execution,
    verification_evidence: {host: "generic-cli", costs: {human_confirmations: {value: 0, evidence_ref: external.ref}}}}});
  assert.equal(readback.verification_evidence?.costs?.warm_cache_tokens, undefined);
  assert.equal(readback.verification_evidence?.costs?.human_confirmations?.value, 0);
  assert.equal(verifiedHostDepth(readback.verification_evidence, receipts), null);
});

test("the same exact checked receipts qualify every host name; absent or changed proof caps the tier", () => {
  for (const host of ["codex", "pi", "dsh", "generic-cli", "future-host"]) {
    const record = normalizeExploreExecutionObservation({observation: {...execution,
      verification_evidence: {host, proofs: {external_verifier: external, warm_session_cache: warm,
        white_box_hooks: hooks, receipt_compaction: compaction}}}}).verification_evidence;
    assert.equal(verifiedHostDepth(record, [warm, hooks, compaction]), null);
    assert.equal(verifiedHostDepth(record, [external, {...warm, sha256: compaction.sha256}]), 0);
    assert.equal(verifiedHostDepth(record, [external, warm, hooks]), 1);
    assert.equal(verifiedHostDepth(record, [external, warm, hooks, {...compaction, criterion_id: "other"}]), 1);
    assert.equal(verifiedHostDepth(record, receipts), 2);
  }
  assert.throws(() => normalizeExploreExecutionObservation({observation: {...execution,
    verification_evidence: {baseline: {...baseline, ref: "uncaptured"}}}}), /captured reference/);
  assert.throws(() => normalizeExploreExecutionObservation({observation: {...execution,
    verification_evidence: {costs: {human_confirmations: {value: -1, evidence_ref: external.ref}}}}}),
    /nonnegative/);
});

test("each tier requires a distinct verified receipt for the added capability", () => {
  const claims = {external_verifier: external, warm_session_cache: warm,
    white_box_hooks: hooks, receipt_compaction: compaction};
  const alias = {...warm, sha256: external.sha256};
  assert.equal(verifiedHostDepth({host: "generic-cli", proofs: {...claims, warm_session_cache: external}},
    receipts), 0);
  assert.equal(verifiedHostDepth({host: "generic-cli", proofs: {...claims, warm_session_cache: alias}},
    [...receipts, alias]), 0);
  assert.equal(verifiedHostDepth({host: "generic-cli", proofs: {...claims, receipt_compaction: warm}},
    receipts), 1);
});

test("Yardstick history pins its paired observations to the original work and event prefix", () => {
  const selected = finding("selected", {evidence_refs: evidenceRefs, work_yardstick: {
    binding: {goal_id: "goal", agent_id: "worker", todo_id: "todo_case", turn_instance_id: "turn-1"},
    decision: {schema_version: "work_yardstick_decision_v0",
      target: {summary: "Reduce errors", scope: "Frozen input", acceptance_ref: "check:exact"},
      authoritative: exact, proxy, counterevidence_refs: [baseline.ref], prior_finding_id: null,
      next_action: {method_id: "screen", reason: "Compare on fixed inputs", cost_bound: "One evaluation"}},
    }, execution_observation: {...execution, verification_evidence: {baseline}},
    measurements: [measure(exact, candidate, 5, external.ref), measure(proxy, candidate, 8, warm.ref)]});
  const control = finding("control", {evidence_refs: [baseline.ref], measurements: [
    measure(exact, baseline.sha256, 9, baseline.ref), measure(proxy, baseline.sha256, 4, baseline.ref),
  ]});
  const negative = finding("negative", {status: "refuted", evidence_refs: [hooks.ref]});
  const foreign = {...control, goal_id: "other"};
  const nearby = {...negative, node_id: "nearby"};
  const binding = selected.work_yardstick!.binding;
  const history = (rows: Record<string, unknown>[], {count = 1, broken = false, legacy = false} = {}) => {
    const digest = createHash("sha256").update(JSON.stringify(count ? ["control"] : [])).digest("hex");
    const capture = {...selected.work_yardstick, ...(legacy ? {} : {source_snapshot: {
      event_count: count, event_ids_sha256: broken ? "0".repeat(64) : digest,
    }})};
    const result = projectWorkYardstickPresentations({
      captures: [{capture, goal_id: "goal", agent_id: "worker", event_id: "selected", evidence_refs: evidenceRefs}],
      history_sources: {
        candidates: {selected: {...selected, recorded_order: 1}},
        findings_by_prefix: {[count]: rows},
        source_events: [{goal_id: "goal", event_id: "control"}, {goal_id: "goal", event_id: "selected"}],
      },
    });
    assert.deepEqual(result.captures, [selected.work_yardstick], "The original prefix stays on the server");
    return (result.comparisons as Record<string, unknown>).selected;
  };
  const earlier = {...control, recorded_order: 0};
  const projected = finding("selected", {...selected, yardstick_measurements: history([earlier])});
  const projection = projectVerificationWorkbench(projected, [projected, negative, nearby])!;
  assert.equal(projection.fidelity, "different_order");
  assert.deepEqual([projection.authoritative?.delta, projection.proxy?.delta], [4, -4]);
  assert.deepEqual(projection.alternatives.map(item => item.event_id), ["negative"]);
  assert.equal(projectVerificationWorkbench(selected, [selected, control, foreign])?.fidelity, "unknown",
    "Unqualified measurements in browser history cannot repair a missing pair");
  assert.deepEqual(history([{...earlier, work_binding: binding}]), projected.yardstick_measurements);
  assert.equal(history([{...earlier, agent_id: "another-worker"}]), undefined);
  assert.equal(history([{...earlier, work_binding: {...binding, todo_id: "todo_other"}}]), undefined);
  assert.equal(history([{...earlier, recorded_order: 2}]), undefined);
  assert.equal(history([earlier], {count: 0}), undefined);
  assert.equal(history([earlier], {broken: true}), undefined);
  assert.equal(history([earlier], {legacy: true}), undefined);
  assert.equal(history([earlier, {...earlier, event_id: "duplicate"}]), undefined);
  assert.equal(history([{...earlier, measurements: control.measurements?.map(row => ({...row, evidence_ref: "uncited"}))}]), undefined);
});
