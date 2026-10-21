import assert from "node:assert/strict";
import test from "node:test";

import {
  PLANNING_HORIZON_REQUEST_SCHEMA_VERSION,
  projectQuotaPlanningHorizon,
} from "../../loopx/control_plane/work_items/planning_horizon.ts";
import {
  TODO_PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION,
  TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION,
  projectTodoPlanningInventory,
  projectTodoPlanningInventoryDetail,
} from "../../loopx/control_plane/work_items/planning_inventory.ts";

function todo(
  todoId: string,
  index: number,
  priority: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    todo_id: todoId,
    index,
    text: `Deliver ${todoId}.`,
    priority,
    status: "open",
    task_class: "advancement_task",
    action_kind: `deliver_${index}`,
    claimed_by: "codex-planning",
    ...overrides,
  };
}

function request(overrides: Record<string, unknown> = {}) {
  const selected = todo("todo_regression_gate", 5, "P1");
  const legacy = {
    selected_todo: selected,
    candidates: [selected],
    runnable_todo_ids: [selected.todo_id],
    source_context_todo_count: 1,
    acceptance_gaps: [],
    ...overrides,
  };
  const candidates = legacy.candidates as Array<Record<string, unknown>>;
  const runnableIds = new Set(legacy.runnable_todo_ids as string[]);
  return {
    schema_version: PLANNING_HORIZON_REQUEST_SCHEMA_VERSION,
    planning_inventory: projectTodoPlanningInventory({
      schema_version: TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION,
      goal_id: "planning-horizon-fixture",
      agent_id: "codex-planning",
      selected_todo: legacy.selected_todo,
      source_items: candidates,
      runnable_candidates: candidates.filter((item) =>
        runnableIds.has(String(item.todo_id))
      ),
      unavailable_higher_priority: [],
      source_context_todo_count: legacy.source_context_todo_count,
    }),
    acceptance_gaps: legacy.acceptance_gaps,
    ...(overrides.goal_facts === undefined ? {} : {goal_facts: overrides.goal_facts}),
  };
}

test("one local task still carries the original Goal and separately scoped acceptance", () => {
  const objective = "Deliver the whole accepted outcome. ".repeat(20) + "Preserve the final constraint.";
  const result = projectQuotaPlanningHorizon(request({goal_facts: {
    active_state_objective: objective,
    registry_objective: "Older registered objective",
    goal_read_action: "cat -- 'source with spaces/ACTIVE_GOAL_STATE.md'",
    acceptance_contract: {objective: "Validate this slice only", scope: {kind: "selected_work"}},
  }}));
  assert.deepEqual(result?.core_goal, {
    objective, objective_source: "active_state", objective_missing: false,
    read_action: "cat -- 'source with spaces/ACTIVE_GOAL_STATE.md'",
    acceptance_contract: {objective: "Validate this slice only", scope: {kind: "selected_work"}},
  });
  assert.equal(result?.selected_todo_id, "todo_regression_gate");
  assert.equal((result?.selection_contract as Record<string, unknown>).horizon_changes_selection, false);
  assert.equal((result?.work_items as unknown[]).length, 1);
  assert.throws(() => projectQuotaPlanningHorizon(request({goal_facts: {active_state_objective: 42}})));
  assert.deepEqual(projectQuotaPlanningHorizon(request({goal_facts: {}}))?.core_goal, {
    objective: null, objective_source: null, objective_missing: true, acceptance_contract: null,
  });
});

test("Goal context exposes a bounded flat backlog without requiring declared dependencies", () => {
  const selected = todo("todo_selected001", 1, "P0");
  const alternatives = Array.from({length: 8}, (_, i) => todo(`todo_alternative${i}`, i + 2, "P1"));
  const inputs = {selected_todo: selected, candidates: [selected, ...alternatives],
    runnable_todo_ids: [selected.todo_id, ...alternatives.map(item => item.todo_id)],
    source_context_todo_count: 9};
  assert.equal(projectQuotaPlanningHorizon(request(inputs)), null);
  const result = projectQuotaPlanningHorizon(request({...inputs,
    goal_facts: {active_state_objective: "Complete the accepted outcome."}}));
  assert.equal((result?.core_goal as Record<string, unknown>).objective, "Complete the accepted outcome.");
  const items = result?.work_items as Array<Record<string, unknown>>;
  assert.equal(items.length, 5);
  assert.equal(items[0].todo_id, selected.todo_id);
  assert.equal(items[4].todo_id, alternatives[3].todo_id);
  const completeness = result?.completeness as Record<string, unknown>;
  assert.equal(completeness.omitted_candidate_todo_count, 4);
  assert.equal(completeness.complete, false);
  assert.ok((result?.detail_refs as Record<string, unknown>).full_todo_list);
});

test("planning horizon preserves the typed strategic chain around local selected work", () => {
  const facts = todo("todo_facts_source", 1, "P0", {
    status: "deferred",
    resume_when: "pr_merged:#fixture-source",
    successor_todo_ids: ["todo_allowlist_policy"],
  });
  const allowlist = todo("todo_allowlist_policy", 2, "P0", {
    status: "deferred",
    resume_when: "todo_done:todo_facts_source",
    successor_todo_ids: ["todo_runtime_admission"],
  });
  const runtime = todo("todo_runtime_admission", 3, "P0", {
    status: "deferred",
    resume_when: "todo_done:todo_allowlist_policy",
    successor_todo_ids: ["todo_per_model_tests"],
  });
  const tests = todo("todo_per_model_tests", 4, "P1", {
    status: "deferred",
    resume_when: "todo_done:todo_runtime_admission",
    successor_todo_ids: ["todo_regression_gate"],
  });
  const selected = todo("todo_regression_gate", 5, "P1");
  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, facts, allowlist, runtime, tests],
    source_context_todo_count: 5,
    acceptance_gaps: [{
      kind: "vision_acceptance_gap",
      source: "latest_agent_vision",
      acceptance_summary: "Prove policy, admission, and model coverage before treating the regression gate as sufficient.",
      replan_trigger_summary: "The local gate does not establish the upstream allowlist policy.",
      advancement_policy: "repeat_until_closed",
    }],
  }));

  assert.equal(result?.schema_version, "quota_planning_horizon_v0");
  assert.equal(result?.mode, "read_only");
  assert.deepEqual(
    (result?.work_items as Array<Record<string, unknown>>).map((item) => item.todo_id),
    [
      "todo_regression_gate",
      "todo_per_model_tests",
      "todo_runtime_admission",
      "todo_allowlist_policy",
      "todo_facts_source",
    ],
  );
  assert.deepEqual(result?.attention_todo_ids, [
    "todo_per_model_tests",
    "todo_runtime_admission",
    "todo_allowlist_policy",
  ]);
  assert.deepEqual(
    (result?.relations as Array<Record<string, unknown>>).filter(
      (relation) => relation.relation === "successor",
    ),
    [
      {
        from_todo_id: "todo_per_model_tests",
        to_ref: "todo_regression_gate",
        relation: "successor",
        enforcement: "lineage_only",
      },
      {
        from_todo_id: "todo_runtime_admission",
        to_ref: "todo_per_model_tests",
        relation: "successor",
        enforcement: "lineage_only",
      },
      {
        from_todo_id: "todo_allowlist_policy",
        to_ref: "todo_runtime_admission",
        relation: "successor",
        enforcement: "lineage_only",
      },
      {
        from_todo_id: "todo_facts_source",
        to_ref: "todo_allowlist_policy",
        relation: "successor",
        enforcement: "lineage_only",
      },
    ],
  );
  assert.deepEqual(result?.selection_contract, {
    selected_todo_authority: "$.selected_todo",
    action_choice_authority: "$.action_portfolio",
    horizon_changes_selection: false,
    explicit_selection_required_for_other_work: true,
  });
  assert.equal(
    (result?.completeness as Record<string, unknown>).complete,
    true,
  );
});

test("planning horizon reports every bounded omission instead of claiming completeness", () => {
  const selected = todo("todo_selected001", 9, "P2");
  const candidates = Array.from({ length: 9 }, (_, index) =>
    todo(`todo_context${String(index).padStart(3, "0")}`, index, "P0", {
      successor_todo_ids: index < 8
        ? [`todo_context${String(index + 1).padStart(3, "0")}`]
        : [selected.todo_id],
    })
  );
  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, ...candidates],
    runnable_todo_ids: [selected.todo_id],
    source_context_todo_count: 13,
    acceptance_gaps: [
      { kind: "gap_one", acceptance_summary: "a".repeat(500) },
      { kind: "gap_two", acceptance_summary: "second" },
      { kind: "gap_three", acceptance_summary: "third" },
    ],
  }));
  const completeness = result?.completeness as Record<string, unknown>;

  assert.equal((result?.work_items as unknown[]).length, 5);
  assert.equal((result?.relations as unknown[]).length, 5);
  assert.equal((result?.acceptance_gaps as unknown[]).length, 2);
  assert.equal(completeness.source_unrepresented_todo_count, 3);
  assert.equal(completeness.omitted_candidate_todo_count, 5);
  assert.equal(completeness.omitted_relation_count, 4);
  assert.equal(completeness.omitted_acceptance_gap_count, 1);
  assert.equal(completeness.compact_field_truncation_count, 1);
  assert.equal(completeness.complete, false);
});

test("planning horizon never infers dependency edges from Todo prose", () => {
  const selected = todo("todo_selected001", 2, "P1");
  const proseOnly = todo("todo_prose001", 1, "P0", {
    text: "This prose says it depends on todo_selected001 but carries no typed relation.",
  });
  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, proseOnly],
    runnable_todo_ids: [selected.todo_id, proseOnly.todo_id],
    source_context_todo_count: 2,
  }));

  assert.equal(result, null);
});

test("planning horizon omits flat runnable alternatives already owned by the action portfolio", () => {
  const selected = todo("todo_selected001", 2, "P1");
  const alternatives = Array.from({ length: 8 }, (_, index) =>
    todo(`todo_alternative${index}`, index, "P0")
  );

  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, ...alternatives],
    runnable_todo_ids: [selected.todo_id, ...alternatives.map((item) => item.todo_id)],
    source_context_todo_count: 12,
  }));

  assert.equal(result, null);
});

test("planning horizon keeps waiting context ahead of flat runnable alternatives", () => {
  const selected = todo("todo_selected001", 20, "P1");
  const alternatives = Array.from({ length: 8 }, (_, index) =>
    todo(`todo_alternative${index}`, index, "P0")
  );
  const waiting = todo("todo_waiting001", 19, "P1", {
    status: "deferred",
    resume_when: "pr_merged:owner/repo#123",
  });

  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, ...alternatives, waiting],
    runnable_todo_ids: [selected.todo_id, ...alternatives.map((item) => item.todo_id)],
    source_context_todo_count: 10,
  }));
  const projected = result?.work_items as Array<Record<string, unknown>>;

  assert.equal(projected[0].todo_id, selected.todo_id);
  assert.equal(projected[1].todo_id, waiting.todo_id);
  assert.equal(projected[1].planning_state, "waiting");
  assert.equal(
    (result?.relations as Array<Record<string, unknown>>)[0].relation,
    "resumes_when",
  );
  assert.equal(
    (result?.completeness as Record<string, unknown>).complete,
    false,
  );
});

test("planning horizon preserves claim requirements for unclaimed runnable context", () => {
  const selected = todo("todo_selected001", 3, "P1");
  const unclaimed = todo("todo_unclaimed001", 1, "P0", { claimed_by: undefined });
  const waiting = todo("todo_waiting001", 2, "P1", {
    status: "deferred",
    resume_when: "pr_merged:owner/repo#123",
  });
  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, unclaimed, waiting],
    runnable_todo_ids: [selected.todo_id, unclaimed.todo_id],
    source_context_todo_count: 3,
  }));
  const projected = result?.work_items as Array<Record<string, unknown>>;
  const unclaimedProjection = projected.find((item) => item.todo_id === unclaimed.todo_id);

  assert.equal(unclaimedProjection?.planning_state, "runnable");
  assert.equal(unclaimedProjection?.claim_state, "unclaimed");
  assert.equal(unclaimedProjection?.claim_required_before_work, true);
});

test("opaque route refs cannot invent Todo proximity in the horizon", () => {
  const selected = todo("todo_selected001", 1, "P1", {route_id: "todo_unrelated001"});
  const unrelated = todo("todo_unrelated001", 2, "P0", {status: "deferred"});
  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected, candidates: [selected, unrelated], source_context_todo_count: 2,
  }));
  const item = (result?.work_items as Array<Record<string, unknown>>)
    .find(value => value.todo_id === unrelated.todo_id);
  assert.ok(item);
  assert.equal((item.context_reasons as string[]).includes("related_to_selected"), false);
});

test("planning inventory detail reuses Todo rows without duplicating their payload", () => {
  const selected = todo("todo_selected001", 2, "P1", {
    required_capabilities: ["network"],
    required_write_scopes: ["artifacts/**"],
  });
  const unclaimed = todo("todo_unclaimed001", 1, "P0", {
    claimed_by: undefined,
  });
  const inventory = (request({
    selected_todo: selected,
    candidates: [selected, unclaimed],
    runnable_todo_ids: [selected.todo_id, unclaimed.todo_id],
    source_context_todo_count: 2,
  }).planning_inventory);
  const detail = projectTodoPlanningInventoryDetail(inventory);
  const items = detail.items as Array<Record<string, unknown>>;

  assert.equal(detail.schema_version, TODO_PLANNING_INVENTORY_DETAIL_SCHEMA_VERSION);
  assert.equal(detail.item_detail_ref, "$.agent_todo_summary");
  assert.equal(items[1].claim_state, "unclaimed");
  assert.equal(items[1].claim_required_before_work, true);
  assert.equal("text" in items[0], false);
  assert.equal("required_capabilities" in items[0], false);
  assert.equal("required_write_scopes" in items[0], false);
});

test("planning horizon rejects malformed identities but degrades bounded source overflow", () => {
  assert.throws(
    () => projectQuotaPlanningHorizon(request({
      candidates: [{ todo_id: "not-a-todo", text: "Malformed." }],
    })),
    /public Todo id/,
  );
  const selected = todo("todo_selected001", 40, "P2");
  const monitors = Array.from({ length: 33 }, (_, index) =>
    todo(`todo_overflow${index}`, index, "P2", {
      task_class: "continuous_monitor",
      next_due_at: "2099-01-01T00:00:00Z",
    })
  );
  const result = projectQuotaPlanningHorizon(request({
    selected_todo: selected,
    candidates: [selected, ...monitors],
    runnable_todo_ids: [selected.todo_id],
    source_context_todo_count: 34,
  }));

  assert.equal((result?.work_items as unknown[]).length, 5);
  assert.equal(
    (result?.completeness as Record<string, unknown>).omitted_candidate_todo_count,
    29,
  );
});
