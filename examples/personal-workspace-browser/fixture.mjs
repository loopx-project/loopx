import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { startViteDashboardServer } from "../dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../../scripts/test-python.mjs";

const require = createRequire(import.meta.url);
export const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
export const dashboardDir = resolve(repoRoot, "apps/presentation/dashboard");
export const outputDir = resolve(repoRoot, "output/playwright/personal-workspace");
export const port = Number(process.env.LOOPX_PERSONAL_WORKSPACE_PORT ?? "5196");
export const packaged = process.env.LOOPX_PERSONAL_WORKSPACE_PACKAGED === "1";
export const collectCoverage = process.env.LOOPX_DASHBOARD_COVERAGE === "1";

const periodicReportProjection = {
  schema_version: "periodic_report_workspace_projection_v0",
  goal_id: "product-release",
  agent_id: "codex",
  generation_id: "generation-workspace-smoke",
  generated_at: "2026-09-01T10:00:00Z",
  title: "Product Release milestone report",
  summary: "A verified incremental report shown with the Goal's other durable outputs.",
  content_sha256: `sha256:${"7".repeat(64)}`,
  period_window: { start_at: "2026-08-25T10:00:00Z", end_at: "2026-09-01T10:00:00Z" },
  interaction: { attention_kind: "progress", interaction: "inform", delivery: "surface", form: "milestone_report", writable: false },
  delta: {
    added_count: 1,
    changed_count: 1,
    item_count: 2,
    items: [
      { fact_id: "fact-added", source_ref: "todo:release-ready", title: "Release candidate verified", summary: "The candidate passed the bounded verification suite.", status: "done", content_kind: "outcome", change_kind: "added" },
      { fact_id: "fact-changed", source_ref: "todo:rollout", title: "Rollout plan updated", summary: "The next rollout step now carries an explicit readback gate.", status: "open", content_kind: "next_action", change_kind: "changed", previous_status: "blocked" },
    ],
  },
  publication: { publication_id: "publication-workspace-smoke", delivered_at: "2026-09-01T10:05:00Z", predecessor_publication_id: "publication-workspace-previous", cursor_id: "cursor-workspace-smoke" },
  truth_contract: { published_cursor_is_source_of_truth: true, generation_receipt_is_delivery_receipt: false, projection_is_writable: false, browser_write_api: false },
};

function periodicReportCapability({ effectiveConfiguration, machineCurrent } = {}) {
  return {
    capability_id: "periodic_report",
    display_name: "Periodic reports",
    description: "Turn validated Goal progress into a reviewable report draft.",
    available_scopes: ["machine", "goal"],
    availability: "supported_explicit_override",
    machine_namespace: "periodic_report",
    goal_feature_id: "periodic_report",
    effective_value_policy: "goal_override_over_live_machine_default",
    default: {
      schema_version: "periodic_report_machine_defaults_v0",
      enabled: false,
      inheritance: "live_machine_default",
      timezone: "UTC",
    },
    ...(machineCurrent ? { machine_current: machineCurrent } : {}),
    ...(effectiveConfiguration ? { effective_configuration: effectiveConfiguration } : {}),
    configuration_editor: {
      schema_version: "capability_configuration_editor_v0",
      editable: true,
      supported_scopes: ["machine", "goal"],
      writable_scopes: ["machine", "goal"],
      fields: [
        { key: "enabled", label: "Enabled", description: "", input_kind: "boolean", required: false },
        { key: "profile_preset", label: "Profile preset", description: "", input_kind: "text", required: false },
        { key: "route_ref", label: "Goal Channel route", description: "", input_kind: "text", required: false },
        { key: "timezone", label: "Timezone", description: "", input_kind: "text", required: true },
      ],
    },
  };
}

function multiSubagentCapability({ current } = {}) {
  const fallback = {
    enabled: false,
    max_children: 4,
    allowed_domains: [],
  };
  const effective = current ?? fallback;
  return {
    capability_id: "multi_subagent",
    context_contribution: {
      supported_phases: ["before_plan", "before_delegate", "after_delegate_result"],
      target: "coordinator", activation: "with_capability", receipt_required: true,
    },
    display_name: "Adaptive child capacity",
    description: "Bound child-agent capacity and eligible responsibility domains.",
    available_scopes: ["goal"],
    availability: "supported_opt_in",
    goal_feature_id: "multi_subagent",
    default: fallback,
    ...(current ? { current } : {}),
    effective_configuration: {
      schema_version: "capability_configuration_resolution_v0",
      capability_id: "multi_subagent",
      source: current ? "goal_override" : "capability_default",
      configuration: effective,
      inherited: false,
      goal_override_present: Boolean(current),
      machine_default_present: false,
      effective_revision: current ? "sha256:subagent-current" : "sha256:subagent-default",
    },
    configuration_editor: {
      schema_version: "capability_configuration_editor_v0",
      editable: true,
      supported_scopes: ["goal"],
      writable_scopes: ["goal"],
      fields: [
        { key: "enabled", label: "Enabled", description: "", input_kind: "boolean", required: false },
        { key: "model", label: "Child model", description: "", input_kind: "text", required: false },
        { key: "reasoning_effort", label: "Child reasoning effort", description: "", input_kind: "text", required: false },
        { key: "max_children", label: "Maximum children", description: "", input_kind: "number", required: false, minimum: 1, maximum: 32 },
        { key: "allowed_domains", label: "Allowed responsibility domains", description: "", input_kind: "string_list", required: false },
      ],
    },
  };
}

function goalCapability({
  availability = "supported_opt_in",
  defaultConfiguration,
  capabilityId,
  displayName,
  editorScopes = ["goal"],
  fields = [{ key: "enabled", label: "Enabled", description: "", input_kind: "boolean", required: false }],
  readOnlyReason,
}) {
  const editable = fields.length > 0;
  return {
    capability_id: capabilityId,
    display_name: displayName,
    description: `${displayName} Goal policy.`,
    available_scopes: ["goal"],
    goal_feature_id: capabilityId,
    availability,
    default: defaultConfiguration ?? (editable ? Object.fromEntries(fields.flatMap((field) => field.key === "enabled" ? [[field.key, false]] : [])) : {}),
    configuration_editor: {
      schema_version: "capability_configuration_editor_v0",
      editable,
      supported_scopes: editorScopes,
      writable_scopes: editable ? editorScopes : [],
      fields,
      ...(readOnlyReason ? { read_only_reason: readOnlyReason } : {}),
    },
  };
}

export function goalCapabilityCatalog(multiSubagentConfiguration) {
  return [
    goalCapability({
      capabilityId: "todo_replan_cadence",
      displayName: "Goal review cadence",
      editorScopes: ["machine", "goal"],
      defaultConfiguration: { completed_todos: 5 },
      fields: [{ key: "completed_todos", label: "Completed Todos between Goal reviews", description: "", input_kind: "number", required: true, minimum: 1, maximum: 5 }],
    }),
    periodicReportCapability(),
    goalCapability({
      capabilityId: "change_quality_qualification",
      displayName: "Change quality qualification",
      editorScopes: ["machine", "goal"],
      fields: [
        { key: "enabled", label: "Enabled", description: "", input_kind: "boolean", required: false },
        { key: "safe_fix", label: "Allow one bounded safe-fix pass", description: "", input_kind: "boolean", required: false },
        { key: "strict_receipt", label: "Require an exact-diff receipt", description: "", input_kind: "boolean", required: false },
      ],
    }),
    goalCapability({ capabilityId: "explore_graph", displayName: "Explore Graph" }),
    goalCapability({
      capabilityId: "explore_harness",
      displayName: "Explore Harness",
      fields: [
        { key: "enabled", label: "Enabled", description: "", input_kind: "boolean", required: false },
        { key: "profile", label: "Planner profile", description: "", input_kind: "select", required: false, options: ["generic"] },
      ],
    }),
    goalCapability({ capabilityId: "lark_kanban_heartbeat_sync", displayName: "Lark Kanban heartbeat sync" }),
    goalCapability({
      capabilityId: "lark_event_inbox",
      displayName: "Lark event inbox",
      fields: [],
      readOnlyReason: "Requires a local-private provider binding.",
    }),
    goalCapability({
      availability: "supported_explicit_opt_in",
      capabilityId: "peer_task_coordination",
      displayName: "Registered-peer task coordination",
      fields: [{ key: "coordinator_agent_id", label: "Coordinator Agent", description: "", input_kind: "text", required: false }],
    }),
    multiSubagentCapability({ current: multiSubagentConfiguration }),
    goalCapability({ availability: "retired", capabilityId: "local_authority_shadow", displayName: "Retired authority observation", fields: [], readOnlyReason: "Clear retired observation config explicitly; bootstrap runtime shadow separately." }),
    goalCapability({
      availability: "experimental_opt_in",
      capabilityId: "reward_memory",
      displayName: "Reward Memory experiment",
      fields: [],
      readOnlyReason: "Requires a reviewed local-private provider binding.",
    }),
  ];
}

function withMachineConfiguration(capability, { configuration, description }) {
  return {
    ...capability,
    description,
    available_scopes: ["machine", "goal"],
    machine_namespace: capability.capability_id,
    machine_current: configuration,
    default: configuration,
    effective_value_policy: "goal_override_over_live_machine_default",
    effective_configuration: {
      schema_version: "capability_configuration_resolution_v0",
      capability_id: capability.capability_id,
      source: "machine_default",
      configuration,
      inherited: true,
      goal_override_present: false,
      machine_default_present: true,
      effective_revision: `sha256:${capability.capability_id}`,
    },
  };
}

/**
 * The receipt a confirmed team plan writes, for the plan the fixture stored.
 *
 * The product answers an apply with the plan's own outcome, so a scenario that
 * confirms a plan carrying a staffing gap has to observe the same readback the
 * card renders: which lanes became work and which ones stayed unstaffed, with
 * the host fact behind each gap. The lanes are derived from the stored plan
 * because that is the plan the apply was confirmed against.
 */
function teamPlanApplyReceipt(proposal) {
  const plan = proposal?.normalized_parameters?.plan;
  if (!plan || typeof plan !== "object" || !Array.isArray(plan.lanes)) return null;
  const ready = plan.lanes.filter((lane) => lane.staffing !== "gap");
  const gaps = plan.lanes.filter((lane) => lane.staffing === "gap");
  const receipt = {
    projection_verified: true,
    receipt_id: "fixture-team-plan-receipt",
  };
  if (ready.length > 0) {
    receipt.outcome = gaps.length > 0 ? "team_plan_partially_applied" : "team_plan_applied";
  }
  if (ready.length > 0) {
    receipt.resource_ids = {
      goal_id: proposal?.normalized_parameters?.goal_id ?? null,
      todo_id: `todo_${ready[0].lane_id}`,
      lane_todo_ids: ready.map((lane) => `todo_${lane.lane_id}`),
    };
    receipt.lanes = ready.map((lane) => ({
      lane_id: lane.lane_id,
      agent_id: lane.agent_id,
      priority: lane.first_todo?.priority ?? "P1",
      disposition: "created",
      todo_id: `todo_${lane.lane_id}`,
      acceptance: lane.acceptance,
    }));
  }
  if (gaps.length > 0) {
    // The count and the lanes it counts stay together, and a lane that stayed
    // unstaffed names the Agent it was meant to run on.
    receipt.gap_count = gaps.length;
    receipt.gap_lanes = gaps.map((lane) => ({
      lane_id: lane.lane_id,
      agent_id: lane.agent_id,
      reason_code: lane.gap_reason_code,
    }));
  }
  return receipt;
}

export function startServer() {
  if (packaged) {
    // An explicit installed interpreter must resolve its own package, not the checkout.
    const isolation = process.env.LOOPX_PYTHON_BIN ? ["-I"] : [];
    return spawn(resolveTestPython(), [...isolation, "-c", `
from loopx.chat_server import ChatHTTPServer, ChatRequestHandler, default_chat_assets_dir
from loopx.presentation.chat_bundle import validate_bundle
assets = default_chat_assets_dir()
validate_bundle(assets)
server = ChatHTTPServer(("127.0.0.1", ${port}), ChatRequestHandler)
server.assets_dir = assets
server.verbose = False
server.serve_forever()
`], {
      cwd: repoRoot,
      env: { ...process.env },
      stdio: "ignore",
    });
  }
  return startViteDashboardServer({ dashboardDir, port });
}

export async function visibleElementCount(locator) {
  return locator.evaluateAll((elements) => elements.filter((element) => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
  }).length);
}

export async function waitForInputValue(locator, expected, timeoutMs = 5_000) {
  const deadline = Date.now() + timeoutMs;
  let actual = await locator.inputValue();
  while (actual !== expected && Date.now() < deadline) {
    await new Promise((resolveWait) => setTimeout(resolveWait, 50));
    actual = await locator.inputValue();
  }
  if (actual !== expected) {
    throw new Error(`Timed out waiting for input value ${expected}; received ${actual}`);
  }
}

function capturedStatusGeneration(state) {
  if (!state.captureNextStatusGeneration) return state.goalActivationStates;
  if (!state.capturedStatusGeneration) {
    state.capturedStatusGeneration = new Map(state.goalActivationStates);
  }
  return state.capturedStatusGeneration;
}

/** The registry revision the progressive loader fences every Goal read against. */
function workspaceRegistryRevision(state) {
  return [...capturedStatusGeneration(state).entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([goalId, activationState]) => `${goalId}:${activationState}`)
    .join("|");
}

/**
 * Goals the fixture injects into both the workspace directory and the status
 * payload. One list keeps a Goal's registered name identical in a per-Goal read
 * and in the directory entry that outlives its snapshot.
 */
const directoryGoalFixtures = [
  { id: "product-release", display_name: "Product Release" },
  { id: "research-monitor", display_name: "Research Monitor" },
  { id: "progress-projection", display_name: "Progress Projection" },
  { id: "legacy-benchmark", display_name: "Legacy Benchmark" },
  { id: "archived-notes", display_name: "Archived Notes" },
];

function filterStatusFixtureToScope(fixture, matchesScope) {
  fixture.attention_queue.items = fixture.attention_queue.items.filter((item) => matchesScope(item.goal_id));
  fixture.attention_queue.item_count = fixture.attention_queue.items.length;
  if (fixture.todo_index?.items) {
    fixture.todo_index.items = fixture.todo_index.items.filter((item) => matchesScope(item.goal_id));
    fixture.todo_index.total_count = fixture.todo_index.items.length;
  }
  if (fixture.usage_summary?.goals) {
    fixture.usage_summary.goals = fixture.usage_summary.goals.filter((item) => matchesScope(item.goal_id));
  }
  if (fixture.event_ledger_summary?.goals) {
    fixture.event_ledger_summary.goals = fixture.event_ledger_summary.goals.filter((item) => matchesScope(item.goal_id));
  }
  if (fixture.decision_freshness_summary?.items) {
    fixture.decision_freshness_summary.items = fixture.decision_freshness_summary.items.filter((item) => matchesScope(item.goal_id));
  }
  if (fixture.agent_management_projection?.agents) {
    fixture.agent_management_projection.agents = fixture.agent_management_projection.agents.filter((agent) =>
      (agent.goal_ids ?? []).some(matchesScope) || matchesScope(agent.current_todo?.goal_id));
  }
  if (fixture.goal_channel_notification_projection?.goals) {
    fixture.goal_channel_notification_projection.goals = fixture.goal_channel_notification_projection.goals.filter((item) => matchesScope(item.goal_id));
  }
}

export async function installApi(page, { goalSubagentConfigurationEnabled = true, initialActionProposals = [], managerChannelBinding = null, progressiveWorkspace = false, runtimeAgents = null } = {}) {
  let turnCounter = 0;
  const runtime = page.__loopxRuntime ??= { actionProposals: new Map(), goalSubagentConfigurations: new Map(), larkConnections: [], messages: new Map(), sessions: new Map(), turnMessages: new Map() };
  const actionProposals = runtime.actionProposals;
  const sessions = runtime.sessions;
  const messages = runtime.messages;
  const turnMessages = runtime.turnMessages;
  const loopxModes = runtime.loopxModes ??= new Map();
  for (const proposal of initialActionProposals) {
    actionProposals.set(proposal.proposal_id, structuredClone(proposal));
  }
  // Like ChatStore, persist completion before serving it and replay after disconnect.
  const completedTurns = runtime.completedTurns ??= new Map();
  const finishTurn = (sessionId, turnId, answer, protectedAction = null) => {
    const key = JSON.stringify([sessionId, turnId]);
    if (completedTurns.has(key)) return completedTurns.get(key);
    const current = sessions.get(sessionId);
    if (!current || current.active_turn_id !== turnId) return "";
    const visible = messages.get(sessionId) ?? [];
    if (!visible.some((message) => message.message_id === `${turnId}-assistant`)) {
      visible.push({ message_id: `${turnId}-assistant`, turn_id: turnId, role: "assistant", text: answer, created_at: "2026-08-13T01:00:02Z" });
    }
    messages.set(sessionId, visible);
    const event = (id, kind, payload) => `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify({ event_id: id, sequence: Number(id), kind, created_at: "2026-08-13T01:00:02Z", payload })}\n\n`;
    const body = event("1", "assistant.delta", { text: answer }) + event("2", "turn.completed", { response: { schema_version: "loopx_chat_agent_response_v0", message: answer, proposals: [], protected_action: protectedAction, gate: null } });
    completedTurns.set(key, body);
    sessions.set(sessionId, { ...current, active_turn_id: null, status: "ready", updated_at: "2026-08-13T01:00:02Z" });
    return body;
  };

  const actionKinds = new Map(Array.from(actionProposals.values(), (proposal) => [proposal.proposal_id, proposal.action_kind]));
  const state = {
    nextLifecycleProposalPatch: null,
    nextLifecycleApplyOutcome: null,
    loseNextTeamPlanResponse: false,
    actionApplies: [],
    actionCancels: [],
    actionPreviews: [],
    durableResources: new Set(),
    durableWriteCount: 0,
    failNextLifecycleApply: false,
    failNextGoalSubagentResponse: false,
    failNextLifecyclePreview: false,
    failNextActionPreview: false,
    failNextStatusRequest: false,
    freezeGoalSubagentStatusProjection: false,
    goalSubagentConfigurationEnabled,
    goalActivationStates: new Map([
      ["product-release", "active"],
      ["research-monitor", "active"],
      ["legacy-benchmark", "stopped"],
      ["archived-notes", "stopped"],
    ]),
    goalSubagentPreviews: [],
    goalSubagentWrites: [],
    interrupts: [],
    goalConfigurationRequests: [],
    machineConfigurationRequests: [],
    machineInspectionStatus: "configured",
    failNextMachineInspection: false,
    invalidMachineNamespaces: [],
    larkWrites: [],
    actionTransitions: [],
    allowNextHeartbeatApply: false,
    nextLifecycleApplyDelayMs: 0,
    nextLifecyclePreviewDelayMs: 0,
    nextActionPreviewDelayMs: 0,
    nextStatusDelayMs: 0,
    nextFullStatusDelayMs: 0,
    failNextFullStatus: false,
    captureNextStatusGeneration: false,
    capturedStatusGeneration: null,
    activationChangeAfterCapturedActive: null,
    statusRequestCount: 0,
    // Progressive loading reads one Goal at a time; the scenario asserts which
    // Goals a single action sent back through the loader.
    goalStatusRequests: [],
    workspaceDirectoryRequests: 0,
    operatorCredential: {
      providerKeyConfigured: true,
      providerKeySource: "service_environment",
      providerKeyFingerprint: "3efe046b2b3d",
      baseUrl: null,
      baseUrlSource: "unset",
    },
    operatorCredentialWrites: [],
    turnRequests: [],
    hostThreadActivity: {},
    answerForMessage: null,
    loopxModeRequests: [],
    get larkConnections() { return runtime.larkConnections; },
    get goalSubagentConfigurations() { return runtime.goalSubagentConfigurations; },
  };
  await page.route(`http://127.0.0.1:${port}/status.json*`, async (route) => {
    state.statusRequestCount += 1;
    const requestUrl = new URL(route.request().url());
    const requestedGoalId = requestUrl.searchParams.get("goal_id");
    if (progressiveWorkspace && requestUrl.searchParams.get("view") === "workspace-directory") {
      state.workspaceDirectoryRequests += 1;
      // The directory carries the same registered names as the per-Goal
      // payloads, so a Goal keeps its title while it has no snapshot.
      const registeredNames = new Map(directoryGoalFixtures.map((goal) => [goal.id, goal.display_name]));
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "loopx_workspace_directory_v1",
        registry_revision: workspaceRegistryRevision(state),
        goals: [...state.goalActivationStates.entries()].map(([id, activation_state]) => ({
          activation_state, display_name: registeredNames.get(id) ?? id, id, registry_member: true,
        })),
      }, status: 200 });
      return;
    }
    if (progressiveWorkspace && requestedGoalId) state.goalStatusRequests.push(requestedGoalId);
    const fixture = structuredClone(require(resolve(repoRoot, "examples/status.example.json")));
    const defaultSubagentConfiguration = { mode: "default", spawn_allowed: false, max_children: 0, allowed_domains: [] };
    const projectedSubagentConfiguration = (goalId, fallback) => state.freezeGoalSubagentStatusProjection
      ? fallback ?? defaultSubagentConfiguration
      : runtime.goalSubagentConfigurations.get(goalId) ?? fallback ?? defaultSubagentConfiguration;
    const statusGeneration = capturedStatusGeneration(state);
    fixture.local_dashboard_api = {
      ...(fixture.local_dashboard_api ?? {}),
      periodic_report_index_url: "/periodic-report-workspace",
      periodic_report_detail_url: "/periodic-report-workspace-projection",
    };
    for (const directoryGoal of directoryGoalFixtures) {
      const activation_state = statusGeneration.get(directoryGoal.id) ?? "active";
      const existingGoal = fixture.run_history.goals.find((goal) => goal.id === directoryGoal.id);
      if (existingGoal) {
        existingGoal.activation_state = activation_state;
        if (state.goalSubagentConfigurationEnabled) {
          existingGoal.spawn_policy = projectedSubagentConfiguration(directoryGoal.id, existingGoal.spawn_policy);
        } else {
          delete existingGoal.spawn_policy;
        }
        continue;
      }
      fixture.run_history.goals.push({
        ...directoryGoal, activation_state,
        status: "active-read-only", registry_member: true,
        legacy_runtime_goal: false, adapter_kind: "generic_project_goal_v0", adapter_status: "connected",
        lifecycle_phase: "registered", lifecycle_flags: ["registered"],
        ...(state.goalSubagentConfigurationEnabled
          ? { spawn_policy: projectedSubagentConfiguration(directoryGoal.id) }
          : {}),
        quota: { compute: 1, window_hours: 24, slot_minutes: 1, allowed_slots: 1440, spent_slots: 0, state: activation_state === "stopped" ? "paused" : "waiting" },
        index_exists: false, raw_index_records: 0, unique_runs: 0, latest_runs: [],
      });
    }
    for (const fixtureGoal of fixture.run_history.goals) {
      if (state.goalSubagentConfigurationEnabled) {
        fixtureGoal.spawn_policy = projectedSubagentConfiguration(fixtureGoal.id, fixtureGoal.spawn_policy);
      } else {
        delete fixtureGoal.spawn_policy;
      }
    }
    if (!fixture.run_history.goals.some((goal) => goal.id === "stale-browser-goal")) {
      fixture.run_history.goals.push({
        id: "stale-browser-goal", status: "monitoring", registry_member: false,
        legacy_runtime_goal: false, adapter_kind: null, adapter_status: null,
        index_exists: false, raw_index_records: 0, unique_runs: 0, latest_runs: [],
      });
    }
    const first = fixture.attention_queue?.items?.[0];
    if (first) {
      first.waiting_on = "user_or_controller";
      first.user_todos = {
        items: [{ done: false, goal_id: first.goal_id, index: 0, role: "user", text: "确认本轮独立审查范围", todo_id: "todo-browser-user-gate" }],
        open_count: 1,
        source_section: "User Todo",
        total_count: 1,
      };
      const domainTodos = (first.project_asset?.agent_todos?.items ?? first.agent_todos?.items ?? [])
        .filter((todo) => !todo.done)
        .slice(0, 2);
      for (const [index, todo] of domainTodos.entries()) {
        todo.task_class = "advancement_task";
        todo.task_domain = index === 0 ? "code" : "validation";
        const indexedTodo = fixture.todo_index?.items?.find((item) => item.todo_id === todo.todo_id);
        if (indexedTodo) {
          indexedTodo.role = "agent";
          indexedTodo.task_class = todo.task_class;
          indexedTodo.task_domain = todo.task_domain;
        } else if (fixture.todo_index?.items) {
          fixture.todo_index.items.push({ ...todo, goal_id: first.goal_id, role: "agent", source: "browser-smoke" });
        }
      }
    }
    if (!fixture.attention_queue.items.some((item) => item.goal_id === "progress-projection")) {
      const idlessLongTitle = `Idless long Todo ${"projection identity ".repeat(16)}keeps one card`;
      const scheduledDeferredTodo = {
        done: true,
        index: 8,
        role: "agent",
        status: "deferred",
        resume_when: "resume_at:2026-09-14T01:30:00Z",
        resume_ready: true,
        resume_condition: {
          schema_version: "todo_resume_condition_v0",
          kind: "resume_at",
          satisfied: true,
          resume_when: "resume_at:2026-09-14T01:30:00Z",
          resume_receipt: {
            schema_version: "todo_resume_receipt_v0",
            receipt_id: "resume_at_browser_smoke_receipt",
          },
        },
        task_class: "advancement_task",
        text: "Deferred queue task",
        title: "Deferred queue task",
        todo_id: "todo-progress-deferred",
      };
      const currentTodo = {
        done: false,
        index: 4,
        role: "agent",
        status: "open",
        task_class: "advancement_task",
        text: "Current Todo",
        title: "Current Todo",
        todo_id: "todo-progress-current",
      };
      fixture.attention_queue.items.push({
        agent_todos: {
          advancement_done_count: 42,
          done_count: 6,
          deferred_count: 2,
          items: [
            currentTodo,
            { done: false, index: 5, role: "agent", status: "open", task_class: "advancement_task", text: idlessLongTitle, title: idlessLongTitle },
            { done: false, index: 7, role: "agent", status: "open", task_class: "advancement_task", text: "Full queue follow-up", title: "Full queue follow-up", todo_id: "todo-progress-full" },
            scheduledDeferredTodo,
            { done: true, index: 1, role: "agent", status: "done", task_class: "advancement_task", text: "Completed A", title: "Completed A", todo_id: "todo-progress-a" },
            { done: true, index: 2, role: "agent", status: "done", task_class: "advancement_task", text: "Completed B", title: "Completed B", todo_id: "todo-progress-b" },
            { done: true, index: 3, role: "agent", status: "done", task_class: "advancement_task", text: "Completed C", title: "Completed C", todo_id: "todo-progress-c" },
            { done: true, index: 6, role: "agent", status: "done", task_class: "continuous_monitor", text: "Completed Monitor", title: "Completed Monitor", todo_id: "todo-progress-monitor" },
          ],
          deferred_items: [
            scheduledDeferredTodo,
            { done: true, index: 9, role: "agent", status: "deferred", task_class: "advancement_task", text: "Deferred follow-up outside preview", title: "Deferred follow-up outside preview", todo_id: "todo-progress-deferred-extra" },
          ],
          open_count: 3,
          source_section: "Agent Todo",
          total_count: 9,
        },
        goal_id: "progress-projection",
        project_asset: {
          agent_todos: {
            advancement_done_count: 42,
            done: 6,
            deferred_count: 2,
            items: [
              currentTodo,
              { done: false, index: 5, role: "agent", status: "open", task_class: "advancement_task", text: idlessLongTitle.slice(0, 220), title: idlessLongTitle.slice(0, 220) },
            ],
            open: 3,
            recent_completed_advancement_items: [
              { done: true, index: 1, role: "agent", status: "done", task_class: "advancement_task", text: "Completed A", title: "Completed A", todo_id: "todo-progress-a" },
              { done: true, index: 2, role: "agent", status: "done", task_class: "advancement_task", text: "Completed B", title: "Completed B", todo_id: "todo-progress-b" },
              { done: true, index: 3, role: "agent", status: "done", task_class: "advancement_task", text: "Completed C", title: "Completed C", todo_id: "todo-progress-c" },
            ],
            total: 9,
          },
          gate: "none",
          next_action: "Current Todo",
          owner: "example-agent",
          stop_condition: "All synthetic Todos complete",
        },
        recommended_action: "Older Todo",
        severity: "info",
        status: "active",
        waiting_on: "codex",
      });
      fixture.agent_management_projection.agents.push({
        agent_id: "example-agent",
        current_todo: {
          action_kind: "synthetic_progress_projection",
          goal_id: "progress-projection",
          priority: "P0",
          role: "agent",
          status: "open",
          task_class: "advancement_task",
          title: "Current Todo",
          todo_id: "todo-progress-current",
        },
        goal_ids: ["progress-projection"],
        last_activity_at: "2026-08-24T14:53:12+08:00",
        next_action: "Continue projected todo todo-progress-current.",
        state: "running",
      });
    }
    if (!fixture.run_history.goals.some((goal) => goal.id === "multi-agent-projection")) {
      fixture.run_history.goals.push({
        id: "multi-agent-projection", display_name: "Multi Agent Projection", activation_state: "active",
        status: "active-read-only", registry_member: true, legacy_runtime_goal: false,
        adapter_kind: "generic_project_goal_v0", adapter_status: "connected",
        lifecycle_phase: "registered", lifecycle_flags: ["registered"],
        quota: { compute: 1, window_hours: 24, slot_minutes: 1, allowed_slots: 1440, spent_slots: 0, state: "eligible" },
        coordination: { thread_agent_bindings: [{ agent_id: "codex-latest-lane", host_surface: "codex-app", thread_id: "fixture-bound-thread" }] },
        index_exists: false, raw_index_records: 0, unique_runs: 0, latest_runs: [],
      });
      fixture.attention_queue.items.push({
        agent_todos: { items: [], open_count: 2, source_section: "Agent Todo", total_count: 2 },
        goal_id: "multi-agent-projection",
        project_asset: {
          agent_todos: { items: [], open: 2, done: 0, total: 2 },
          gate: "none", next_action: "Continue the latest work lane", owner: "LoopX", stop_condition: "Both lanes complete",
        },
        recommended_action: "Continue the latest work lane", severity: "info", status: "active", waiting_on: "codex",
      });
      fixture.agent_management_projection.agents.push(
        {
          agent_id: "codex-older-lane",
          current_todo: { claimed_by: "codex-older-lane", goal_id: "multi-agent-projection", role: "agent", status: "open", task_class: "advancement_task", title: "Older lane work", todo_id: "todo-older-lane" },
          goal_ids: ["multi-agent-projection"], last_activity_at: "2026-08-24T10:00:00+08:00", next_action: "Continue projected todo todo-older-lane.", state: "running",
        },
        {
          agent_id: "codex-latest-lane",
          current_todo: { claimed_by: "codex-latest-lane", goal_id: "multi-agent-projection", role: "agent", status: "open", task_class: "advancement_task", title: "Latest lane work", todo_id: "todo-latest-lane" },
          goal_ids: ["multi-agent-projection"], last_activity_at: "2026-08-24T15:00:00+08:00", next_action: "Continue projected todo todo-latest-lane.", state: "running",
        },
      );
    }
    for (const [goalId, activity] of Object.entries(state.hostThreadActivity)) {
      const goal = fixture.run_history.goals.find((item) => item.id === goalId);
      if (goal) goal.host_thread_activity = activity;
    }
    const goalActivationScope = new URL(route.request().url()).searchParams.get("goal_activation");
    const isActiveScope = goalActivationScope === "active";
    const activeGoalCount = fixture.run_history.goals.filter((goal) => goal.activation_state !== "stopped").length;
    const stoppedGoalCount = fixture.run_history.goals.length - activeGoalCount;
    const registryRevision = workspaceRegistryRevision(state);
    const delayMs = state.nextStatusDelayMs;
    state.nextStatusDelayMs = 0;
    if (delayMs > 0) await new Promise((resolveWait) => setTimeout(resolveWait, delayMs));
    if (state.failNextStatusRequest) {
      state.failNextStatusRequest = false;
      await route.fulfill({ contentType: "application/json", json: { error: "temporary status failure" }, status: 503 });
      return;
    }
    if (progressiveWorkspace) {
      // The real service answers a per-Goal read with exactly that Goal and the
      // registry revision the directory was read at.
      fixture.workspace_registry_revision = registryRevision;
      if (requestedGoalId) {
        fixture.goal_projection = {
          schema_version: "loopx_goal_projection_scope_v0",
          scope: "active",
          complete: true,
          projected_goal_count: 1,
          registry_goal_count: fixture.run_history.goals.length,
          registry_revision: registryRevision,
        };
        fixture.run_history.goals = fixture.run_history.goals.filter((goal) => goal.id === requestedGoalId);
        filterStatusFixtureToScope(fixture, (goalId) => goalId === requestedGoalId);
        await route.fulfill({ contentType: "application/json", json: fixture, status: 200 });
        return;
      }
    }
    if (isActiveScope) {
      fixture.goal_projection = {
        schema_version: "loopx_goal_projection_scope_v0",
        scope: "active",
        complete: false,
        projected_goal_count: activeGoalCount,
        registry_goal_count: fixture.run_history.goals.length,
        registry_revision: registryRevision,
      };
      fixture.run_history.goals = fixture.run_history.goals.filter((goal) => goal.activation_state !== "stopped");
      filterStatusFixtureToScope(fixture, (goalId) => (statusGeneration.get(goalId) ?? "active") === "active");
      // Freeze only the first half of the active-first read. The stopped
      // request must observe the registry after the intervening lifecycle
      // change so this fixture exercises the cross-snapshot revision fence.
      state.captureNextStatusGeneration = false;
      state.capturedStatusGeneration = null;
      if (state.activationChangeAfterCapturedActive) {
        const { goalId, activationState } = state.activationChangeAfterCapturedActive;
        state.goalActivationStates.set(goalId, activationState);
        state.activationChangeAfterCapturedActive = null;
      }
    } else if (goalActivationScope === "stopped") {
      const fullDelayMs = state.nextFullStatusDelayMs;
      state.nextFullStatusDelayMs = 0;
      if (fullDelayMs > 0) await new Promise((resolveWait) => setTimeout(resolveWait, fullDelayMs));
      if (state.failNextFullStatus) {
        state.failNextFullStatus = false;
        await route.fulfill({ contentType: "application/json", json: { error: "stopped goals unavailable" }, status: 503 });
        return;
      }
      fixture.goal_projection = {
        schema_version: "loopx_goal_projection_scope_v0",
        scope: "stopped",
        complete: false,
        projected_goal_count: stoppedGoalCount,
        registry_goal_count: fixture.run_history.goals.length,
        registry_revision: registryRevision,
      };
      fixture.run_history.goals = fixture.run_history.goals.filter((goal) => goal.activation_state === "stopped");
      filterStatusFixtureToScope(fixture, (goalId) => (statusGeneration.get(goalId) ?? "active") === "stopped");
    } else {
      fixture.goal_projection = {
        schema_version: "loopx_goal_projection_scope_v0",
        scope: "all",
        complete: true,
        projected_goal_count: fixture.run_history.goals.length,
        registry_goal_count: fixture.run_history.goals.length,
        registry_revision: registryRevision,
      };
    }
    await route.fulfill({ contentType: "application/json", json: fixture, status: 200 });
  });
  await page.route("**/periodic-report-workspace?*", async (route) => {
    const requestUrl = new URL(route.request().url());
    const goalId = requestUrl.searchParams.get("goal_id");
    if (requestUrl.searchParams.get("limit") !== "100" || requestUrl.searchParams.get("offset") !== "0") {
      throw new Error("Periodic-report index request did not negotiate a bounded window");
    }
    const items = goalId === periodicReportProjection.goal_id ? [{
      goal_id: periodicReportProjection.goal_id,
      agent_id: periodicReportProjection.agent_id,
      generation_id: periodicReportProjection.generation_id,
      publication_id: periodicReportProjection.publication.publication_id,
      delivered_at: periodicReportProjection.publication.delivered_at,
      predecessor_publication_id: periodicReportProjection.publication.predecessor_publication_id,
      detail_ref: {
        goal_id: periodicReportProjection.goal_id,
        agent_id: periodicReportProjection.agent_id,
        generation_id: periodicReportProjection.generation_id,
        content_sha256: periodicReportProjection.content_sha256,
      },
    }] : [];
    await route.fulfill({
      contentType: "application/json",
      json: {
        ok: true,
        periodic_reports: {
          schema_version: "periodic_report_workspace_index_v0",
          count: items.length,
          returned_count: items.length,
          total_count: items.length,
          limit: 100,
          offset: 0,
          truncated: false,
          items,
        },
      },
      status: 200,
    });
  });
  await page.route("**/periodic-report-workspace-projection?*", async (route) => {
    await route.fulfill({ contentType: "application/json", json: { ok: true, projection: periodicReportProjection }, status: 200 });
  });
  await page.route(`http://127.0.0.1:${port}/api/ssh-source/ensure`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: { ok: true, status_url: "http://127.0.0.1:8876/status.json", tunnel_required: true, remote_started: true },
      status: 200,
    });
  });
  await page.route(`http://127.0.0.1:${port}/ssh-hosts`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: {
        ok: true,
        schema_version: "ssh_host_catalog_v0",
        hosts: [{ alias: "remote-lab" }, { alias: "remote-build" }],
      },
      status: 200,
    });
  });
  await page.route("http://127.0.0.1:8876/status.json", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: require(resolve(repoRoot, "examples/status.example.json")),
      status: 200,
    });
  });
  await page.route("http://127.0.0.1:8976/status.json", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: require(resolve(repoRoot, "examples/status.example.json")),
      status: 200,
    });
  });
  await page.route("http://127.0.0.1:8877/status.json", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      json: require(resolve(repoRoot, "examples/status.example.json")),
      status: 200,
    });
  });
  await page.route("**/api/chat/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/chat/completed-todos") {
      const total = url.searchParams.get("goal_id") === "progress-projection" ? 4087 : 0;
      const offset = Number(url.searchParams.get("cursor") || 0);
      const items = Array.from({ length: Math.min(40, total - offset) }, (_, position) => {
        const index = offset + position;
        return { todo_id: `todo_history_${index}`, text: index < 3 ? `Completed ${String.fromCharCode(65 + index)}` : `Completed historical Task ${index + 1}`, claimed_by: "example-agent", evidence: null, priority: null, task_class: "advancement_task" };
      });
      await route.fulfill({ json: { ok: true, total, items, next_cursor: offset + 40 < total ? String(offset + 40) : null } });
      return;
    }
    if (url.pathname === "/api/chat/goal-results") {
      await route.fulfill({ json: { ok: true, items: [], total: 0, next_cursor: null, unavailable_count: 0, unavailable_todo_ids: [] } });
      return;
    }
    const periodicConfiguration = {
      schema_version: "periodic_report_machine_defaults_v0",
      enabled: true,
      inheritance: "live_machine_default",
      profile_preset: "weekly-progress",
      route_ref: "report-route",
      timezone: "Asia/Shanghai",
    };
    const cadenceConfiguration = {
      schema_version: "todo_replan_cadence_machine_defaults_v0",
      completed_todos: 3,
    };
    const changeQualityConfiguration = {
      schema_version: "change_quality_machine_defaults_v0",
      enabled: true,
      safe_fix: false,
      strict_receipt: true,
    };
    const managerRuntimeConfiguration = {
      schema_version: "manager_runtime_profile_v0",
      runtime_profile: "restricted",
    };
    const stewardExecutorConfiguration = {
      schema_version: "steward_executor_machine_defaults_v1",
      selection_policy: "preferred",
      executor_endpoint: "codex",
      eligible_endpoints: [],
      executor_model: null,
      executor_reasoning_effort: null,
    };
    // Applied namespaces persist for the page; this handler runs once per request.
    const machineNamespaces = state.machineNamespaces ??= {
      change_quality_qualification: changeQualityConfiguration,
      manager_runtime: managerRuntimeConfiguration,
      periodic_report: periodicConfiguration,
      steward_executor: stewardExecutorConfiguration,
      todo_replan_cadence: cadenceConfiguration,
    };
    const goalCapabilities = goalCapabilityCatalog();
    const machineConfigurationBase = {
      ok: true,
      available_namespaces: ["change_quality_qualification", "manager_runtime", "periodic_report", "steward_executor", "todo_replan_cadence"],
      namespace_catalog: {
        schema_version: "machine_configuration_catalog_v0",
        namespaces: [
          {
            namespace: "change_quality_qualification",
            title: "Change quality qualification",
            description: "Live exact-diff qualification policy without added authority.",
            schema_versions: ["change_quality_machine_defaults_v0"],
            configuration_template: changeQualityConfiguration,
            template_status: "ready",
          },
          {
            namespace: "manager_runtime",
            title: "Manager runtime",
            description: "Persistent host-tool profile for owner manager conversations.",
            schema_versions: ["manager_runtime_profile_v0"],
            configuration_template: managerRuntimeConfiguration,
            template_status: "ready",
          },
          {
            namespace: "periodic_report",
            title: "Periodic reports",
            description: "Governed report defaults.",
            schema_versions: ["periodic_report_machine_defaults_v0"],
            configuration_template: periodicConfiguration,
            template_status: "ready",
          },
          {
            namespace: "steward_executor",
            title: "Steward executor",
            description: "Executor, model, reasoning effort, and selection boundary for this machine's steward channel.",
            schema_versions: ["steward_executor_machine_defaults_v0", "steward_executor_machine_defaults_v1"],
            configuration_template: stewardExecutorConfiguration,
            template_status: "ready",
          },
          {
            namespace: "todo_replan_cadence",
            title: "Goal review cadence",
            description: "Live review threshold without added turns, quota, or authority.",
            schema_versions: ["todo_replan_cadence_machine_defaults_v0"],
            configuration_template: cadenceConfiguration,
            template_status: "ready",
          },
        ],
      },
      capability_catalog: {
        schema_version: "capability_configuration_catalog_v0",
        capabilities: [{
          capability_id: "manager_runtime",
          display_name: "Manager runtime",
          description: "Persistent host-tool profile for owner manager conversations.",
          available_scopes: ["machine"],
          machine_namespace: "manager_runtime",
          configuration_editor: {
            schema_version: "capability_configuration_editor_v0",
            editable: true,
            supported_scopes: ["machine"],
            writable_scopes: ["machine"],
            fields: [{
              key: "runtime_profile",
              label: "Runtime profile",
              description: "Restricted uses scoped reads. Trusted owner enables normal host tools while protected operations retain separate checks.",
              input_kind: "select",
              required: true,
              options: ["restricted", "trusted_owner"],
            }],
          },
          default: managerRuntimeConfiguration,
          effective_configuration: {
            schema_version: "capability_configuration_resolution_v0",
            capability_id: "manager_runtime",
            source: "machine_default",
            configuration: managerRuntimeConfiguration,
            inherited: false,
            goal_override_present: false,
            machine_default_present: true,
            effective_revision: "sha256:manager-runtime-effective",
          },
        }, {
          capability_id: "steward_executor",
          display_name: "Steward executor",
          description: "Executor, model, reasoning effort, and selection boundary for this machine's steward channel.",
          available_scopes: ["machine"],
          machine_namespace: "steward_executor",
          configuration_editor: {
            schema_version: "capability_configuration_editor_v0",
            editable: true,
            supported_scopes: ["machine"],
            writable_scopes: ["machine"],
            fields: [{
              key: "selection_policy",
              label: "Selection policy",
              description: "Preferred permits an explicit user choice; pinned rejects another executor; flexible permits fallback only inside the eligible pool.",
              input_kind: "select",
              required: true,
              options: ["preferred", "pinned", "flexible"],
            }, {
              key: "executor_endpoint",
              label: "Primary steward executor",
              description: "The executor this machine's steward channel answers on. The choice outranks the Chat service environment.",
              input_kind: "select",
              required: true,
              options: ["codex", "dsh"],
            }, {
              key: "eligible_endpoints",
              label: "Flexible eligible executors",
              description: "One authorized executor id per line. Required only for flexible selection; include the primary executor.",
              input_kind: "string_list",
              required: false,
            }, {
              key: "executor_model",
              label: "Model",
              description: "Optional model for the selected executor. Leave blank to keep the executor's own default.",
              input_kind: "text",
              required: false,
              nullable: true,
            }, {
              key: "executor_reasoning_effort",
              label: "Reasoning effort",
              description: "Optional reasoning effort for the selected executor. Leave blank to keep the executor's own default.",
              input_kind: "select",
              required: false,
              nullable: true,
              options: ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
            }],
          },
          default: stewardExecutorConfiguration,
          machine_current: stewardExecutorConfiguration,
          effective_configuration: {
            schema_version: "capability_configuration_resolution_v0",
            capability_id: "steward_executor",
            source: "machine_default",
            configuration: stewardExecutorConfiguration,
            inherited: true,
            goal_override_present: false,
            machine_default_present: true,
            effective_revision: "sha256:steward-executor-effective",
          },
        }, ...goalCapabilities.map((capability) => {
          if (capability.capability_id === "periodic_report") {
            return periodicReportCapability({ machineCurrent: periodicConfiguration });
          }
          if (capability.capability_id === "todo_replan_cadence") {
            return withMachineConfiguration(capability, {
              configuration: cadenceConfiguration,
              description: "Live review threshold without added turns, quota, or authority.",
            });
          }
          if (capability.capability_id === "change_quality_qualification") {
            return withMachineConfiguration(capability, {
              configuration: changeQualityConfiguration,
              description: "Live exact-diff qualification policy without added authority.",
            });
          }
          return capability;
        })],
      },
      changed_namespaces: [],
      invalid_namespaces: [],
      machine_configuration: {
        schema_version: "loopx_machine_configuration_v0",
        namespaces: machineNamespaces,
      },
    };
    if (url.pathname === "/api/chat/machine-configuration" && request.method() === "GET") {
      if (state.failNextMachineInspection) {
        state.failNextMachineInspection = false;
        await route.fulfill({ contentType: "application/json", json: { error: "Machine catalog temporarily unavailable" }, status: 503 });
        return;
      }
      await route.fulfill({ contentType: "application/json", json: {
        ...machineConfigurationBase,
        schema_version: "machine_configuration_inspection_v0",
        status: state.machineInspectionStatus,
        revision: "sha256:machine-current",
        invalid_namespaces: state.invalidMachineNamespaces,
        machine_configuration: state.machineInspectionStatus === "invalid"
          ? null
          : machineConfigurationBase.machine_configuration,
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/operator-credential") {
      const body = request.method() === "POST" ? request.postDataJSON() : null;
      if (body) {
        state.operatorCredentialWrites.push(body);
        if (body.clear_provider_key) {
          state.operatorCredential = { ...state.operatorCredential, providerKeyConfigured: false, providerKeySource: "unset", providerKeyFingerprint: null };
        }
        if (typeof body.provider_key === "string" && body.provider_key.trim()) {
          // The fixture never stores the key itself: the readback only ever
          // carries the fingerprint the real projection returns.
          state.operatorCredential = { ...state.operatorCredential, providerKeyConfigured: true, providerKeySource: "machine_store", providerKeyFingerprint: "fixture-fingerprint" };
        }
        if (typeof body.base_url === "string" && body.base_url.trim()) {
          state.operatorCredential = { ...state.operatorCredential, baseUrl: body.base_url.trim(), baseUrlSource: "machine_store" };
        }
        if (body.clear_base_url) {
          state.operatorCredential = { ...state.operatorCredential, baseUrl: null, baseUrlSource: "unset" };
        }
      }
      const credential = state.operatorCredential;
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "operator_provider_credential_projection_v0",
        action: body ? "readback" : undefined,
        store_ref: "fixture-operator-store",
        store_revision: "sha256:fixture-operator-store",
        record_present: credential.providerKeyConfigured || Boolean(credential.baseUrl),
        status: credential.providerKeyConfigured ? "configured" : "absent",
        repair: "",
        provider_key: {
          configured: credential.providerKeyConfigured,
          env_var: credential.providerKeySource === "service_environment" ? "DEEPSEEK_API_KEY" : undefined,
          fingerprint: credential.providerKeyFingerprint,
          source: credential.providerKeySource,
        },
        base_url: { configured: Boolean(credential.baseUrl), source: credential.baseUrlSource, value: credential.baseUrl },
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/machine-configuration/preview" && request.method() === "POST") {
      const body = request.postDataJSON();
      state.machineConfigurationRequests.push({ phase: "preview", ...body });
      await route.fulfill({ contentType: "application/json", json: {
        ...machineConfigurationBase,
        schema_version: "machine_configuration_update_plan_v0",
        status: "preview",
        action: "update",
        current_revision: "sha256:machine-current",
        desired_revision: "sha256:machine-desired",
        plan_revision: "sha256:machine-plan",
        writes_required: 1,
        changed_namespaces: [body.namespace],
        machine_configuration: {
          schema_version: "loopx_machine_configuration_v0",
          namespaces: { ...machineNamespaces, [body.namespace]: body.namespace_configuration },
        },
      }, status: 201 });
      return;
    }
    if (url.pathname === "/api/chat/machine-configuration/apply" && request.method() === "POST") {
      const body = request.postDataJSON();
      state.machineConfigurationRequests.push({ phase: "apply", ...body });
      machineNamespaces[body.namespace] = body.namespace_configuration;
      state.machineInspectionStatus = "configured";
      state.invalidMachineNamespaces = [];
      await route.fulfill({ contentType: "application/json", json: {
        ...machineConfigurationBase,
        schema_version: "machine_configuration_transaction_v0",
        status: "applied",
        plan_revision: body.expected_plan_revision,
        transaction_id: "machine-transaction",
        readback_verified: true,
        rollback_available: true,
        applied_revision: "sha256:machine-desired",
        prior_revision: "sha256:machine-current",
        changed_namespaces: [body.namespace],
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/goal-configuration" && request.method() === "GET") {
      const goalId = url.searchParams.get("goal_id");
      const spawnPolicy = runtime.goalSubagentConfigurations.get(goalId);
      const multiSubagentConfiguration = spawnPolicy ? {
        enabled: spawnPolicy.mode === "multi_subagent" && spawnPolicy.spawn_allowed === true,
        max_children: spawnPolicy.max_children || null,
        allowed_domains: spawnPolicy.allowed_domains ?? [],
      } : undefined;
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "goal_configuration_inspection_v0",
        status: "configured",
        goal_id: goalId,
        revision: "sha256:goal-current",
        available_capabilities: [
          "periodic_report",
          "change_quality_qualification",
          "explore_graph",
          "explore_harness",
          "lark_kanban_heartbeat_sync",
          "lark_event_inbox",
          "peer_task_coordination",
          "multi_subagent",
          "local_authority_shadow",
          "reward_memory",
        ],
        capability_catalog: {
          schema_version: "capability_configuration_catalog_v0",
          capabilities: [...machineConfigurationBase.capability_catalog.capabilities.filter(
            (capability) => !capability.available_scopes.includes("goal"),
          ), ...goalCapabilityCatalog(multiSubagentConfiguration).map((capability) => capability.capability_id === "periodic_report" ? periodicReportCapability({
              machineCurrent: periodicConfiguration,
              effectiveConfiguration: {
                schema_version: "capability_configuration_resolution_v0",
                capability_id: "periodic_report",
                source: "machine_default",
                configuration: periodicConfiguration,
                inherited: true,
                goal_override_present: false,
                machine_default_present: true,
                effective_revision: "sha256:periodic-effective",
              },
            }) : capability)],
        },
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/goal-configuration/preview" && request.method() === "POST") {
      const body = request.postDataJSON();
      state.goalConfigurationRequests.push({ phase: "preview", ...body });
      await route.fulfill({ contentType: "application/json", json: {
        ok: true, schema_version: "goal_configuration_update_plan_v0", status: "preview",
        action: body.capability_id === "multi_subagent" && runtime.goalSubagentConfigurations.has(body.goal_id) ? "update" : "create", current_revision: "absent", desired_revision: "sha256:desired",
        base_revision: "sha256:goal-current", plan_revision: `sha256:goal-plan-${body.capability_id}`, writes_required: 1,
        goal_id: body.goal_id, capability_id: body.capability_id,
        changed_fields: [body.capability_id], goal_configuration: body.configuration,
        capability_catalog: { schema_version: "capability_configuration_catalog_v0", capabilities: [] },
      }, status: 201 });
      return;
    }
    if (url.pathname === "/api/chat/goal-configuration/apply" && request.method() === "POST") {
      const body = request.postDataJSON();
      state.goalConfigurationRequests.push({ phase: "apply", ...body });
      if (body.capability_id === "multi_subagent") {
        runtime.goalSubagentConfigurations.set(body.goal_id, body.configuration.enabled ? {
          mode: "multi_subagent",
          spawn_allowed: true,
          max_children: body.configuration.max_children,
          allowed_domains: body.configuration.allowed_domains,
        } : {
          mode: "default",
          spawn_allowed: false,
          max_children: 0,
          allowed_domains: [],
        });
        await route.fulfill({ contentType: "application/json", json: {
          ok: true, schema_version: "goal_configuration_transaction_v0", status: "applied",
          goal_id: body.goal_id, capability_id: body.capability_id,
          plan_revision: body.expected_plan_revision, applied_revision: "sha256:goal-applied",
          readback_verified: true, changed_fields: [body.capability_id], goal_configuration: body.configuration,
          capability_catalog: { schema_version: "capability_configuration_catalog_v0", capabilities: [] },
        }, status: 200 });
        return;
      }
      await route.fulfill({ contentType: "application/json", json: {
        ok: false, schema_version: "goal_configuration_transaction_v0", status: "partial_write",
        goal_id: body.goal_id, capability_id: body.capability_id,
        plan_revision: body.expected_plan_revision, applied_revision: "sha256:goal-applied",
        source_written: true, shared_sync_pending: true, readback_verified: true,
        changed_fields: ["periodic_report"], goal_configuration: body.configuration,
        capability_catalog: { schema_version: "capability_configuration_catalog_v0", capabilities: [] },
        error: "shared projection did not synchronize",
        recommended_action: `rerun loopx sync-global --goal-id ${body.goal_id}`,
      }, status: 207 });
      return;
    }
    const resumedEvents = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/turns\/([^/]+)\/events$/);
    if (resumedEvents && request.method() === "GET") {
      const sessionId = resumedEvents[1];
      const turnId = resumedEvents[2];
      const answer = "已沿用当前 Goal 与 Agent Session。接下来会先核对状态，再继续推进。";
      await new Promise((resolveWait) => setTimeout(resolveWait, /(中断控制|刷新恢复)/u.test(turnMessages.get(turnId) ?? "") ? 5000 : 1200));
      await route.fulfill({ contentType: "text/event-stream", body: finishTurn(sessionId, turnId, answer), status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/goals/contexts") {
      const fixture = require(resolve(repoRoot, "examples/status.example.json"));
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "loopx_chat_goal_contexts_v0",
        goals: (fixture.run_history?.goals ?? []).map((goal) => ({
          goal_id: goal.id,
          repository: { branch: "codex/lark-goal-topic-binding", identity: "git:github.com/loopx-ai/loopx", label: "loopx-ai/loopx", read_only: true },
        })),
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/lark/apps") {
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "loopx_lark_apps_v0",
        apps: [
          { active: true, app_ref: "mew", brand: "feishu", label: "LoopX Mew", ready: true, reply_ready: true },
          { active: true, app_ref: "mew-research", brand: "feishu", label: "LoopX Research", ready: true, reply_ready: true },
        ],
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/lark/chats") {
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "loopx_lark_group_chats_v0",
        chats: [{ chat_id: "oc_browser_fixture", chat_name: "Product group" }],
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/lark/connections" && request.method() === "GET") {
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        schema_version: "loopx_lark_goal_topic_connections_v0",
        connections: runtime.larkConnections,
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/lark/connections" && request.method() === "POST") {
      const body = request.postDataJSON();
      if (body.connection_id) {
        const existing = runtime.larkConnections.find((item) => item.connection_id === body.connection_id && item.goal_id === body.goal_id);
        if (!existing || body.app_ref || body.chat_id || body.agent_bindings) throw new Error("Editing must select the stored connection without replacing its identity");
        if (body.execute) {
          Object.assign(existing, { agent_id: body.agent_id, ingress_mode: body.ingress_mode, capture_scope: body.capture_scope });
          state.larkWrites.push({ ...body });
        }
        await route.fulfill({ contentType: "application/json", json: { ok: true, status: body.execute ? "connected" : "preview_ready" }, status: 200 });
        return;
      }
      const bindings = Array.isArray(body.agent_bindings)
        ? body.agent_bindings
        : [{ agent_id: body.agent_id ?? null, app_ref: body.app_ref }];
      if (body.execute) {
        const fixture = require(resolve(repoRoot, "examples/status.example.json"));
        const goal = (fixture.run_history?.goals ?? []).find((item) => item.id === body.goal_id);
        for (const binding of bindings) {
          const connectionId = `lark-${body.goal_id}-${binding.agent_id ?? "default"}`;
          runtime.larkConnections = runtime.larkConnections.filter((item) => item.connection_id !== connectionId);
          runtime.larkConnections.push({
            agent_id: body.conversation_kind === "manager" ? "loopx-manager" : binding.agent_id ?? null,
            conversation_kind: body.conversation_kind ?? "goal",
            connection_id: connectionId,
            app_label: binding.app_ref === "mew-research" ? "LoopX Research" : "LoopX Mew", app_ref: binding.app_ref, chat_name: body.chat_name, enabled: true,
            capture_scope: body.capture_scope,
            event_count: 0, health_error_code: "lark_event_delivery_unverified",
            goal_id: body.goal_id, goal_title: goal?.id ?? body.goal_id, incoming_mode: body.incoming_mode,
            ingress_mode: body.ingress_mode,
            last_event_reason: null, last_event_status: null, listener_error_code: null, listener_status: "listening", replied_count: 0,
            reply_mode: "topic_reply", target_ref: "product-group", topic_name: goal?.id ?? body.goal_id,
            topic_setup_required: false, reply_ready: false,
          });
          state.larkWrites.push({ ...body, agent_id: binding.agent_id, app_ref: binding.app_ref });
        }
      }
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        status: body.execute ? "connected" : "preview_ready",
        public_summary: body.execute ? "connected" : "previewed",
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/lark/connections" && request.method() === "DELETE") {
      const connectionId = url.searchParams.get("connection_id");
      runtime.larkConnections = runtime.larkConnections.filter((item) => item.connection_id !== connectionId);
      await route.fulfill({ contentType: "application/json", json: { ok: true, status: "disconnected" }, status: 200 });
      return;
    }
    if (["/api/chat/goal-subagents/dry-run", "/api/chat/goal-subagents/apply"].includes(url.pathname) && request.method() === "POST") {
      if (state.failNextGoalSubagentResponse) {
        state.failNextGoalSubagentResponse = false;
        await route.fulfill({ body: "", contentType: "text/plain", status: 502 });
        return;
      }
      const body = request.postDataJSON();
      const apply = url.pathname.endsWith("/apply");
      const before = runtime.goalSubagentConfigurations.get(body.goal_id)
        ?? { mode: "default", spawn_allowed: false, max_children: 0, allowed_domains: [] };
      const after = body.enabled
        ? { mode: "multi_subagent", spawn_allowed: true, max_children: body.max_children, allowed_domains: body.allowed_domains }
        : { mode: "default", spawn_allowed: false, max_children: 0 };
      const modelConfig = body.model_config === undefined ? before.model_config : body.model_config;
      if (modelConfig) after.model_config = modelConfig;
      const changed = JSON.stringify(before) !== JSON.stringify(after);
      const previewId = `goal-subagents-${body.goal_id}-${JSON.stringify(after)}`;
      if (apply && body.preview_id !== previewId) {
        await route.fulfill({ contentType: "application/json", json: { ok: false, error: "stale Goal sub-agent preview", error_code: "stale_goal_subagent_preview" }, status: 409 });
        return;
      }
      if (apply && changed) {
        runtime.goalSubagentConfigurations.set(body.goal_id, after);
        state.goalSubagentWrites.push({ ...body });
        state.durableWriteCount += 1;
      } else if (!apply) {
        state.goalSubagentPreviews.push({ ...body, preview_id: previewId });
      }
      await route.fulfill({ contentType: "application/json", json: {
        ok: true,
        dry_run: !apply,
        execute: apply,
        written: apply && changed,
        changed,
        goal_configuration_changed: changed,
        goal_id: body.goal_id,
        changed_fields: changed ? ["orchestration"] : [],
        before: { orchestration: before },
        after: { orchestration: after },
        preview_id: previewId,
        feature_summary: { multi_subagent: body.enabled ? "enabled" : "off" },
        codex_host_capacity: {
          alignment_requested: Boolean(body.align_codex_host_capacity),
          configured_children: null,
          counts_main_thread: false,
          new_session_required: Boolean(apply && body.enabled && body.align_codex_host_capacity),
          required_children: body.enabled ? body.max_children : 0,
          status: body.enabled ? "implicit_default_unknown" : "not_required",
          write_required: Boolean(body.enabled && body.align_codex_host_capacity),
          written: Boolean(apply && body.enabled && body.align_codex_host_capacity),
        },
        global_sync: {
          required: changed,
          executed: apply && changed,
          readback: { status: apply && changed ? "verified" : changed ? "not_executed" : "not_required", verified: apply && changed },
        },
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/capabilities") {
      await route.fulfill({ contentType: "application/json", json: {
        ok: true, schema_version: "loopx_chat_capabilities_v1", agent_backend: "multi_adapter",
        sandbox: "read-only", approval_policy: "never", todo_write: "preview_locked",
        manager: {
          scope: "owner_global",
          model: "gpt-6-astra",
          reasoning_effort: "high",
          ...(managerChannelBinding ? { channel_binding: managerChannelBinding } : {}),
          runtime: {
            schema_version: "manager_runtime_effective_profile_v0",
            runtime_profile: "restricted",
            source: "capability_default",
            configuration_revision: "absent",
            standing_grant: "none",
            sandbox: "read-only",
            approval_policy: "never",
            tool_classes: ["loopx_core"],
            status: "ready",
          },
        },
        ...(state.goalSubagentConfigurationEnabled ? { goal_subagent_configuration: "preview_locked" } : {}),
        goal_id: null, streaming: true, resume: true, interrupt: true, typed_actions: true,
        action_kinds: ["goal.create", "goal.lifecycle", "agent.bind", "heartbeat.bind", "monitor.create", "run.correct"],
        adapters: [
          { agent_id: "codex", display_name: "Codex", adapter_kind: "codex_app_server", available: true, streaming: true, resume: true, interrupt: true },
          { agent_id: "claude-code", display_name: "Claude Code", adapter_kind: "claude_code_cli", available: true, streaming: true, resume: true, interrupt: true },
          { agent_id: "offline-agent", display_name: "Offline Agent", adapter_kind: "acp", available: false, streaming: false, resume: false, interrupt: false },
        ].concat(runtimeAgents ?? []),
      }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/sessions" && request.method() === "GET") {
      const requestedGoal = url.searchParams.get("goal_id");
      const requestedAgent = url.searchParams.get("agent_id");
      const requestedChannel = url.searchParams.get("channel_id");
      const matched = [...sessions.values()].filter((session) =>
        (!requestedGoal || session.goal_id === requestedGoal)
        && (!requestedAgent || session.agent_id === requestedAgent)
        && (!requestedChannel || session.channel_id === requestedChannel)
      );
      await route.fulfill({ contentType: "application/json", json: { ok: true, schema_version: "loopx_chat_session_list_v1", sessions: matched }, status: 200 });
      return;
    }
    if (url.pathname === "/api/chat/sessions" && request.method() === "POST") {
      const body = request.postDataJSON();
      if (body.context_kind === "manager" && body.goal_id) throw new Error("Global manager request still carries a project anchor");
      const resolvedGoalId = body.context_kind === "manager" ? "loopx-manager" : body.goal_id;
      const session_id = `session-${body.context_kind}-${resolvedGoalId}-${body.agent_id}`;
      const existing = body.mode === "resume_latest" ? sessions.get(session_id) : null;
      const session = existing ?? { session_id, goal_id: resolvedGoalId, agent_id: body.agent_id, adapter_kind: body.agent_id, channel_id: body.context_kind === "manager" ? "manager" : `goal.${body.goal_id}`, status: "ready", active_turn_id: null, last_error_code: null, created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:00Z", last_activity_at: "2026-08-13T01:00:00Z", resumable: true, ...(body.context_kind === "manager" ? { manager_runtime: { schema_version: "manager_runtime_session_readback_v0", runtime_profile: "restricted", configuration_revision: "absent", status: "ready", sandbox: "read-only", standing_grant: "none", tool_classes: ["loopx_core"] } } : {}) };
      sessions.set(session_id, session);
      messages.set(session_id, messages.get(session_id) ?? []);
      await route.fulfill({ contentType: "application/json", json: { ok: true, agent_id: body.agent_id, goal_id: body.goal_id, resumed: body.mode === "resume_latest", session_id, session }, status: 201 });
      return;
    }
    const loopxMode = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/loopx$/);
    if (loopxMode) {
      const sessionId = decodeURIComponent(loopxMode[1]);
      const session = sessions.get(sessionId);
      if (!session) {
        await route.fulfill({ contentType: "application/json", json: { ok: false, error: "session not found" }, status: 404 });
        return;
      }
      const current = loopxModes.get(sessionId) ?? {
        ok: true,
        session_id: sessionId,
        enabled: false,
        active_turn_id: null,
        conversation_busy: false,
        settings: { execution_config: ".loopx/config/delegations.json" },
        native: { status: "absent" },
        registered_agents: ["lead"],
        paused: false,
        recovery_required: false,
        members: [],
        deliveries: [],
        ingress: [],
      };
      loopxModes.set(sessionId, current);
      if (request.method() === "GET") {
        // A conversation's own mode is its work index: the Todos it dispatched
        // work for and the coordinator bindings it was configured with. Only
        // these Todo identities can make a Goal conversation relevant.
        const deliveries = current.fixturePlanTodoId
          ? [{operation_id: "accepted-analysis", agent_id: "local-analyst", todo_id: current.fixturePlanTodoId, status: "accepted"}]
          : current.deliveries;
        await route.fulfill({ contentType: "application/json", json: {...current, deliveries}, status: 200 });
        return;
      }
      const body = request.postDataJSON();
      state.loopxModeRequests.push({ sessionId, ...body });
      if (body.operation === "pause") {
        const paused = {...current, active_turn_id: null, paused: true, native: {...current.native, status: "paused"}};
        loopxModes.set(sessionId, paused);
        await route.fulfill({json: paused});
        return;
      }
      if (body.operation === "read") {
        if (current.fixtureTeamReadDelayMs) await new Promise(resolveWait => setTimeout(resolveWait, current.fixtureTeamReadDelayMs));
        if (current.fixtureCorrectionEpisode && body.operation_id === "original-analysis") {
          await route.fulfill({json: {ok: true, operation_id: body.operation_id, request_id: "request-original",
            agent_id: "local-analyst", todo_id: "todo_original", status: "accepted", worker_active: false,
            recovery_required: false, artifacts: [{ref: "report.json", sha256: "a".repeat(64), text: '{"cash_flow":90}'}]}});
        } else if (current.fixtureCorrectionEpisode && body.operation_id === "review-objection") {
          await route.fulfill({json: {ok: true, operation_id: body.operation_id, request_id: "request-review",
            agent_id: "independent-reviewer", todo_id: "todo_review", status: "accepted", worker_active: false,
            recovery_required: false, artifacts: [{ref: "objection.json", sha256: "b".repeat(64), text: '{"objection":"The source was superseded"}'}],
            dependencies: [{operation_id: "original-analysis", ref: "report.json", sha256: "a".repeat(64),
              input_ref: "original.json", relation: "responds_to", state: "current"}]}});
        } else if (body.operation_id === "accepted-synthesis") {
          await route.fulfill({json: {ok: true, operation_id: body.operation_id, request_id: "request-synthesis",
            agent_id: "synthesizer", todo_id: "todo_synthesis", status: "accepted", worker_active: false,
            recovery_required: false, artifacts: [{ref: "synthesis.json", sha256: "e".repeat(64), text: '{"accepted_cash_flow":75}'},
              {ref: "report.md", sha256: "8".repeat(64), text: "# Reviewed cash allocation\n\n| Measure | Value |\n|---|---:|\n| Free cash | 75 |\n\nIndependent review retained the original conclusion."}],
            dependencies: [{operation_id: "accepted-analysis", ref: "report.json", sha256: "d".repeat(64),
              input_ref: "accepted-input.json", relation: "uses", state: current.fixtureAdoptionState ?? "current"},
              {operation_id: "accepted-analysis", ref: "report.md", sha256: "9".repeat(64), input_ref: "analysis.md", relation: "responds_to", state: "current"}]}});
        } else if (body.operation_id !== "accepted-analysis") {
          await route.fulfill({status: 409, json: {ok: false, error: "delegation artifact unavailable"}});
        } else {
          await route.fulfill({json: {ok: true, operation_id: body.operation_id, request_id: "request-analysis",
            agent_id: "local-analyst", todo_id: current.fixturePlanTodoId ?? "todo_analysis", status: "accepted", worker_active: false,
            recovery_required: false,
            ...(current.fixtureCorrectionEpisode ? {dependencies: [
              {operation_id: "original-analysis", ref: "report.json", sha256: "a".repeat(64),
                input_ref: "original.json", relation: "revises", state: "current"},
              {operation_id: "review-objection", ref: "objection.json", sha256: "b".repeat(64),
                input_ref: "objection.json", relation: "responds_to", state: "current"}]} : {}),
            ...(current.fixtureAdoptionState ? {adoptions: [{requester_agent_id: "lead", consumer_operation_id: "accepted-synthesis",
              consumer_request_id: "request-synthesis", consumer_agent_id: "synthesizer", consumer_todo_id: "todo_synthesis",
              source_artifacts: [{ref: "report.json", sha256: "d".repeat(64)}],
              consumer_artifacts: [{ref: "synthesis.json", sha256: "e".repeat(64)},
                ...(current.fixturePlanTodoId ? [{ref: "report.md", sha256: "8".repeat(64)}] : [])], state: current.fixtureAdoptionState}]} : {}),
            artifacts: [{ref: "report.json", sha256: "d".repeat(64),
              text: '{"cash_flow":75,"note":"<script>window.artifactExecuted=true</script>"}'},
              {ref: "report.md", sha256: "9".repeat(64), text: "# Cash allocation\n\n| Measure | Value |\n|---|---:|\n| Free cash | 75 |\n\n[Source](https://example.org/report)\n<script>window.artifactExecuted=true</script>"}]}});
        }
        return;
      }
      if (body.operation === "message") {
        current.ingress.push({client_ingress_id: body.operation_id, mode: "loopx_inbox", status: "pending"});
        await route.fulfill({json: {ok: true, status: "pending", delivery_mode: "inbox"}});
        return;
      }
      if (body.operation === "operations") {
        if (!current.settings.agent_id) {
          await route.fulfill({status: 400, json: {ok: false, error: "configure a coordinator identity first"}});
          return;
        }
        if (current.fixtureTeamInventoryError) {
          await route.fulfill({status: 503, json: {ok: false, error: "delegation inventory unavailable"}});
          return;
        }
        const items = body.cursor ? [{record_id: "c".repeat(64), operation_id: "needs-recovery",
          agent_id: "cloud-reviewer", todo_id: "todo_review", status: "running", worker_active: false, recovery_required: true}]
          : [{record_id: "a".repeat(64), operation_id: "accepted-analysis", agent_id: "local-analyst",
            todo_id: current.fixturePlanTodoId ?? "todo_analysis", status: "accepted", worker_active: false, recovery_required: false,
            artifacts: [{ref: "report.json", sha256: "d".repeat(64)}, {ref: "report.md", sha256: "9".repeat(64)}]},
          ...(!current.fixturePlanTodoId || current.fixtureInventoryGap
            ? [{record_id: "b".repeat(64), operation_id: "stale-output", status: "unavailable", recovery_required: null}]
            : [])];
        await route.fulfill({json: {items, has_more: !body.cursor, next_cursor: body.cursor ? null : "b".repeat(64),
          page_readback_complete: Boolean(body.cursor || (current.fixturePlanTodoId && !current.fixtureInventoryGap))}});
        return;
      }
      if (body.operation === "inspect") {
        if (body.binding_id === "review") {
          await route.fulfill({status: 503, json: {error: "Review runtime unavailable"}});
          return;
        }
        await route.fulfill({json: {state: body.binding_id === "synthesis" ? "launchable" : "runtime_unverified",
          turn_eligible: true, acceptance_ready: true, turn_route: "ready_for_host",
          executor: {host: "generic-cli", available: body.binding_id === "synthesis" ? true : null, reason: null, profile: null}}});
        return;
      }
      if (body.operation !== "configure") {
        await route.fulfill({ contentType: "application/json", json: { ok: false, error: "unsupported fixture operation" }, status: 400 });
        return;
      }
      const configured = {
        ...current,
        settings: {
          ...body.settings,
          execution_config: current.settings.execution_config,
        },
        members: [{id: "analysis", agent_id: "local-analyst", todo_id: "todo_analysis"},
          {id: "synthesis", agent_id: "synthesizer", todo_id: "todo_synthesis"},
          {id: "review", agent_id: "cloud-reviewer", todo_id: "todo_review"}],
      };
      loopxModes.set(sessionId, configured);
      await route.fulfill({ contentType: "application/json", json: configured, status: 200 });
      return;
    }
    const snapshot = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)$/);
    if (snapshot && request.method() === "GET") {
      const session = sessions.get(snapshot[1]);
      await route.fulfill({ contentType: "application/json", json: { ok: true, schema_version: "loopx_chat_store_v1", session, messages: messages.get(snapshot[1]) ?? [], active_turn: session?.active_turn_id ? { turn_id: session.active_turn_id, status: "running", response: null } : null }, status: session ? 200 : 404 });
      return;
    }
    const turns = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/turns$/);
    if (turns && request.method() === "POST") {
      const body = request.postDataJSON();
      const turn_id = `turn-${Date.now()}`;
      turnMessages.set(turn_id, body.message);
      const current = sessions.get(turns[1]);
      if (current) sessions.set(turns[1], { ...current, active_turn_id: turn_id, status: "busy", updated_at: "2026-08-13T01:00:01Z" });
      messages.get(turns[1])?.push({ message_id: `${turn_id}-user`, turn_id, role: "user", text: body.message, created_at: "2026-08-13T01:00:01Z" });
      state.turnRequests.push({ message: body.message, sessionId: turns[1], turnId: turn_id });
      await route.fulfill({ contentType: "application/json", json: { ok: true, session_id: turns[1], turn_id, created: true, status: "running", events_url: `/events/${turns[1]}/${turn_id}` }, status: 202 });
      return;
    }
    const interrupt = url.pathname.match(/^\/api\/chat\/sessions\/([^/]+)\/turns\/([^/]+)\/interrupt$/);
    if (interrupt && request.method() === "POST") {
      const current = sessions.get(interrupt[1]);
      if (current) sessions.set(interrupt[1], { ...current, active_turn_id: null, status: "ready", updated_at: "2026-08-13T01:00:02Z" });
      state.interrupts.push({ sessionId: interrupt[1], turnId: interrupt[2] });
      await route.fulfill({ contentType: "application/json", json: { ok: true, session_id: interrupt[1], turn_id: interrupt[2], status: "interrupted" }, status: 200 });
      return;
    }
    await route.fulfill({ contentType: "application/json", json: { ok: true }, status: 200 });
  });
  await page.route("**/events/**", async (route) => {
    const parts = new URL(route.request().url()).pathname.split("/").filter(Boolean);
    const sessionId = parts[1];
    const turnId = parts[2];
    const operatorMessage = turnMessages.get(turnId) ?? "";
    const protectedAction = operatorMessage === "请合并 PR #123"
      ? { operation: "merge", target: "PR #123", summary: "准备 PR #123 的受保护合并预览。" }
      : operatorMessage === "请合并我刚才说的那个"
        ? { operation: "merge", target: "PR #999", summary: "模型错误补出了用户没有提供的目标。" }
      : null;
    const scriptedAnswer = typeof state.answerForMessage === "function" ? state.answerForMessage(operatorMessage) : null;
    const answer = scriptedAnswer || (operatorMessage.startsWith("我现在该做什么？")
      ? "管家已读取当前授权范围的 Goal 证据。"
      : operatorMessage === "请只回复：合并后真实回复已收到"
      ? "合并后真实回复已收到"
      : operatorMessage === "请分析：合并 PR #123 后会有什么风险"
        ? "主要风险是检查未完成或目标分支发生变化；这里只做分析，不会创建合并预览。"
          : operatorMessage === "请合并"
            ? "请告诉我要合并的具体 PR 或 MR；在目标明确前不会创建执行预览。"
          : operatorMessage === "请合并我刚才说的那个"
            ? "这个指代不够明确，请提供具体 PR 或 MR。"
          : protectedAction
            ? "我识别到一个明确的合并请求。LoopX 会先展示受保护操作预览，不会直接执行。"
            : "已沿用当前 Goal 与 Agent Session。接下来会先核对状态，再继续推进。");
    await new Promise((resolveWait) => setTimeout(resolveWait, /(中断控制|刷新恢复)/u.test(operatorMessage) ? 5000 : 1200));
    await route.fulfill({ contentType: "text/event-stream", body: finishTurn(sessionId, turnId, answer, protectedAction), status: 200 });
  });
  await page.route("**/api/actions?**", async (route) => {
    const url = new URL(route.request().url());
    const goalId = url.searchParams.get("goal_id");
    const contextKind = url.searchParams.get("context_kind");
    const proposals = Array.from(actionProposals.values()).filter((proposal) => {
      if (proposal.status === "cancelled") return false;
      if (goalId && (proposal.context?.goal_id ?? proposal.normalized_parameters?.goal_id) !== goalId) return false;
      return !contextKind || proposal.context?.kind === contextKind;
    });
    await route.fulfill({ contentType: "application/json", json: { ok: true, schema_version: "loopx_chat_action_list_v1", proposals }, status: 200 });
  });
  await page.route("**/api/actions/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/actions/preview") {
      const body = request.postDataJSON();
      const actionDelayMs = state.nextActionPreviewDelayMs;
      state.nextActionPreviewDelayMs = 0;
      const lifecycleDelayMs = body.action_kind === "goal.lifecycle" ? state.nextLifecyclePreviewDelayMs : 0;
      state.nextLifecyclePreviewDelayMs = 0;
      const previewDelayMs = Math.max(actionDelayMs, lifecycleDelayMs);
      if (previewDelayMs > 0) await new Promise((resolveWait) => setTimeout(resolveWait, previewDelayMs));
      if (state.failNextActionPreview) {
        state.failNextActionPreview = false;
        await route.fulfill({
          contentType: "application/json",
          json: { error: "Action preview temporarily unavailable", error_code: "preview_unavailable", ok: false },
          status: 503,
        });
        return;
      }
      if (body.action_kind === "goal.lifecycle" && state.failNextLifecyclePreview) {
        state.failNextLifecyclePreview = false;
        await route.fulfill({
          contentType: "application/json",
          json: { error: "Lifecycle preview temporarily unavailable", error_code: "preview_unavailable", ok: false },
          status: 503,
        });
        return;
      }
      const proposal_id = `proposal-${body.idempotency_key}`;
      actionKinds.set(proposal_id, body.action_kind);
      state.actionPreviews.push({ ...body, proposalId: proposal_id });
      const proposal = {
        schema_version: "loopx_chat_action_proposal_v1", proposal_id, action_kind: body.action_kind,
        summary: body.summary, normalized_parameters: body.normalized_parameters, context: body.context,
        expected_state_fingerprint: "fixture-r1", permission_classification: "durable_write",
        validation_evidence: ["fixture validation"], available_transitions: ["apply", "cancel"],
        status: "preview_ready", receipt: null, stale: null, created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:00Z",
      };
      if (body.action_kind === "goal.lifecycle" && state.nextLifecycleProposalPatch) {
        Object.assign(proposal, state.nextLifecycleProposalPatch);
        state.nextLifecycleProposalPatch = null;
      }
      actionProposals.set(proposal_id, proposal);
      await route.fulfill({ contentType: "application/json", json: { ok: true, proposal }, status: 201 });
      return;
    }
    const apply = url.pathname.match(/^\/api\/actions\/(.+)\/apply$/);
    if (apply) {
      state.actionApplies.push(apply[1]);
      if (actionKinds.get(apply[1]) === "heartbeat.bind" && !state.allowNextHeartbeatApply) {
        await route.fulfill({ contentType: "application/json", json: { ok: false, schema_version: "loopx_chat_action_gate_v1", error: "Host activation required", error_code: "protected_action", gate: { kind: "host_activation_required", summary: "需要 Codex App 宿主创建 Heartbeat 自动化。", next_action: "确认宿主自动化后重新验证。" }, write_attempted: false }, status: 409 });
        return;
      }
      if (actionKinds.get(apply[1]) === "heartbeat.bind") state.allowNextHeartbeatApply = false;
      const actionKind = actionKinds.get(apply[1]) ?? "goal.create";
      const preview = state.actionPreviews.find((item) => item.proposalId === apply[1]);
      const lifecycleDelayMs = actionKind === "goal.lifecycle" ? state.nextLifecycleApplyDelayMs : 0;
      state.nextLifecycleApplyDelayMs = 0;
      if (lifecycleDelayMs > 0) await new Promise((resolveWait) => setTimeout(resolveWait, lifecycleDelayMs));
      if (actionKind === "goal.lifecycle" && state.failNextLifecycleApply) {
        state.failNextLifecycleApply = false;
        await route.fulfill({
          contentType: "application/json",
          json: {
            error: "Lifecycle gate changed before apply",
            error_code: "protected_action",
            gate: { kind: "goal_lifecycle_gate", summary: "Goal 状态已变化，请重新确认。" },
            ok: false,
            write_attempted: false,
          },
          status: 409,
        });
        return;
      }
      if (actionKind === "goal.lifecycle" && state.nextLifecycleApplyOutcome) {
        const outcome = state.nextLifecycleApplyOutcome;
        state.nextLifecycleApplyOutcome = null;
        const proposal = { ...actionProposals.get(apply[1]),
          status: outcome === "stale" ? "stale" : "applied",
          stale: outcome === "stale" ? { current_state_fingerprint: "fixture-r2" } : null,
          receipt: outcome === "stale" ? null : { projection_verified: outcome.startsWith("mismatch-") },
          ...(outcome === "mismatch-id" ? { proposal_id: "another-proposal" } : {}),
          ...(outcome === "mismatch-goal" ? { normalized_parameters: { goal_id: "other-goal", operation: "stop" } } : {}),
          ...(outcome === "mismatch-operation" ? { normalized_parameters: { goal_id: "product-release", operation: "resume" } } : {}),
        };
        actionProposals.set(apply[1], proposal);
        await route.fulfill({ contentType: "application/json", status: outcome === "stale" ? 409 : 200,
          json: outcome === "stale" ? { ok: false, error_code: "action_stale", error: "Source state changed", proposal } : { ok: true, proposal } });
        return;
      }
      let acceptedTurn = null;
      if (actionKind === "run.correct" && preview) {
        const sessionId = preview.normalized_parameters.session_id;
        const turnId = `turn-${++turnCounter}`;
        acceptedTurn = { session_id: sessionId, turn_id: turnId, status: "queued", created: true };
        turnMessages.set(turnId, preview.normalized_parameters.message);
        state.turnRequests.push({ message: preview.normalized_parameters.message, sessionId, turnId });
        const active = sessions.get(sessionId) ?? {
          session_id: sessionId,
          goal_id: preview.normalized_parameters.goal_id,
          agent_id: "codex",
          adapter_kind: "codex",
          channel_id: `goal.${preview.normalized_parameters.goal_id}`,
          active_turn_id: null,
          status: "ready",
          resumable: true,
        };
        sessions.set(sessionId, { ...active, active_turn_id: turnId, status: "busy" });
        const sessionMessages = messages.get(sessionId) ?? [];
        sessionMessages.push({ message_id: `${turnId}-user`, turn_id: turnId, role: "user", text: preview.normalized_parameters.message, created_at: "2026-08-13T01:00:01Z" });
        messages.set(sessionId, sessionMessages);
      }
      if (actionKind === "goal.lifecycle" && preview) {
        state.goalActivationStates.set(
          preview.normalized_parameters.goal_id,
          preview.normalized_parameters.operation === "stop" ? "stopped" : "active",
        );
      }
      const resourceKey = `${actionKind}:${apply[1]}`;
      const replay = state.durableResources.has(resourceKey);
      if (!replay) {
        state.durableResources.add(resourceKey);
        state.durableWriteCount += 1;
      }
      // Injected proposals live in the action store, not in the session
      // previews, so the plan a confirmed card carries has to be read there.
      const teamPlanReceipt = teamPlanApplyReceipt(actionProposals.get(apply[1]));
      if (teamPlanReceipt && replay) teamPlanReceipt.outcome = "team_plan_commit_recovered";
      const proposal = {
        schema_version: "loopx_chat_action_proposal_v1", proposal_id: apply[1], action_kind: actionKind,
        summary: "已应用", normalized_parameters: preview?.normalized_parameters ?? actionProposals.get(apply[1])?.normalized_parameters ?? {}, context: preview?.context ?? actionProposals.get(apply[1])?.context ?? {}, expected_state_fingerprint: "fixture-r1",
        permission_classification: "durable_write", validation_evidence: [], available_transitions: ["apply", "cancel"],
        status: "applied", receipt: teamPlanReceipt ?? { projection_verified: true, receipt_id: "fixture-receipt" }, stale: null, created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:01Z",
      };
      actionProposals.set(apply[1], proposal);
      if (actionKind === "team.plan" && state.loseNextTeamPlanResponse) {
        state.loseNextTeamPlanResponse = false;
        await route.fulfill({ contentType: "application/json", status: 503, json: { ok: false, error: "Assignment response unavailable", error_code: "team_plan_response_lost" } });
        return;
      }
      await route.fulfill({ contentType: "application/json", json: { ok: true, proposal, turn: acceptedTurn }, status: acceptedTurn ? 202 : 200 });
      return;
    }
    const cancel = url.pathname.match(/^\/api\/actions\/(.+)\/cancel$/);
    if (cancel) {
      state.actionCancels.push(cancel[1]);
      const proposal = {
        schema_version: "loopx_chat_action_proposal_v1", proposal_id: cancel[1], action_kind: actionKinds.get(cancel[1]) ?? "goal.create",
        summary: "已取消", normalized_parameters: {}, context: {}, expected_state_fingerprint: "fixture-r1",
        permission_classification: "durable_write", validation_evidence: [], available_transitions: ["apply", "cancel"],
        status: "cancelled", receipt: null, stale: null, created_at: "2026-08-13T01:00:00Z", updated_at: "2026-08-13T01:00:01Z",
      };
      actionProposals.set(cancel[1], proposal);
      await route.fulfill({ contentType: "application/json", json: { ok: true, proposal }, status: 200 });
      return;
    }
    const transition = url.pathname.match(/^\/api\/actions\/(.+)\/(defer|reject|regenerate)$/);
    if (transition) {
      const existing = actionProposals.get(transition[1]);
      const nextId = transition[2] === "regenerate" ? `${transition[1]}-regenerated` : transition[1];
      const proposal = {
        ...(existing ?? {}),
        schema_version: "loopx_chat_action_proposal_v1",
        proposal_id: nextId,
        action_kind: existing?.action_kind ?? actionKinds.get(transition[1]) ?? "goal.create",
        summary: existing?.summary ?? "已更新决定",
        normalized_parameters: existing?.normalized_parameters ?? {},
        context: existing?.context ?? {},
        expected_state_fingerprint: "fixture-r1",
        permission_classification: "durable_write",
        validation_evidence: [],
        available_transitions: ["apply", "cancel"],
        status: transition[2] === "defer" ? "deferred" : transition[2] === "reject" ? "rejected" : "preview_ready",
        receipt: null,
        stale: null,
        created_at: existing?.created_at ?? "2026-08-13T01:00:00Z",
        updated_at: "2026-08-13T01:00:01Z",
      };
      actionProposals.set(nextId, proposal);
      state.actionTransitions.push({ proposalId: transition[1], transition: transition[2] });
      await route.fulfill({ contentType: "application/json", json: { ok: true, proposal }, status: 200 });
      return;
    }
    await route.fulfill({ contentType: "application/json", json: { ok: true }, status: 200 });
  });
  return state;
}
