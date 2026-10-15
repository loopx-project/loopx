import { z } from "zod";

import {
  parseStatusPayload,
  todoItemSchema,
  todoGroupSchema,
  projectAssetTodoSummarySchema,
  todoIndexItemSchema,
  periodicReportIndexItemSchema,
  periodicReportIndexResponseSchema,
} from "../src/data/status";
import { mergeScopedStatusProjections } from "../src/data/status-merge";
import {
  beginStatusRequest,
  createStatusRequestFence,
  statusRequestCanCommit,
  statusRequestIsCurrent,
} from "../src/data/status-request-fence";

function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function equal(actual: unknown, expected: unknown, message: string) {
  assert(Object.is(actual, expected), `${message}: expected ${String(expected)}, received ${String(actual)}`);
}

function basePayload(overrides: Record<string, unknown> = {}): ReturnType<typeof parseStatusPayload> {
  return parseStatusPayload({
    ok: true,
    registry: "registry.json",
    runtime_root: "fixture-runtime",
    goal_count: 0,
    run_count: 0,
    status_contract: {
      schema_version: 0,
      minimum_dashboard_schema_version: 2,
    },
    contract: {
      ok: true,
      summary: { errors: 0, warnings: 0, checks: 0 },
      errors: [],
      warnings: [],
      checks: [],
    },
    attention_queue: {
      available: true,
      item_count: 0,
      needs_user_or_controller: 0,
      needs_controller: 0,
      needs_codex: 0,
      watching_external_evidence: 0,
      items: [],
    },
    run_history: {
      available: true,
      goal_count: 0,
      run_count: 0,
      goals: [],
      recent_runs: [],
    },
    local_dashboard_api: {
      source: "serve-status",
    },
    ...overrides,
  });
}

function goal(id: string, activation: "active" | "stopped") {
  return {
    id,
    activation_state: activation,
    display_name: id,
    registry_member: true,
    legacy_runtime_goal: false,
    index_exists: true,
    raw_index_records: 1,
    unique_runs: 1,
    latest_runs: [],
  };
}

// Host delivery-window diagnostics must survive the public status decoder so
// the personal-workspace view can distinguish observed staleness from absence.
const deliveryWindowStatus = basePayload({
  run_history: {
    available: true,
    goal_count: 1,
    run_count: 0,
    goals: [{
      ...goal("window", "active"),
      host_thread_activity: {
        completeness: "complete",
        threads: [{
          agent_id: "codex",
          host_surface: "codex-app",
          state: "idle",
          last_event_at: "2026-10-14T18:00:00Z",
          delivery_window: {
            schema_version: "loopx_host_delivery_window_v0",
            state: "stale",
            last_observed_at: "2026-10-14T18:00:00Z",
            age_hours: 2.5,
            expected_interval_minutes: 30,
            window_minutes: 60,
            tolerance: 2,
            source: "installed_automation",
          },
        }],
      },
    }],
    recent_runs: [],
  },
});
equal(deliveryWindowStatus.run_history.goals[0].host_thread_activity?.threads[0].delivery_window?.state,
  "stale", "host activity window state survives status parsing");
equal(deliveryWindowStatus.run_history.goals[0].host_thread_activity?.threads[0].delivery_window?.age_hours,
  2.5, "host activity window age survives status parsing");

// A status source may omit totals while still returning a useful bounded list.
// Preserve that absence before the workspace adapter and typed digest see it.
for (const [schema, field] of [[todoGroupSchema, "done_count"], [projectAssetTodoSummarySchema, "done"]] as const) {
  const missing = schema.parse({ items: [] });
  equal((missing as Record<string, unknown>)[field], undefined, "Missing completion total stays unknown");
  const measured = schema.parse({ items: [], [field]: 0 });
  equal((measured as Record<string, unknown>)[field], 0, "Measured zero is retained");
}

// Native Todo identity is the stable todo_id; a Markdown source index is only
// a legacy display coordinate. Both forms must survive a full status parse.
const nativeTodo = { done: false, text: "Review public evidence", todo_id: "todo_native" };
const nativeStatus = basePayload({
  attention_queue: {
    available: true, item_count: 1, needs_user_or_controller: 0,
    needs_controller: 0, needs_codex: 1, watching_external_evidence: 0,
    items: [{ goal_id: "native", status: "running", waiting_on: "codex",
      severity: "normal", recommended_action: "continue",
      agent_todos: { items: [nativeTodo] },
      project_asset: { owner: "agent", gate: "none", next_action: "review",
        stop_condition: "accepted", agent_todos: {
          items: [{ ...nativeTodo, index: null }],
          recent_completed_advancement_items: [{ ...nativeTodo, todo_id: "todo_done", done: true }],
        } },
    }],
  },
  todo_index: { items: [{ ...nativeTodo, index: null, goal_id: "native" }] },
});
equal(nativeStatus.attention_queue.items[0].agent_todos?.items[0].index, undefined,
  "native Todo without a source index remains addressable");
equal(nativeStatus.todo_index?.items[0].index, null,
  "Todo index readback preserves the explicitly absent source coordinate");
assert(todoItemSchema.safeParse({ index: 3, done: false, text: "Legacy Todo" }).success,
  "legacy source-index Todo remains valid without a stable id");
assert(!todoItemSchema.safeParse({ done: false, text: "Anonymous Todo" }).success,
  "missing both source index and stable id is still invalid");
assert(!todoIndexItemSchema.safeParse({ goal_id: "native", index: null, done: false, text: "Anonymous Todo" }).success,
  "Todo index must retain the same identity guard");
assert(!todoItemSchema.safeParse({ ...nativeTodo, index: "3" }).success,
  "non-numeric source coordinates remain invalid");

const activePayload = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "active",
    complete: false,
    projected_goal_count: 2,
    registry_goal_count: 3,
    registry_revision: "registry_activation_v1:active-rev",
  },
  run_history: {
    available: true,
    goal_count: 2,
    run_count: 2,
    goals: [goal("alpha", "active"), goal("beta", "active")],
    recent_runs: [],
  },
  attention_queue: {
    available: true,
    item_count: 1,
    needs_user_or_controller: 0,
    needs_controller: 0,
    needs_codex: 1,
    watching_external_evidence: 0,
    items: [{
      goal_id: "alpha",
      activation_state: "active",
      status: "running",
      waiting_on: "codex",
      severity: "normal",
      recommended_action: "continue",
    }],
  },
});

const stoppedPayload = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "stopped",
    complete: false,
    projected_goal_count: 1,
    registry_goal_count: 3,
    registry_revision: "registry_activation_v1:active-rev",
  },
  run_history: {
    available: true,
    goal_count: 1,
    run_count: 1,
    goals: [goal("gamma", "stopped")],
    recent_runs: [],
  },
  attention_queue: {
    available: true,
    item_count: 1,
    needs_user_or_controller: 1,
    needs_controller: 1,
    needs_codex: 0,
    watching_external_evidence: 0,
    items: [{
      goal_id: "gamma",
      activation_state: "stopped",
      status: "blocked",
      waiting_on: "controller",
      severity: "high",
      recommended_action: "review",
    }],
  },
  event_ledger_summary: {
    available: true,
    source: "run_history",
    sample_run_count: 1,
    totals: {
      events_24h: 1,
      events_7d: 1,
      by_class_24h: { accounting: 0, decision: 0, evidence: 0, state: 0, work: 1 },
      by_class_7d: { accounting: 0, decision: 0, evidence: 0, state: 0, work: 1 },
    },
    goals: [{
      goal_id: "gamma",
      events_24h: 1,
      events_7d: 1,
      by_class_24h: { accounting: 0, decision: 0, evidence: 0, state: 0, work: 1 },
      by_class_7d: { accounting: 0, decision: 0, evidence: 0, state: 0, work: 1 },
      latest_event_class: "work",
      latest_event_at: "2026-09-04T00:00:00Z",
    }],
  },
  todo_index: {
    schema_version: "todo_index_v1",
    source: "attention_queue_and_rollout_event_log",
    total_count: 1,
    current_projected_count: 1,
    rollout_event_count: 0,
    item_limit: 32,
    items: [{
      goal_id: "gamma",
      index: 0,
      done: false,
      text: "release gate",
      todo_id: "gamma:todo:1",
      role: "user",
    }],
  },
  usage_summary: {
    available: true,
    source: "run_history",
    sample_run_count: 1,
    totals: {
      runs_24h: 1,
      runs_7d: 1,
      quota_spend_slots_24h: 0,
      quota_spend_slots_7d: 0,
      automation_run_count_24h: 0,
      automation_run_count_7d: 0,
      progress_signal_run_count_24h: 0,
      progress_signal_run_count_7d: 0,
      input_tokens_24h: 10,
      input_tokens_7d: 10,
      output_tokens_24h: 5,
      output_tokens_7d: 5,
      cache_tokens_24h: 0,
      cache_tokens_7d: 0,
      cost_usd_24h: 0.01,
      cost_usd_7d: 0.01,
      duration_ms_24h: 1000,
      duration_ms_7d: 1000,
    },
    goals: [{
      goal_id: "gamma",
      runs_24h: 1,
      runs_7d: 1,
      quota_spend_slots_24h: 0,
      quota_spend_slots_7d: 0,
      automation_run_count_24h: 0,
      automation_run_count_7d: 0,
      progress_signal_run_count_24h: 0,
      progress_signal_run_count_7d: 0,
      input_tokens_24h: 10,
      input_tokens_7d: 10,
      output_tokens_24h: 5,
      output_tokens_7d: 5,
      cache_tokens_24h: 0,
      cache_tokens_7d: 0,
      cost_usd_24h: 0.01,
      cost_usd_7d: 0.01,
      duration_ms_24h: 1000,
      duration_ms_7d: 1000,
      project_share_24h: 1,
    }],
  },
  agent_management_projection: {
    schema_version: "agent_management_projection_v1",
    agents: [{
      agent_id: "codex",
      role: "primary",
      state: "active",
      goal_ids: ["gamma"],
    }],
  },
  goal_channel_notification_projection: {
    schema_version: "goal_channel_notification_v1",
    goals: [{
      goal_id: "gamma",
      configured: true,
      enabled: true,
      human_gate_auto_notify_enabled: false,
      receipt_count: 1,
    }],
  },
});

// 1) stopped archive merge preserves every goal-scoped derived projection.
const merged = mergeScopedStatusProjections(activePayload, stoppedPayload);
equal(merged.goal_projection?.scope, "all", "merged projection is unscoped all");
equal(merged.goal_projection?.complete, true, "matching registry revisions keep complete true");
equal(merged.run_history.goals.length, 3, "active and stopped goals are all present");
equal(merged.run_history.goals[0].id, "alpha", "active goals stay first");
equal(merged.run_history.goals[2].id, "gamma", "stopped goals fill in after active");
equal(merged.attention_queue.items.length, 2, "attention queue keeps both active and stopped items");
equal(merged.todo_index?.items.length, 1, "stopped todo index is not dropped");
equal(merged.usage_summary?.goals.length, 1, "stopped usage summary is not dropped");
equal(merged.agent_management_projection?.agents.length, 1, "stopped agent management is not dropped");
equal(merged.goal_channel_notification_projection?.goals.length, 1, "stopped goal channel notifications are not dropped");
equal(merged.event_ledger_summary?.goals.length, 1, "stopped event ledger context is not dropped");
equal(merged.attention_queue.needs_controller, 1, "attention counters are recomputed over merged items");

// 2) matching-revision merge dedupes overlapping goals by id.
const overlappingStopped = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "stopped",
    complete: false,
    projected_goal_count: 2,
    registry_goal_count: 3,
    registry_revision: "registry_activation_v1:active-rev",
  },
  run_history: {
    available: true,
    goal_count: 2,
    run_count: 2,
    goals: [goal("alpha", "stopped"), goal("gamma", "stopped")],
    recent_runs: [],
  },
  attention_queue: {
    available: true,
    item_count: 0,
    needs_user_or_controller: 0,
    needs_controller: 0,
    needs_codex: 0,
    watching_external_evidence: 0,
    items: [],
  },
});
const deduped = mergeScopedStatusProjections(activePayload, overlappingStopped);
equal(deduped.run_history.goals.length, 3, "overlapping goal ids are deduplicated");
equal(deduped.run_history.goals.filter((g) => g.id === "alpha").length, 1, "a goal appears at most once after merge");

// 3) a newer active refresh replaces old active rows while preserving stopped rows.
const refreshedActive = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "active",
    complete: false,
    projected_goal_count: 1,
    registry_goal_count: 2,
    registry_revision: "registry_activation_v1:active-rev",
  },
  run_history: {
    available: true,
    goal_count: 1,
    run_count: 1,
    goals: [goal("beta", "active")],
    recent_runs: [],
  },
  attention_queue: {
    available: true,
    item_count: 1,
    needs_user_or_controller: 0,
    needs_controller: 0,
    needs_codex: 1,
    watching_external_evidence: 0,
    items: [{
      goal_id: "beta",
      activation_state: "active",
      status: "running",
      waiting_on: "codex",
      severity: "normal",
      recommended_action: "continue beta",
    }],
  },
});
const refreshed = mergeScopedStatusProjections(merged, refreshedActive);
equal(refreshed.attention_queue.items.length, 2, "new active plus preserved stopped queue rows remain");
assert(!refreshed.attention_queue.items.some((item) => item.goal_id === "alpha"), "stale active queue row is removed");
assert(refreshed.attention_queue.items.some((item) => item.goal_id === "gamma"), "stopped queue row is preserved");

// 4) registry revision mismatch keeps the projection incomplete so the UI resyncs.
const staleStopped = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "stopped",
    complete: false,
    projected_goal_count: 1,
    registry_goal_count: 3,
    registry_revision: "registry_activation_v1:other-rev",
  },
  run_history: {
    available: true,
    goal_count: 1,
    run_count: 1,
    goals: [goal("gamma", "stopped")],
    recent_runs: [],
  },
  attention_queue: {
    available: true,
    item_count: 0,
    needs_user_or_controller: 0,
    needs_controller: 0,
    needs_codex: 0,
    watching_external_evidence: 0,
    items: [],
  },
});
const mismatched = mergeScopedStatusProjections(activePayload, staleStopped);
equal(mismatched.goal_projection?.complete, false, "revision mismatch keeps complete false for resync");

// 5) legacy full payload (no goal_projection) falls back unchanged.
const legacyPayload = basePayload({ run_history: { available: true, goal_count: 1, run_count: 1, goals: [goal("zeta", "active")], recent_runs: [] } });
const legacyMerged = mergeScopedStatusProjections(activePayload, legacyPayload);
equal(legacyMerged.run_history.goals[0].id, "zeta", "legacy full payload replaces the scoped view");

const preRevisionStopped = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "stopped",
    complete: false,
    projected_goal_count: 1,
    registry_goal_count: 3,
  },
  run_history: {
    available: true,
    goal_count: 1,
    run_count: 1,
    goals: [goal("gamma", "stopped")],
    recent_runs: [],
  },
});
equal(
  mergeScopedStatusProjections(activePayload, preRevisionStopped).goal_projection?.complete,
  true,
  "a scoped server without revision remains compatible",
);

// 6) usage metrics omitted by the producer remain unknown instead of being
// normalized into measured zeroes. Run and quota counters retain their existing
// zero semantics because they describe the sampled history, not measurement.
const usageWithNoMeasurements = basePayload({
  usage_summary: {
    totals: { runs_24h: 0, runs_7d: 0, quota_spend_slots_24h: 0, quota_spend_slots_7d: 0 },
    goals: [],
  },
});
equal(usageWithNoMeasurements.usage_summary?.totals.input_tokens_24h, undefined, "missing token count stays unknown");
equal(usageWithNoMeasurements.usage_summary?.totals.cost_usd_24h, undefined, "missing cost stays unknown");
equal(usageWithNoMeasurements.usage_summary?.totals.runs_24h, 0, "missing run count remains a measured zero");

const activeUsageWithoutTokens = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "active",
    complete: false,
    projected_goal_count: 1,
    registry_goal_count: 2,
    registry_revision: "registry_activation_v1:usage-rev",
  },
  run_history: { available: true, goal_count: 1, run_count: 1, goals: [goal("alpha", "active")], recent_runs: [] },
  usage_summary: {
    totals: { runs_24h: 1, runs_7d: 1, quota_spend_slots_24h: 0, quota_spend_slots_7d: 0 },
    goals: [{ goal_id: "alpha", runs_24h: 1, runs_7d: 1, quota_spend_slots_24h: 0, quota_spend_slots_7d: 0 }],
  },
});
const stoppedUsageWithTokens = basePayload({
  goal_projection: {
    schema_version: "loopx_goal_projection_scope_v0",
    scope: "stopped",
    complete: false,
    projected_goal_count: 1,
    registry_goal_count: 2,
    registry_revision: "registry_activation_v1:usage-rev",
  },
  run_history: { available: true, goal_count: 1, run_count: 1, goals: [goal("gamma", "stopped")], recent_runs: [] },
  usage_summary: {
    totals: { runs_24h: 1, runs_7d: 1, quota_spend_slots_24h: 0, quota_spend_slots_7d: 0, input_tokens_24h: 10, input_tokens_7d: 10, cache_tokens_24h: 0 },
    goals: [{ goal_id: "gamma", runs_24h: 1, runs_7d: 1, quota_spend_slots_24h: 0, quota_spend_slots_7d: 0, input_tokens_24h: 10, input_tokens_7d: 10, cache_tokens_24h: 0 }],
  },
});
const mergedObservedUsage = mergeScopedStatusProjections(activeUsageWithoutTokens, stoppedUsageWithTokens);
equal(mergedObservedUsage.usage_summary?.totals.input_tokens_24h, 10, "merge sums available token measurements");
equal(mergedObservedUsage.usage_summary?.totals.cache_tokens_24h, 0, "merge preserves an observed zero measurement");
equal(mergedObservedUsage.usage_summary?.totals.cost_usd_24h, undefined, "merge retains unknown when neither scope measured cost");
equal(mergedObservedUsage.usage_summary?.totals.runs_24h, 2, "merge retains run counter semantics");

// 7) fence: overlapping same-source background requests must be latest-wins.
const url = "/status.json";
const fence = createStatusRequestFence(null);
fence.loadedUrl = url;
const bg1 = beginStatusRequest(fence, url, { background: true });
assert(bg1 !== null, "first background request starts");
const bg2 = beginStatusRequest(fence, url, { background: true });
assert(bg2 !== null, "second background request starts");
assert(bg1.generation !== bg2.generation, "each started request gets a unique generation");
assert(!statusRequestCanCommit(fence, bg1), "older background request cannot commit once a newer one started");
assert(statusRequestCanCommit(fence, bg2), "newer background request can commit");
assert(statusRequestIsCurrent(fence, bg2), "newer background request is current");
assert(!statusRequestIsCurrent(fence, bg1), "older background request is not current");

// 7) foreground request invalidates earlier background.
const fg = beginStatusRequest(fence, url, { background: false });
assert(fg !== null, "foreground request starts");
assert(!statusRequestCanCommit(fence, bg2), "background request cannot commit after foreground started");
assert(statusRequestCanCommit(fence, fg), "foreground request can commit");

// 8) periodic-report index remains compatible across staggered app/server upgrades.
const periodicReportItem = {
  goal_id: "synthetic-goal",
  agent_id: "synthetic-agent",
  generation_id: "generation-one",
  publication_id: "publication-one",
  delivered_at: "2026-09-06T00:00:00Z",
  detail_ref: {
    goal_id: "synthetic-goal",
    agent_id: "synthetic-agent",
    generation_id: "generation-one",
    content_sha256: `sha256:${"1".repeat(64)}`,
  },
};
const legacyPeriodicReportIndexResponseSchema = z.object({
  ok: z.literal(true),
  periodic_reports: z.object({
    schema_version: z.literal("periodic_report_workspace_index_v0"),
    count: z.number().int().nonnegative(),
    items: z.array(periodicReportIndexItemSchema),
  }).strict(),
}).strict();
const legacyIndexResponse = {
  ok: true as const,
  periodic_reports: {
    schema_version: "periodic_report_workspace_index_v0" as const,
    count: 1,
    items: [periodicReportItem],
  },
};
const newServerLegacyIndexResponse = {
  ...legacyIndexResponse,
  periodic_reports: { ...legacyIndexResponse.periodic_reports },
};
const windowedIndexResponse = {
  ok: true as const,
  periodic_reports: {
    ...legacyIndexResponse.periodic_reports,
    returned_count: 1,
    total_count: 1,
    limit: 100,
    offset: 0,
    truncated: false,
  },
};
legacyPeriodicReportIndexResponseSchema.parse(legacyIndexResponse);
periodicReportIndexResponseSchema.parse(legacyIndexResponse);
legacyPeriodicReportIndexResponseSchema.parse(newServerLegacyIndexResponse);
periodicReportIndexResponseSchema.parse(windowedIndexResponse);
equal(
  periodicReportIndexResponseSchema.parse(legacyIndexResponse).periodic_reports.total_count,
  1,
  "new reader normalizes a legacy index response",
);

console.log("status projection contract smoke: ok");
