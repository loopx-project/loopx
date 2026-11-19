import assert from "node:assert/strict";
import test from "node:test";
import {prepareBenchmarkTerminalFollowthrough as prepare} from "../../loopx/control_plane/capabilities/benchmark_terminal_followthrough.ts";
import {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} from "../../loopx/control_plane/effect_runtime_handlers.ts";

function fixture() {
  const identity = {
    benchmark_id: "synthetic", study_id: "study", case_id: "case", run_id: "run",
    arm_id: "reference", arm_role: "baseline", attempt: 1,
  };
  const run = {run_id: "run", case_id: "case", arm_role: "baseline", admitted_at: "2026-01-01T01:00:00Z"};
  const row = {
    ...identity,
    schema_version: "benchmark_experiment_board_row_v0",
    status: "running", observed_at: "2026-01-01T01:01:00+00:00",
    model_id: "model", protocol_id: "protocol", comparison_protocol_id: "comparison",
    claim_scope: "matched_study", primary_metric: "score", guardrail_metrics: [],
    metrics: {}, countability: {integrity_qualified: false, official_result_present: false, score_countable: false},
    treatment_fidelity: "not_applicable", effort: {}, insight: {status: "pending"},
  };
  return {
    goal_id: "goal",
    reconciled_at: "2026-01-01T01:04:00+00:00",
    admission_receipt: {ok: true, admitted: true, dry_run: false, write_performed: true, idempotent: false,
      goal_id: "goal", run},
    identity,
    rows: [row],
    active_runs: [run],
    terminal: {...identity, status: "completed", observed_at: "2026-01-01T01:02:00+00:00",
      solver_terminal: true, grading_drained: true},
    runtime_observation: {
      schema_version: "benchmark_runtime_observation_v0", admission_active: true, job_receipt_state: "resolved",
      runner_owner_state: "unknown", terminal_result_present: true, reconciliation_required: true,
      classification: "terminal_pending_reconcile", recommended_transition: "write_terminal_then_release",
    },
  };
}

test("prepares the terminal board transition and exact release through the Effect runtime", async () => {
  const request = fixture();
  const before = structuredClone(request);
  const handlers = createEffectRuntimeHandlers({fingerprint: "test", requestShutdown() {}});
  assert.deepEqual(await dispatchEffectRuntimeMethod(handlers, "benchmark.terminal-followthrough.prepare", request), {
    row: {...request.rows[0], status: "completed", observed_at: request.reconciled_at},
    active_run: request.admission_receipt.run,
    already_released: false,
  });
  assert.deepEqual(request, before);
});

test("a confirmed drained runner-invalid attempt can close an existing planned row", () => {
  const request = fixture();
  request.rows[0].status = "planned";
  request.terminal.status = "runner_invalid";
  const result = prepare(request);
  assert.equal(result.row.status, "runner_invalid");
  assert.deepEqual(result.row.countability, request.rows[0].countability);
  assert.deepEqual(result.active_run, request.admission_receipt.run);
});

test("replay preserves late score and insight enrichment with and without the admitted slot", () => {
  const request = fixture();
  const row = {
    ...request.rows[0], status: "completed", observed_at: "2026-01-01T01:03:00+00:00",
    metrics: {score: {value: 0, unit: "points", higher_is_better: true}},
    countability: {integrity_qualified: true, official_result_present: true, score_countable: true},
    insight: {status: "complete"},
  };
  const replay = {...request, rows: [row], reconciled_at: row.observed_at};
  assert.deepEqual(prepare(replay), {
    row, active_run: request.admission_receipt.run, already_released: false,
  });
  const released = {...request.runtime_observation, admission_active: false, reconciliation_required: false,
    classification: "not_admitted", recommended_transition: "none"};
  assert.deepEqual(prepare({...replay, active_runs: [], runtime_observation: released}), {
    row, active_run: null, already_released: true,
  });
  assert.throws(() => prepare({...replay, runtime_observation: released}),
    /resolved terminal runtime evidence/);
});

test("terminal status alone cannot close or release a run with unknown runtime or grading", () => {
  const request = fixture();
  for (const flags of [{solver_terminal: false}, {grading_drained: false}, {grading_drained: null}]) {
    assert.throws(() => prepare({...request, terminal: {...request.terminal, ...flags}}), /terminal solver and drained grading/);
  }
  for (const evidence of [{job_receipt_state: "ambiguous"}, {terminal_result_present: false},
    {classification: "runner_lost_pending_reconcile", recommended_transition: "write_runner_invalid_then_release"}]) {
    assert.throws(() => prepare({...request, runtime_observation: {...request.runtime_observation, ...evidence}}),
      /resolved terminal runtime evidence/);
  }
});

test("requires the original executed admission receipt for the same goal and run", () => {
  const request = fixture();
  for (const fields of [{dry_run: true}, {admitted: false}, {goal_id: "other-goal"},
    {idempotent: true}, {write_performed: false}, {idempotent: true, write_performed: false}]) {
    assert.throws(() => prepare({...request, admission_receipt: {...request.admission_receipt, ...fields}}),
      /original executed admission for this goal/);
  }
  const run = {...request.admission_receipt.run, case_id: "other-case"};
  assert.throws(() => prepare({...request, admission_receipt: {...request.admission_receipt, run}}), /does not match the run identity/);
});

test("an enriched terminal row does not authorize releasing a reused or ambiguous slot", () => {
  const request = fixture();
  request.rows[0].status = "completed";
  const [slot] = request.active_runs;
  for (const replacement of [{admitted_at: "2026-01-01T01:00:00.000001Z"}, {case_id: "another-case"}, {arm_role: "explore"}]) {
    assert.throws(() => prepare({...request, active_runs: [{...slot, ...replacement}]}),
      {kind: "conflict", message: "active slot does not match the executed admission"});
  }
  assert.throws(() => prepare({...request, active_runs: [slot, slot]}), /active slot does not match/);
});

test("rejects absent, mismatched and ambiguous board identities without creating a row", () => {
  const request = fixture();
  const [row] = request.rows;
  for (const rows of [[], [{...row, attempt: 2}], [{...row, arm_id: "other-arm"}], [row, {...row, arm_id: "other-arm"}]]) {
    assert.throws(() => prepare({...request, rows}), /one existing exact board row/);
  }
  assert.throws(() => prepare({...request, terminal: {...request.terminal, attempt: 2}}),
    /terminal evidence does not match the run identity/);
});

test("a goal slot cannot disambiguate the same run ID across studies or benchmarks", () => {
  const request = fixture();
  const [row] = request.rows;
  for (const scope of [{study_id: "other-study"}, {benchmark_id: "other-benchmark"}]) {
    assert.throws(() => prepare({...request, rows: [row, {...row, ...scope}]}), /one existing exact board row/);
  }
  const other = {...row, study_id: "other-study", run_id: "other-run"};
  assert.deepEqual(prepare({...request, rows: [row, other]}).active_run, request.admission_receipt.run);
});

test("a missing slot cannot advance a board row and a conflicting terminal status cannot release a slot", () => {
  const request = fixture();
  assert.throws(() => prepare({...request, active_runs: []}), /terminal board transition requires the admitted slot/);
  assert.throws(() => prepare({...request, rows: [{...request.rows[0], status: "cancelled"}]}),
    /existing board status conflicts/);
});

test("rejects terminal evidence older than the executed admission", () => {
  const request = fixture();
  const terminal = {...request.terminal, observed_at: "2026-01-01T00:59:59.999999Z"};
  assert.throws(() => prepare({...request, terminal}), /terminal observation predates admission/);
});

test("delayed terminal closeout uses host time after the newer running snapshot", () => {
  const request = fixture();
  const row = {...request.rows[0], observed_at: "2026-01-01T09:02:00.000001+08:00"};
  assert.deepEqual(prepare({...request, rows: [row]}), {
    row: {...row, status: "completed", observed_at: request.reconciled_at},
    active_run: request.admission_receipt.run, already_released: false,
  });
});

test("a host timestamp at or before the running snapshot cannot order the terminal transition", () => {
  const request = fixture();
  const row = {...request.rows[0], observed_at: "2026-01-01T09:02:00.000001+08:00"};
  for (const reconciled_at of ["2026-01-01T01:02:00.000001Z", "2026-01-01T01:02:00Z"]) {
    assert.throws(() => prepare({...request, rows: [row], reconciled_at}),
      /host reconciliation time must follow the existing board observation/);
  }
});

test("provider proof cannot postdate the host reconciliation time", () => {
  const request = fixture();
  const terminal = {...request.terminal, observed_at: "2026-01-01T01:04:00.000001Z"};
  assert.throws(() => prepare({...request, terminal}), /terminal observation follows host reconciliation time/);
});
