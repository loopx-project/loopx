import assert from "node:assert/strict";
import test from "node:test";

import {
  ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION,
  ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
  QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION,
  projectQuotaActionPortfolio,
  qualifyActionSelection,
  reconcileRetainedActionSelection,
} from "../../loopx/control_plane/work_items/action_portfolio.ts";
import {
  PLANNING_HORIZON_REQUEST_SCHEMA_VERSION,
  projectQuotaPlanningHorizon,
} from "../../loopx/control_plane/work_items/planning_horizon.ts";
import {
  TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION,
  projectTodoPlanningInventory,
  projectTodoPlanningInventoryDetail,
} from "../../loopx/control_plane/work_items/planning_inventory.ts";

function candidate(todoId: string, text: string, priority: string) {
  return {
    todo_id: todoId,
    text,
    priority,
    status: "open",
    task_class: "advancement_task",
    claimed_by: "codex-main",
    required_capabilities: [`capability_${todoId}`],
    required_write_scopes: [`artifacts/${todoId}/**`],
    continuation_hint: `Continue ${todoId} from its latest validated boundary.`,
  };
}

function request(
  primary: Record<string, unknown>,
  candidates: Record<string, unknown>[],
  unavailable: Record<string, unknown>[] = [],
  maxAlternativeActions?: number,
) {
  const sourceItems = [primary, ...candidates, ...unavailable];
  const planningInventoryRequest = {
    schema_version: TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION,
    goal_id: "action-portfolio-fixture",
    agent_id: "codex-main",
    selected_todo: primary,
    source_items: sourceItems,
    runnable_candidates: [primary, ...candidates],
    unavailable_higher_priority: unavailable,
    source_context_todo_count: new Set(sourceItems.map((item) => item.todo_id)).size,
  };
  return {
    schema_version: ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION,
    planning_inventory: projectTodoPlanningInventory(planningInventoryRequest),
    ...(maxAlternativeActions === undefined
      ? {}
      : { max_alternative_actions: maxAlternativeActions }),
  };
}

function planningPacketRequest(
  primary: Record<string, unknown>,
  candidates: Record<string, unknown>[],
  unavailable: Record<string, unknown>[] = [],
  overrides: Record<string, unknown> = {},
) {
  const sourceItems = [primary, ...candidates, ...unavailable];
  return {
    schema_version: QUOTA_PLANNING_PACKET_REQUEST_SCHEMA_VERSION,
    planning_inventory_request: {
      schema_version: TODO_PLANNING_INVENTORY_REQUEST_SCHEMA_VERSION,
      goal_id: "action-portfolio-fixture",
      agent_id: "codex-main",
      selected_todo: primary,
      source_items: sourceItems,
      runnable_candidates: [primary, ...candidates],
      unavailable_higher_priority: unavailable,
      source_context_todo_count: new Set(sourceItems.map((item) => item.todo_id)).size,
    },
    projection_enabled: true,
    include_detail: true,
    acceptance_gaps: [],
    ...overrides,
  };
}

test("planning packet composes every requested lens from one inventory", () => {
  const primary = candidate("todo_primary001", "Run the primary slice.", "P0");
  const alternative = candidate(
    "todo_fallback001",
    "Run the fallback slice.",
    "P1",
  );
  const packetRequest = planningPacketRequest(primary, [alternative], [], {
    acceptance_gaps: [{
      kind: "vision_acceptance_gap",
      acceptance_summary: "Prove the aggregate interface before closeout.",
    }],
  });
  const planningInventoryRequest = packetRequest.planning_inventory_request;
  const inventory = projectTodoPlanningInventory(planningInventoryRequest);
  const portfolio = projectQuotaActionPortfolio({
    schema_version: ACTION_PORTFOLIO_REQUEST_SCHEMA_VERSION,
    planning_inventory: inventory,
    max_alternative_actions: 2,
  });
  const horizon = projectQuotaPlanningHorizon({
    schema_version: PLANNING_HORIZON_REQUEST_SCHEMA_VERSION,
    planning_inventory: inventory,
    acceptance_gaps: packetRequest.acceptance_gaps,
  });

  assert.deepEqual(projectQuotaActionPortfolio(packetRequest), {
    schema_version: "quota_planning_packet_v0",
    action_portfolio: portfolio,
    planning_horizon: horizon,
    agent_todo_planning_inventory: projectTodoPlanningInventoryDetail(inventory),
  });
});

test("planning packet gates optional lenses without leaking its inventory", () => {
  const primary = candidate("todo_primary001", "Run the only slice.", "P0");
  const result = projectQuotaActionPortfolio(planningPacketRequest(
    primary,
    [],
    [],
    { projection_enabled: false, include_detail: true },
  ));

  assert.equal(result?.schema_version, "quota_planning_packet_v0");
  assert.equal("planning_inventory" in (result ?? {}), false);
  assert.equal("action_portfolio" in (result ?? {}), false);
  assert.equal("planning_horizon" in (result ?? {}), false);
  assert.equal("agent_todo_planning_inventory" in (result ?? {}), true);
});

test("planning packet rejects malformed projection gates", () => {
  const primary = candidate("todo_primary001", "Run the only slice.", "P0");
  assert.throws(
    () => projectQuotaActionPortfolio(planningPacketRequest(
      primary,
      [],
      [],
      { projection_enabled: "yes" },
    )),
    /projection_enabled must be a boolean/,
  );
  assert.throws(
    () => projectQuotaActionPortfolio(planningPacketRequest(
      primary,
      [],
      [],
      { acceptance_gaps: "not-an-array" },
    )),
    /acceptance_gaps must be an array/,
  );
});

test("planning packet preserves bounded horizon completeness", () => {
  const primary = candidate("todo_primary001", "Run the primary slice.", "P0");
  const monitors = Array.from({ length: 33 }, (_, index) => ({
    ...candidate(
      `todo_monitor${String(index).padStart(3, "0")}`,
      `Observe monitor ${index}.`,
      "P1",
    ),
    task_class: "continuous_monitor",
    next_due_at: "2099-01-01T00:00:00Z",
  }));
  const packetRequest = planningPacketRequest(primary, []);
  packetRequest.planning_inventory_request.source_items = [primary, ...monitors];
  packetRequest.planning_inventory_request.runnable_candidates = [primary];
  packetRequest.planning_inventory_request.source_context_todo_count = 34;

  const result = projectQuotaActionPortfolio(packetRequest);
  const horizon = result?.planning_horizon as Record<string, unknown>;
  const completeness = horizon.completeness as Record<string, unknown>;

  assert.equal((horizon.work_items as unknown[]).length, 5);
  assert.equal(completeness.omitted_candidate_todo_count, 29);
  assert.equal(completeness.complete, false);
});

test("unbound hard replans expose planning choices even for a sole candidate", () => {
  const primary = candidate("todo_primary001", "Review the primary slice.", "P1");
  for (const alternatives of [[], [candidate("todo_other001", "Another slice.", "P2")]]) {
    const result = projectQuotaActionPortfolio(planningPacketRequest(primary, alternatives, [], {
      projection_enabled: false,
      include_detail: false,
      replan_selection_context: {
        has_turn_identity: true, should_run: true, receipt_bound: false, selection_requested: false, monitor_only: false,
      },
    }));
    const portfolio = result?.action_portfolio as Record<string, unknown>;
    const policy = portfolio.selection_policy as Record<string, unknown>;
    assert.equal(policy.requires_explicit_turn_binding, true);
    assert.equal(policy.recommendation_role, "default_not_binding");
    assert.equal(policy.direct_delivery_before_selection, false);
    assert.equal("agent_todo_planning_inventory" in (result ?? {}), false);
  }
});

test("replan planning does not unbind receipts or override delivery and monitor gates", () => {
  const primary = candidate("todo_primary001", "Review the primary slice.", "P1");
  for (const override of [
    { has_turn_identity: false },
    { should_run: false }, { receipt_bound: true },
    { selection_requested: true }, { monitor_only: true },
  ]) {
    const result = projectQuotaActionPortfolio(planningPacketRequest(primary, [], [], {
      projection_enabled: false,
      include_detail: false,
      replan_selection_context: {
        has_turn_identity: true, should_run: true, receipt_bound: false, selection_requested: false, monitor_only: false,
        ...override,
      },
    }));
    assert.equal("action_portfolio" in (result ?? {}), false);
  }
});

test("every supplied replan planning fact is validated without short-circuiting", () => {
  const primary = candidate("todo_primary001", "Review the primary slice.", "P1");
  for (const field of ["has_turn_identity", "should_run", "receipt_bound", "selection_requested", "monitor_only"]) {
    assert.throws(() => projectQuotaActionPortfolio(planningPacketRequest(primary, [], [], {
      replan_selection_context: {
        has_turn_identity: false, should_run: false, receipt_bound: true, selection_requested: true, monitor_only: true,
        [field]: "yes",
      },
    })), new RegExp(`replan_selection_context.${field} must be a boolean`));
  }
});

test("action portfolio exposes one recommendation and bounded selectable alternatives", () => {
  const primary = candidate("todo_primary001", "Run the primary slice.", "P0");
  const result = projectQuotaActionPortfolio(request(
    primary,
    [
      primary,
      candidate("todo_fallback001", "Run fallback one.", "P1"),
      candidate("todo_fallback001", "Duplicate fallback one.", "P1"),
      candidate("todo_fallback002", "Run fallback two.", "P2"),
      candidate("todo_fallback003", "Run fallback three.", "P3"),
    ],
    [],
    2,
  ));

  assert.equal(result?.schema_version, "quota_action_portfolio_v2");
  assert.deepEqual(result?.selection_policy, {
    decision_owner: "agent",
    mode: "explicit_turn_binding",
    recommendation_role: "default_not_binding",
    requires_explicit_turn_binding: true,
    direct_delivery_before_selection: false,
    max_alternative_actions: 2,
    candidate_scope: "current_authoritative_eligible_todos",
    suggestions_exhaustive: false,
  });
  assert.deepEqual(result?.suggested_actions, [
    {
      todo_id: "todo_primary001",
      text: "Run the primary slice.",
      priority: "P0",
      required_capabilities: ["capability_todo_primary001"],
      required_write_scopes: ["artifacts/todo_primary001/**"],
      continuation_hint: "Continue todo_primary001 from its latest validated boundary.",
      selection_role: "recommended",
    },
    {
      todo_id: "todo_fallback001",
      text: "Run fallback one.",
      priority: "P1",
      required_capabilities: ["capability_todo_fallback001"],
      required_write_scopes: ["artifacts/todo_fallback001/**"],
      continuation_hint: "Continue todo_fallback001 from its latest validated boundary.",
      selection_role: "alternative",
    },
    {
      todo_id: "todo_fallback002",
      text: "Run fallback two.",
      priority: "P2",
      required_capabilities: ["capability_todo_fallback002"],
      required_write_scopes: ["artifacts/todo_fallback002/**"],
      continuation_hint: "Continue todo_fallback002 from its latest validated boundary.",
      selection_role: "alternative",
    },
  ]);
});

test("future or blocked higher priority work keeps the portfolio visible", () => {
  const primary = candidate("todo_fallback001", "Run the ready fallback.", "P1");
  const result = projectQuotaActionPortfolio(request(primary, [], [
      {
        ...candidate("todo_monitor001", "Poll at the next window.", "P0"),
        task_class: "continuous_monitor",
        availability_reason: "scheduled_for_future",
        next_due_at: "2099-01-01T00:00:00Z",
      },
    ]));

  assert.equal(
    (result?.unavailable_higher_priority as Array<Record<string, unknown>>)[0]
      .availability_reason,
    "scheduled_for_future",
  );
  assert.equal(
    (result?.selection_policy as Record<string, unknown>)
      .requires_explicit_turn_binding,
    false,
  );
});

test("a single primary needs no redundant portfolio", () => {
  const primary = candidate("todo_primary001", "Run the only slice.", "P0");
  assert.equal(projectQuotaActionPortfolio(request(primary, [])), null);
});

test("an unclaimed selected action preserves the claim-before-work boundary", () => {
  const primary = {
    ...candidate("todo_primary001", "Run the unclaimed primary slice.", "P0"),
    claimed_by: undefined,
  };
  const result = projectQuotaActionPortfolio(request(primary, [
    candidate("todo_fallback001", "Run the claimed fallback.", "P1"),
  ]));
  const suggestions = result?.suggested_actions as Array<Record<string, unknown>>;

  assert.equal(suggestions[0].selection_role, "recommended");
  assert.equal(suggestions[0].claim_required_before_work, true);
  assert.equal("claim_required_before_work" in suggestions[1], false);
});

test("malformed candidates fail closed at the typed boundary", () => {
  assert.throws(
    () => projectQuotaActionPortfolio(request(
      candidate("todo_primary001", "Run the primary slice.", "P0"),
      [{ text: "missing typed identity" }],
    )),
    /todo_id/,
  );
  assert.throws(
    () => projectQuotaActionPortfolio(request(
      candidate("todo_primary001", "Run the primary slice.", "P0"),
      [
        {
          ...candidate("todo_blocked001", "Blocked work.", "P1"),
          status: "blocked",
        },
      ],
    )),
    /status must be open/,
  );
  assert.throws(
    () => projectQuotaActionPortfolio(request(
      candidate("todo_primary001", "Run the primary slice.", "P0"),
      [
        {
          ...candidate("todo_contract001", "Malformed contract.", "P1"),
          required_write_scopes: "artifacts/**",
        },
      ],
    )),
    /required_write_scopes must be an array of strings/,
  );
});

test("pending selection qualifies only after current hard-lane arbitration", () => {
  const successor = candidate(
    "todo_successor001",
    "Run the visible successor slice.",
    "P1",
  );
  const qualified = qualifyActionSelection({
    schema_version: ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
    requested_todo_id: successor.todo_id,
    candidate: successor,
    should_run: true,
    normal_delivery_allowed: true,
    delivery_preemptions: [],
  });
  assert.equal(qualified.state, "qualified");
  assert.deepEqual(qualified.selected_todo, {
    todo_id: successor.todo_id,
    text: successor.text,
    priority: successor.priority,
    required_capabilities: successor.required_capabilities,
    required_write_scopes: successor.required_write_scopes,
    continuation_hint: successor.continuation_hint,
    selection_binding: "pending_action_selection",
  });

  const deferred = qualifyActionSelection({
    schema_version: ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
    requested_todo_id: successor.todo_id,
    candidate: successor,
    should_run: true,
    normal_delivery_allowed: true,
    delivery_preemptions: ["blocking_work_lane"],
  });
  assert.deepEqual(deferred, {
    schema_version: "action_selection_qualification_v0",
    state: "deferred",
    recovery_action: "reenter_guard_without_selection",
    requested_todo_id: successor.todo_id,
    reason: "blocking_work_lane",
    delivery_preemptions: ["blocking_work_lane"],
  });
});

test("only runnable replan-only deferral permits one inline guard reentry", () => {
  const selected = candidate("todo_selected001", "Review the selected route.", "P1");
  const request = {
    schema_version: ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
    requested_todo_id: selected.todo_id,
    candidate: selected,
    should_run: true,
    normal_delivery_allowed: false,
    delivery_preemptions: ["autonomous_replan", "delivery_not_allowed"],
  };
  const replan = qualifyActionSelection(request);
  assert.equal(replan.state, "deferred");
  assert.equal(replan.inline_reentry_allowed, true);
  assert.equal(replan.selected_todo, undefined);
  for (const extra of ["blocking_work_lane", "control_repair", "heartbeat_receipt",
    "ready_deferred_successor_priority_preemption"]) {
    assert.equal(qualifyActionSelection({...request,
      delivery_preemptions: [...request.delivery_preemptions, extra],
    }).inline_reentry_allowed, undefined);
  }
  for (const patch of [
    {should_run: false}, {candidate: null}, {delivery_preemptions: []},
    {delivery_preemptions: ["delivery_not_allowed"]},
    {normal_delivery_allowed: true},
  ]) {
    assert.equal(qualifyActionSelection({...request, ...patch}).inline_reentry_allowed, undefined);
  }
});

test("pending selection rejects a Todo absent from the current eligible set", () => {
  assert.deepEqual(qualifyActionSelection({
    schema_version: ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
    requested_todo_id: "todo_missing001",
    candidate: null,
    should_run: true,
    normal_delivery_allowed: true,
    delivery_preemptions: [],
  }), {
    schema_version: "action_selection_qualification_v0",
    state: "rejected",
    recovery_action: "reenter_guard_without_selection",
    requested_todo_id: "todo_missing001",
    reason: "candidate_not_currently_eligible",
  });
});

test("pending selection explains an auxiliary monitor outside the advancement lane", () => {
  assert.deepEqual(qualifyActionSelection({
    schema_version: ACTION_SELECTION_QUALIFICATION_REQUEST_SCHEMA_VERSION,
    requested_todo_id: "todo_monitor001",
    candidate: null,
    requested_task_class: "continuous_monitor",
    should_run: true,
    normal_delivery_allowed: true,
    delivery_preemptions: [],
  }), {
    schema_version: "action_selection_qualification_v0",
    state: "rejected",
    recovery_action: "reenter_guard_without_selection",
    requested_todo_id: "todo_monitor001",
    reason: "auxiliary_monitor_not_selectable_in_advancement_lane",
  });
});

test("retained explicit selection cannot be replaced by a projected default", () => {
  const base = {
    schema_version: "retained_action_selection_reentry_request_v0",
    retained_todo_id: "todo_explicit001",
    effective_action: "autonomous_replan_required",
    replan_obligation_id: "replan-0123456789abcdef",
  };

  assert.deepEqual(reconcileRetainedActionSelection({
    ...base,
    projected_todo_id: "todo_explicit001",
  }), {
    schema_version: "retained_action_selection_reentry_v0",
    disposition: "preserve_retained_todo",
    retained_todo_id: "todo_explicit001",
    projected_todo_id: "todo_explicit001",
    clear_fields: ["action_portfolio"],
  });

  assert.deepEqual(reconcileRetainedActionSelection({
    ...base,
    projected_todo_id: "todo_recommended001",
  }), {
    schema_version: "retained_action_selection_reentry_v0",
    disposition: "bind_autonomous_replan",
    retained_todo_id: "todo_explicit001",
    projected_todo_id: "todo_recommended001",
    replan_obligation_id: "replan-0123456789abcdef",
    continuation: "fresh_turn_after_replan_closeout",
    clear_fields: ["action_portfolio"],
  });

  assert.deepEqual(reconcileRetainedActionSelection({
    ...base,
    effective_action: "normal_run",
    replan_obligation_id: null,
    projected_todo_id: "todo_recommended001",
  }), {
    schema_version: "retained_action_selection_reentry_v0",
    disposition: "require_explicit_selection",
    retained_todo_id: "todo_explicit001",
    projected_todo_id: "todo_recommended001",
    reason: "projected_default_differs_from_retained_explicit_choice",
    projection: {
      decision_patch: {
        ok: false,
        decision: "skip",
        should_run: false,
        effective_action: "quota_skip",
        normal_delivery_allowed: false,
        recovery_delivery_allowed: false,
        self_repair_allowed: false,
        state: "action_selection_required",
        reason:
          "the current projected default differs from the explicit Todo retained by this Turn",
        recommended_action:
          "rerun quota should-run with the same --turn-instance-id and an explicit eligible --todo-id",
      },
      execution_obligation_patch: {
        must_attempt_work: false,
        delivery_allowed: false,
        reason:
          "rerun quota should-run with the same --turn-instance-id and an explicit eligible --todo-id",
      },
      clear_fields: ["selected_todo", "todo_id", "agent_lane_next_action"],
    },
  });
});
