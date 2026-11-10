import assert from "node:assert/strict";
import test from "node:test";
import {
  BUILTIN_STAGE_METHODOLOGY_OPERATORS,
  LOOPX_CONTROL_STAGES,
  committedInWorkWindow,
  evaluateMethodologyActivation,
  isHistoricalReplayPrecedingCurrent,
  isValidContentSha256,
  latestCommittedScopedRecord,
  listStageMethodologyOperators,
  matchesExactScopeTuple,
  matchesWorkIdentity,
  normalizeCanonicalSourceSnapshot,
  projectBoundedMethodologyItems,
  recordedBeforePrefix,
  selectCanonicalEvidenceWindow,
  verifyMethodologyOperatorOutput,
} from "../../loopx/control_plane/capabilities/stage_methodology_operator.ts";
import {projectWorkYardstickCue, workYardstickTodos} from "../../loopx/control_plane/work_items/yardstick_decision.ts";

test("Bug Class 1: strict feature-off & quiet-turn silence reject truthy non-booleans and untriggered turns", () => {
  for (const nonTrue of [false, undefined, null, 0, 1, "true", "false", {}, []]) {
    assert.deepEqual(evaluateMethodologyActivation({enabled: nonTrue}), {
      active: false,
      reason: "disabled",
    });
    assert.deepEqual(
      workYardstickTodos({
        yardstick_enabled: nonTrue,
        agent_id: "worker",
        work_todos: [{todo_id: "todo_a", role: "agent", done: false, claimed_by: "worker", status: "open"}],
      }),
      [],
    );
    assert.equal(
      projectWorkYardstickCue({enabled: nonTrue, goal_id: "g", agent_id: "worker", todo_id: "todo_a", findings: []}),
      null,
    );
  }

  assert.deepEqual(
    evaluateMethodologyActivation({
      enabled: true,
      required_scope: {goal_id: "g", todo_id: "   "},
    }),
    {active: false, reason: "missing_scope"},
  );
  assert.deepEqual(
    evaluateMethodologyActivation({
      enabled: true,
      quiet_turn_policy: "silent_when_untriggered",
      trigger_active: false,
    }),
    {active: false, reason: "quiet_turn"},
  );
  assert.deepEqual(
    evaluateMethodologyActivation({
      enabled: true,
      quiet_turn_policy: "silent_when_untriggered",
      trigger_active: true,
      required_scope: {goal_id: "g", todo_id: "todo_a"},
    }),
    {active: true, reason: "active"},
  );
});

test("Bug Class 2: canonical evidence window blocks late replays, tied clocks, and post-prefix uncited injections", () => {
  const sha = "a".repeat(64);
  const snapshot = normalizeCanonicalSourceSnapshot({event_count: 3, event_ids_sha256: sha});
  assert.equal(snapshot.event_count, 3);
  assert.throws(() => normalizeCanonicalSourceSnapshot({event_count: -1, event_ids_sha256: sha}), /event count/);
  assert.throws(() => normalizeCanonicalSourceSnapshot({event_count: 1, event_ids_sha256: "short"}), /digest/);

  const binding = {goal_id: "goal-alpha", agent_id: "agent-1", todo_id: "todo_work"};
  const decisionRow = {
    finding_id: "f-decision",
    node_id: "node-q",
    recorded_order: 3,
    last_updated_at: "2026-06-01T10:00:00Z",
    work_yardstick: {binding, decision: {target: {summary: "T", scope: "S", acceptance_ref: "acc:1"}}},
  };
  // Late delivery of an OLD primary (committed 09:59 < 10:00, appended at order 5 >= 3): must NOT win or enter window!
  const delayedOldPrimary = {
    finding_id: "f-delayed-old",
    node_id: "node-q",
    status: "confirmed",
    recorded_order: 5,
    last_updated_at: "2026-06-01T09:59:00Z",
    work_binding: binding,
  };
  // Tied timestamp at order 6: tied clock does NOT prove newer observation!
  const tiedClockPrimary = {
    finding_id: "f-tied",
    node_id: "node-q",
    status: "confirmed",
    recorded_order: 6,
    last_updated_at: "2026-06-01T10:00:00Z",
    work_binding: binding,
  };
  // Genuinely newer confirmed observation in same work window: admitted!
  const validNewPrimary = {
    finding_id: "f-new",
    node_id: "node-q",
    status: "confirmed",
    recorded_order: 7,
    last_updated_at: "2026-06-01T10:01:00Z",
    work_binding: binding,
  };
  // Explicitly cited pre-prefix finding vs. post-prefix spoofed citation
  const citedBeforePrefix = {
    finding_id: "f-cited-early",
    node_id: "node-q",
    agent_id: "agent-1",
    recorded_order: 2,
    evidence_refs: ["ev:cited"],
  };
  const spoofedAfterPrefix = {
    finding_id: "f-cited-late-spoof",
    node_id: "node-q",
    agent_id: "agent-1",
    recorded_order: 3,
    evidence_refs: ["ev:cited"],
  };

  assert.equal(isValidContentSha256(sha), true);
  assert.equal(isValidContentSha256("A".repeat(64)), false);
  assert.equal(
    isHistoricalReplayPrecedingCurrent("2026-06-01T09:59:00Z", "2026-06-01T10:00:00Z"),
    true,
  );
  assert.equal(
    isHistoricalReplayPrecedingCurrent("2026-06-01T10:00:00Z", "2026-06-01T10:00:00Z"),
    false,
  );
  assert.equal(recordedBeforePrefix(citedBeforePrefix, 3), true);
  assert.equal(recordedBeforePrefix(spoofedAfterPrefix, 3), false);
  assert.equal(committedInWorkWindow(delayedOldPrimary, 3, 1780308000000000n), false);

  const latest = latestCommittedScopedRecord([decisionRow, {...delayedOldPrimary, work_yardstick: decisionRow.work_yardstick}], () => true);
  assert.equal(latest?.finding_id, "f-decision");

  const selected = selectCanonicalEvidenceWindow({
    findings: [decisionRow, delayedOldPrimary, tiedClockPrimary, validNewPrimary],
    cited_sources: [
      {
        source_finding_id: "f-decision",
        source_snapshot: snapshot,
        findings: [citedBeforePrefix, spoofedAfterPrefix],
      },
    ],
    source_finding: decisionRow,
    binding,
    source_snapshot: snapshot,
    cited_refs: new Set(["ev:cited"]),
    has_decision_attachment: row => row.work_yardstick !== undefined,
    matches_decision_target: () => true,
  });

  assert.deepEqual(
    selected.scoped_findings.map(r => r.finding_id),
    ["f-decision", "f-new", "f-cited-early"],
  );
});

test("Bug Class 3 & 4: exact scope-tuple isolation and bounded cold-path projection", () => {
  const expected = {
    metric: "accuracy",
    unit: "pct",
    higher_is_better: true,
    comparison_scope: "val:fixed",
    input_revision: "rev-1",
    evaluation_ref: "eval:v1",
  };
  const keys = ["metric", "unit", "higher_is_better", "comparison_scope", "input_revision", "evaluation_ref"] as const;
  assert.equal(matchesExactScopeTuple({...expected}, expected, keys), true);
  assert.equal(matchesExactScopeTuple({...expected, input_revision: "rev-2"}, expected, keys), false);
  assert.equal(matchesExactScopeTuple({...expected, higher_is_better: false}, expected, keys), false);
  assert.equal(
    matchesWorkIdentity(
      {work_binding: {goal_id: "g1", agent_id: "a1", todo_id: "todo_1"}},
      {goal_id: "g1", agent_id: "a1", todo_id: "todo_2"},
    ),
    false,
  );

  const bounded = projectBoundedMethodologyItems(["a", "b", "c", "d", "e"], 3, ["loopx", "explore", "turn-context"]);
  assert.deepEqual(bounded, {
    visible: ["a", "b", "c"],
    omitted_count: 2,
    details_command: ["loopx", "explore", "turn-context"],
  });
});

test("Bug Class 5: 5-stage operator catalog completeness and non-escalation output verifier", () => {
  const catalog = listStageMethodologyOperators();
  assert.deepEqual(catalog.stages, [...LOOPX_CONTROL_STAGES]);
  assert.equal(BUILTIN_STAGE_METHODOLOGY_OPERATORS.length, 6);
  for (const op of BUILTIN_STAGE_METHODOLOGY_OPERATORS) {
    assert.equal(op.advisory_only, true);
    assert.equal(op.grants_execution_authority, false);
    assert.equal(op.rollback_contract, "stateless_disable_preserves_history");
    assert.ok(op.lightweight_guidance.length > 20 && op.lightweight_guidance.length <= 240);
  }

  const executeStageOps = listStageMethodologyOperators({stage: "execute_verify"});
  const ids = (executeStageOps.operators as Array<{operator_id: string}>).map(o => o.operator_id);
  assert.ok(ids.includes("work_yardstick"));
  assert.ok(ids.includes("scoped_yardstick_fidelity"));
  assert.ok(ids.includes("counterfactual_continuation"));
  assert.ok(ids.includes("evolutionary_population"));

  const clean = verifyMethodologyOperatorOutput({
    operator_id: "scoped_yardstick_fidelity",
    output: {advisory_only: true, grants_execution_authority: false, score: 0.75, unknown_cost: null},
  });
  assert.equal(clean.valid, true);

  const escalated = verifyMethodologyOperatorOutput({
    operator_id: "rogue_op",
    output: {
      advisory_only: true,
      grants_execution_authority: false,
      should_run: true,
      nested: {bad: Number.NaN, ctrl: "line1\n\u0007"},
    },
    max_bytes: 32,
  });
  assert.equal(escalated.valid, false);
  assert.ok(escalated.violations.some(v => v.includes("should_run")));
  assert.ok(escalated.violations.some(v => v.includes("non-finite")));
  assert.ok(escalated.violations.some(v => v.includes("control characters")));
  assert.ok(escalated.violations.some(v => v.includes("exceeds cap")));
});
