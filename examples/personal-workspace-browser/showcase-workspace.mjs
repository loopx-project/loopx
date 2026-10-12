// A coherent synthetic workspace for visual review: one software migration
// Goal with two agents, measured usage, an observed live turn, owner
// decisions, a partly verified acceptance contract and a delivery map. UI PRs
// take their before/after screenshots from this state so reviewers judge the
// composition against realistic content rather than fixture placeholders.
// Every name, number and title here is invented.
export const showcaseGoalId = "progress-projection";
export const showcaseGoalTitle = "Migrate billing exports to the ledger API";
const goalId = showcaseGoalId;
const title = showcaseGoalTitle;
const minutesAgo = (minutes) => new Date(Date.now() - minutes * 60_000).toISOString();
const daysAgo = (days) => minutesAgo(days * 24 * 60);

const workspaceNames = {
  "loopx-meta": "Release 2.4 readiness",
  "product-release": "Onboarding email experiment",
  "research-monitor": "Weekly dependency audit",
  "multi-agent-projection": "Search latency investigation",
  "legacy-benchmark": "Pricing page copy review",
  "archived-notes": "Support macro cleanup",
};

const agentTodo = (index, todoId, text, status, extra = {}) => ({
  done: status === "done", index, role: "agent", status, task_class: "advancement_task", text, title: text, todo_id: `showcase-${todoId}`, ...extra,
});
const openTodos = [
  agentTodo(24, "map-columns", "Map legacy export columns to ledger entry fields", "open", { claimed_by: "ledger-migrator" }),
  agentTodo(25, "backfill", "Backfill March–May exports through the ledger API", "open", { claimed_by: "ledger-migrator", dependencies: ["showcase-map-columns"] }),
  agentTodo(26, "reconcile", "Reconcile monthly totals against the finance warehouse", "open", { claimed_by: "reconciler" }),
  agentTodo(27, "runbook", "Write the cut-over runbook", "open", { claimed_by: "reconciler" }),
];
const deferredTodo = agentTodo(28, "retire-csv", "Retire the nightly CSV export job", "deferred", { done: true, resume_when: "after:cut-over" });
const recentDone = [
  agentTodo(21, "shadow", "Shadow-write January exports to the ledger", "done", { completed_at: minutesAgo(95) }),
  agentTodo(20, "idempotency", "Add idempotency keys to ledger writes", "done", { completed_at: minutesAgo(260) }),
  agentTodo(19, "inventory", "Inventory every export consumer", "done", { completed_at: daysAgo(1) }),
];

const contract = (verificationResults) => ({
  enabled: true, revision: 3, digest: `sha256:${"b".repeat(64)}`,
  objective: "Every billing export is produced from the ledger API, and its totals reconcile with the finance warehouse.",
  scope: { kind: "all_advancement" }, non_goals: [], held_todo_ids: [], status: "partial",
  criteria: [
    { id: "regions", description: "All 14 regional exports read from the ledger API" },
    { id: "idempotent", description: "Replaying an export never duplicates a ledger entry" },
    { id: "reconcile", description: "Backfilled months match the archived CSV totals to the cent" },
    { id: "runbook", description: "The cut-over runbook is approved by the on-call owner" },
  ],
  tasks: [],
  verification: { operation_id: "showcase-verify", contract_revision: 3, contract_digest: `sha256:${"b".repeat(64)}`, todo_id: null, results: verificationResults },
});
const acceptance = {
  schema_version: "goal_acceptance_observation_projection_v0", goal_id: goalId, read_only: true, acceptance_assessed: false,
  coverage: "partial", missing_sources: [], truncated: false, historical_progress: [], acceptance_gaps: [], guards: [],
  next_action: null, next_action_source: null,
  goal_acceptance_contract: contract([
    { criterion_id: "regions", passed: true, exit_code: 0 },
    { criterion_id: "idempotent", passed: true, exit_code: 0 },
    { criterion_id: "reconcile", passed: false, exit_code: 1 },
  ]),
};

const mapNode = (id, kind, nodeTitle, state, depth, extra = {}) =>
  ({ node_id: `map_${id}`, kind, title: nodeTitle, state, depth, refs: { todo_ids: [`showcase-${id}`] }, ...extra });
const mapEdge = (from, to, relation, enforcement) =>
  ({ edge_id: `map_${from}_${to}_${relation}`, from_node_id: `map_${from}`, to_node_id: `map_${to}`, relation, enforcement, reason: "Recorded task relation." });
function deliverySnapshot() {
  return {
    ok: true, goal_id: goalId, observed_at: minutesAgo(1), acceptance,
    goal_map: {
      schema_version: "goal_task_map_v0", mode: "read_only", goal_id: goalId,
      limits: { node_limit: 120, emitted_node_count: 9, omitted_node_count: 0, source_truncated: false, missing_endpoint_count: 0, cycle_edge_count: 0, topology_complete: true },
      nodes: [
        mapNode("inventory", "deliverable", "Inventory every export consumer", "done", 0, { owner_agent: "ledger-migrator" }),
        mapNode("idempotency", "deliverable", "Add idempotency keys to ledger writes", "done", 1, { owner_agent: "ledger-migrator" }),
        mapNode("shadow", "deliverable", "Shadow-write January exports", "done", 2, { owner_agent: "ledger-migrator" }),
        mapNode("map-columns", "deliverable", "Map legacy export columns", "open", 3, { owner_agent: "ledger-migrator" }),
        mapNode("backfill", "deliverable", "Backfill March–May exports", "blocked", 4, { owner_agent: "ledger-migrator" }),
        mapNode("reconcile", "deliverable", "Reconcile monthly totals", "open", 3, { owner_agent: "reconciler" }),
        mapNode("cutover-window", "gate", "Approve the cut-over window", "open", 0),
        mapNode("retire-csv", "deliverable", "Retire the nightly CSV job", "waiting", 1, { owner_agent: "reconciler" }),
        mapNode("error-rate", "monitor", "Watch the ledger API error rate", "open", 0, { owner_agent: "reconciler" }),
      ],
      edges: [
        mapEdge("idempotency", "inventory", "continues", "lineage_only"),
        mapEdge("shadow", "idempotency", "continues", "lineage_only"),
        mapEdge("map-columns", "shadow", "continues", "lineage_only"),
        mapEdge("backfill", "map-columns", "depends_on", "typed_lifecycle"),
        mapEdge("retire-csv", "cutover-window", "depends_on", "typed_lifecycle"),
      ],
    },
    graph: null,
  };
}

/** Installs the showcase status, usage and delivery reads. `state` stays live: change it, then refresh. */
export async function installShowcaseWorkspace(api, page, state) {
  await page.route("**/api/chat/delivery-review?*", (route) => state.deliveryFails
    ? route.fulfill({ status: 503, contentType: "application/json", json: { ok: false, error: "temporarily unavailable" } })
    : route.fulfill({ contentType: "application/json", json: deliverySnapshot() }));

  api.hostThreadActivity = { [goalId]: { completeness: "complete", threads: [
    { agent_id: "ledger-migrator", host_surface: "codex-app", state: "turn_open", last_event_at: minutesAgo(1) },
  ] } };
  api.transformStatus = (fixture) => {
    for (const goal of fixture.run_history.goals) {
      if (workspaceNames[goal.id]) goal.display_name = workspaceNames[goal.id];
    }
    const goal = fixture.run_history.goals.find((item) => item.id === goalId);
    if (goal) Object.assign(goal, { display_name: title, acceptance_observation: acceptance });
    const queueItem = fixture.attention_queue.items.find((item) => item.goal_id === goalId);
    if (queueItem) {
      const items = [...openTodos, deferredTodo, ...recentDone];
      queueItem.agent_todos = {
        advancement_done_count: state.done, done_count: state.done, deferred_count: 1, items, deferred_items: [deferredTodo],
        open_count: openTodos.length, source_section: "Agent Todo", total_count: state.done + openTodos.length + 1,
      };
      queueItem.project_asset = {
        ...queueItem.project_asset, owner: "ledger-migrator", next_action: openTodos[0].text, stop_condition: "All exports reconcile from the ledger API",
        agent_todos: { advancement_done_count: state.done, done: state.done, deferred_count: 1, items: openTodos, open: openTodos.length,
          recent_completed_advancement_items: recentDone, total: state.done + openTodos.length + 1 },
      };
      queueItem.recommended_action = openTodos[0].text;
      queueItem.waiting_on = "user_or_controller";
      queueItem.user_todos = { source_section: "User Todo", open_count: 2, total_count: 2, items: [
        { done: false, status: "open", index: 0, role: "user", task_class: "user_gate", goal_id: goalId, todo_id: "showcase-gate-window",
          text: "Approve the Saturday 02:00–04:00 UTC cut-over window", updated_at: minutesAgo(42) },
        { done: false, status: "open", index: 1, role: "user", task_class: "user_action", goal_id: goalId, todo_id: "showcase-action-credentials",
          text: "Rotate the finance warehouse read credentials", updated_at: minutesAgo(180) },
      ] };
    }
    const lead = fixture.agent_management_projection.agents.find((agent) => agent.goal_ids?.includes(goalId));
    if (lead) {
      Object.assign(lead, { agent_id: "ledger-migrator", last_activity_at: minutesAgo(1), next_action: openTodos[0].text,
        current_todo: { ...lead.current_todo, claimed_by: "ledger-migrator", title: openTodos[0].text, todo_id: openTodos[0].todo_id } });
      if (!fixture.agent_management_projection.agents.some((agent) => agent.agent_id === "reconciler")) {
        fixture.agent_management_projection.agents.push({ agent_id: "reconciler", goal_ids: [goalId], last_activity_at: minutesAgo(14), state: "running",
          next_action: openTodos[2].text, current_todo: { claimed_by: "reconciler", goal_id: goalId, role: "agent", status: "open", task_class: "advancement_task", title: openTodos[2].text, todo_id: openTodos[2].todo_id } });
      }
    }
    fixture.usage_summary.goals.push({ goal_id: goalId,
      ...(state.usageWindow === "unknown" ? {} : { cost_usd_24h: 6.2, duration_ms_24h: 3_000_000 }),
      ...(!state.usageWindow || state.usageWindow === "7d" ? { cost_usd_7d: state.cost, duration_ms_7d: 13_320_000 } : {}),
    });
  };
}

export async function openShowcaseGoal(page) {
  await page.locator(".personal-goal-row").filter({ has: page.locator("strong", { hasText: title }) }).first().locator(".personal-goal-link").click();
  await page.getByTestId("goal-vitals").waitFor({ state: "visible", timeout: 15_000 });
  await page.getByRole("navigation", { name: /^(Goal 视图|Goal view)$/ }).getByRole("button", { name: /^(概览|Overview)$/ }).click();
  await page.locator(".work-map, .delivery-review [role=alert]").first().waitFor({ timeout: 15_000 });
}
