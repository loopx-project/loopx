#!/usr/bin/env node
// Browser-level smoke for the canonical Chinese-first dashboard home.

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { existsSync } from "node:fs";
import { mkdir, rm, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const dashboardDir = resolve(repoRoot, "apps/presentation/dashboard");
const projectionResponse = require(
  resolve(dashboardDir, "src/data/fixtures/presentation-projection.example.json"),
);
const projectionDetailRef = {
  extension_id: projectionResponse.projection.extension_id,
  surface_id: projectionResponse.projection.surface_id,
  extension_revision: projectionResponse.projection.extension_revision,
  payload_sha256: projectionResponse.projection.payload_sha256,
};
const fixtureName = "status.home.browser-smoke.json";
const fixturePath = resolve(dashboardDir, "public", fixtureName);
const emptyFixtureName = "status.home.browser-smoke.empty.json";
const emptyFixturePath = resolve(dashboardDir, "public", emptyFixtureName);
const duplicateSurfaceFixtureName = "status.home.browser-smoke.duplicate-surface.json";
const duplicateSurfaceFixturePath = resolve(
  dashboardDir,
  "public",
  duplicateSurfaceFixtureName,
);
const projectionFixtureName = "status.home.browser-smoke.projection.json";
const projectionFixturePath = resolve(dashboardDir, "public", projectionFixtureName);
const visualOutputDir = resolve(repoRoot, "output/playwright/dashboard-home-visual-acceptance");
const port = Number(process.env.LOOPX_DASHBOARD_HOME_SMOKE_PORT ?? "5194");

const quotaEligible = {
  compute: 1,
  window_hours: 24,
  slot_minutes: 1,
  allowed_slots: 1440,
  spent_slots: 3,
  state: "eligible",
  reason: "fixture eligible quota",
};

const quotaFocusWait = {
  ...quotaEligible,
  spent_slots: 8,
  state: "focus_wait",
  reason: "fixture outcome floor blocks further spend",
  handoff_outcome_floor_block: true,
  safe_bypass_allowed: true,
  safe_bypass_kind: "outcome_floor_recovery",
  safe_bypass_policy: "Outcome-floor recovery only: attempt one bounded ranker_or_cross_domain_evidence evidence segment or write back a concrete blocker.",
  must_advance: ["ranker_or_cross_domain_evidence"],
  avoid: ["clean_downstream_surface_propagation", "synthetic_only_test_chain"],
  post_handoff_outcome_gap_streak: 3,
};

const goalSpecs = [
  {
    id: "showcase-user-gate-safe-side-path",
    domain: "showcase-user-gate-fixture",
    status: "state_refreshed",
    waiting_on: "user_or_controller",
    quota: { ...quotaEligible, state: "operator_gate" },
    userTodos: { open: 1, done: 3, total: 4, next: "请确认 owner 选项 A/B 的取舍。" },
    userTodoItems: [
      { done: false, text: "请确认 owner 选项 A/B 的取舍；未确认前不推进 gated route。" },
      { done: true, text: "已读完核心迁移文档第 8 节，并确认当前结论。" },
      { done: true, text: "已确认 item embedding 不阻塞当前 P0 i2i 路径。" },
      { done: true, text: "已确认 P0 degrade 走统一标准品策略。" },
    ],
    agentTodos: { open: 4, done: 0, total: 4, next: "推进与 owner 决策独立的 safe side path。" },
    agentTodoItems: [
      { done: false, text: "推进与 owner 决策独立的 safe side path，输出公开可复现证据。" },
      { done: false, text: "复查公开边界，确认 showcase 数据不包含内部项目名或生产细节。" },
      { done: false, text: "按安全再生成计划只写私有 tmp_data，不触碰生产配置。" },
      { done: false, text: "把关闭的 P0 阻塞和剩余 gate 写回迁移目标状态。" },
    ],
    latest: {
      generated_at: "2026-01-01T00:00:00+00:00",
      classification: "state_refreshed",
      delivery_batch_scale: "multi_surface",
      health_check: "fixture platform migration state",
      json_exists: true,
      markdown_exists: true,
    },
  },
  {
    id: "showcase-creator-operator",
    domain: "creator-operator-fixture",
    status: "state_refreshed",
    waiting_on: "codex",
    quota: quotaEligible,
    userTodos: { open: 0, done: 1, total: 1, next: null },
    userTodoItems: [
      { done: true, text: "用户已授权 creator operator 使用合成素材主动推进。" },
    ],
    agentTodos: { open: 4, done: 0, total: 4, next: "整理热点、洞察、语料和创作 backlog。" },
    agentTodoItems: [
      { done: false, text: "整理各平台热点，形成按偏好排序的创作候选。" },
      { done: false, text: "提炼热点洞察，补充素材库和语料库。" },
      { done: false, text: "把可用素材转成文章或视频脚本草稿。" },
      { done: false, text: "每次产出都写回 showcase backlog 和证据 ledger。" },
    ],
    latest: {
      generated_at: "2026-01-01T00:01:00+00:00",
      classification: "state_refreshed",
      delivery_batch_scale: "implementation",
      health_check: "fixture creator-operator active-delivery authorization",
      json_exists: true,
      markdown_exists: true,
    },
  },
  {
    id: "showcase-side-agent-self-iteration",
    displayName: "Showcase peer agent self iteration",
    domain: "side-bypass-fixture",
    status: "state_refreshed",
    waiting_on: "codex",
    quota: quotaFocusWait,
    userTodos: { open: 1, done: 0, total: 1, next: "提供或批准 hard_category held-out / paired eval 范围。" },
    userTodoItems: [
      { done: false, text: "提供或批准 candidate-specific hard_category held-out / paired eval 范围。" },
    ],
    agentTodos: { open: 3, done: 1, total: 4, next: "产出排序器 / 跨域证据，否则不花配额写 blocker。" },
    agentTodoItems: [
      { done: false, text: "产出排序器 / 跨域证据，证明不是单面小步。" },
      { done: false, text: "若证据范围仍不可用，写回具体 blocker 并停止花配额。" },
      { done: false, text: "禁止继续 summary / queue / field 的表层传播。" },
      { done: true, text: "已识别 outcome_floor_recovery 分支并拦住普通 delivery。" },
    ],
    handoffReadiness: {
      ready: true,
      codex_ready: true,
      source: "project_asset",
      quota_state: "focus_wait",
      handoff_status: "post_handoff_run_seen",
      post_handoff_run_seen: true,
      post_handoff_outcome_gap_streak: 3,
      post_handoff_latest_run: {
        generated_at: "2026-01-01T00:02:00+00:00",
        classification: "state_refreshed",
        delivery_batch_scale: "single_surface",
        delivery_outcome: "outcome_gap",
        health_check: "fixture side-bypass outcome gap",
        json_exists: true,
        markdown_exists: true,
      },
    },
    latest: {
      generated_at: "2026-01-01T00:02:00+00:00",
      classification: "state_refreshed",
      delivery_batch_scale: "single_surface",
      delivery_outcome: "outcome_gap",
      health_check: "fixture side-bypass outcome gap",
      json_exists: true,
      markdown_exists: true,
    },
  },
  {
    id: "loopx-meta",
    domain: "loopx-fixture",
    status: "dashboard_home_chinese_operator_copy_contract",
    waiting_on: "codex",
    quota: { ...quotaEligible, spent_slots: 9 },
    userTodos: { open: 0, done: 1, total: 1, next: null },
    userTodoItems: [
      { done: true, text: "用户确认分享页应以中文控制面为主屏。" },
    ],
    agentTodos: { open: 3, done: 1, total: 4, next: "拆分 dependency blocker 和 current-goal blocker。" },
    agentTodoItems: [
      { done: false, text: "拆分依赖阻塞和当前目标阻塞，避免 meta 可执行回合空转。" },
      {
        done: false,
        text: "增加自动 backlog 候选面，让 P1/P2 可持续推进。",
        todo_id: "todo_dashboard_search_meta_backlog",
        priority: "P1",
        status: "open",
        task_class: "advancement_task",
        action_kind: "dashboard_todo_search_fixture",
        claimed_by: "codex-side-bypass",
      },
      { done: false, text: "统一多项目看板的 serve-status --global-registry 命令说明。" },
      { done: true, text: "已硬化 heartbeat prompt，依赖项目 todo 不再吃掉当前 goal turn。" },
    ],
    controlPlane: {
      self_repair: {
        enabled: true,
        allow_health_blocker_repair: true,
        allow_waiting_projection_repair: true,
      },
    },
    orchestration: {
      mode: "multi_subagent",
      spawn_allowed: true,
      max_children: 2,
      allowed_domains: ["docs", "validation"],
    },
    latest: {
      generated_at: "2026-01-01T00:03:00+00:00",
      classification: "dashboard_home_chinese_operator_copy_contract",
      delivery_batch_scale: "multi_surface",
      delivery_outcome: "primary_goal_outcome",
      health_check: "fixture home copy contract",
      json_exists: true,
      markdown_exists: true,
    },
  },
];

function projectAssetFor(spec) {
  return {
    owner: spec.waiting_on,
    gate: spec.userTodos.open > 0 ? "user_todo" : "none",
    next_action: spec.agentTodos.next ?? "continue from current fixture state",
    stop_condition: "fixture stop condition",
    user_todos: spec.userTodos,
    agent_todos: spec.agentTodos,
    quota: spec.quota,
    control_plane: spec.controlPlane,
    orchestration: spec.orchestration,
    latest_validation: {
      generated_at: spec.latest.generated_at,
      classification: spec.latest.classification,
      summary: spec.latest.health_check,
    },
  };
}

function todoGroupFor(spec, role) {
  const summary = role === "user" ? spec.userTodos : spec.agentTodos;
  const rawItems = role === "user" ? spec.userTodoItems : spec.agentTodoItems;
  return {
    source_section: role === "user" ? "User Todo / Owner Review Reading Queue" : "Agent Todo",
    total_count: summary.total,
    open_count: summary.open,
    done_count: summary.done,
    items: (rawItems ?? []).map((item, index) => ({
      index: index + 1,
      done: item.done,
      text: item.text,
      todo_id: item.todo_id,
      priority: item.priority,
      status: item.status,
      task_class: item.task_class,
      action_kind: item.action_kind,
      claimed_by: item.claimed_by,
      review_materials: [],
    })),
  };
}

const researchDetailRef = { ...projectionDetailRef };

const statusFixture = {
  ok: true,
  registry: "./fixtures/registry.global.json",
  runtime_root: "./fixtures/runtime",
  goal_count: goalSpecs.length,
  run_count: goalSpecs.length,
  status_contract: {
    schema_version: 2,
    minimum_dashboard_schema_version: 2,
    producer: "loopx status",
    reload_hint: "scripts/macos-dashboard-launchagent.sh restart",
  },
  contract: {
    ok: true,
    summary: { errors: 0, warnings: 0, checks: 1 },
    errors: [],
    warnings: [],
    checks: ["public-safe dashboard home fixture"],
  },
  global_registry: {
    available: true,
    ok: true,
    registry: "./fixtures/registry.global.json",
    current_registry: "./fixtures/registry.global.json",
    current_registry_is_global: true,
    global_goal_count: goalSpecs.length,
    current_goal_count: goalSpecs.length,
    source_registry_count: 4,
    summary: { high: 0, action: 0, info: 0, checks: 1, findings: 0 },
    findings: [],
    checks: ["public-safe dashboard home fixture"],
  },
  attention_queue: {
    available: true,
    item_count: goalSpecs.length,
    needs_user_or_controller: 1,
    needs_controller: 0,
    needs_codex: 3,
    watching_external_evidence: 0,
    autonomous_backlog_candidates: {
      source: "attention_queue.agent_todos",
      open_count: 2,
      items: [
        {
          goal_id: "loopx-meta",
          status: "dashboard_home_chinese_operator_copy_contract",
          waiting_on: "codex",
          quota_state: "eligible",
          priority: "P1",
          todo_index: 2,
          text: "增加自动 backlog 候选面，让 P1/P2 可持续推进。",
          source: "agent_todos",
        },
        {
          goal_id: "showcase-creator-operator",
          status: "state_refreshed",
          waiting_on: "codex",
          quota_state: "eligible",
          priority: "P2",
          todo_index: 1,
          text: "整理热点、洞察、语料和创作 backlog。",
          source: "agent_todos",
        },
      ],
    },
    items: goalSpecs.map((spec) => {
      const sideBypass = goalSpecs.find((goal) => goal.id === "showcase-side-agent-self-iteration");
      const sideBypassUserTodo = sideBypass?.userTodoItems?.find((todo) => !todo.done);
      return {
        goal_id: spec.id,
        status: spec.status,
        waiting_on: spec.waiting_on,
        severity: "action",
        recommended_action: spec.agentTodos.next ?? "continue from current fixture state",
        project_asset: projectAssetFor(spec),
        handoff_readiness: spec.handoffReadiness,
        stale_latest_run_warning: spec.id === "loopx-meta"
          ? {
              severity: "high",
              requires_refresh_state: true,
              reason: "latest_run state is stale",
              recommended_action: "run refresh-state before trusting latest_run-derived routing",
            }
          : null,
        quota: spec.quota,
        control_plane: spec.controlPlane,
        user_todos: todoGroupFor(spec, "user"),
        agent_todos: todoGroupFor(spec, "agent"),
        dependency_blockers: spec.id === "loopx-meta" && sideBypassUserTodo
          ? {
              source: "attention_queue.user_todos",
              open_count: 1,
              items: [{
                goal_id: "showcase-side-agent-self-iteration",
                status: "state_refreshed",
                waiting_on: "codex",
                severity: "action",
                index: 1,
                text: sideBypassUserTodo.text,
                source: "user_todos",
              }],
            }
          : null,
        source: "fixture",
      };
    }),
  },
  run_history: {
    available: true,
    goal_count: goalSpecs.length,
    run_count: goalSpecs.length,
    goals: goalSpecs.map((spec) => ({
      id: spec.id,
      display_name: spec.displayName,
      domain: spec.domain,
      status: spec.status,
      lifecycle_phase: "fixture",
      lifecycle_flags: ["fixture"],
      registry_member: true,
      legacy_runtime_goal: false,
      adapter_kind: "dashboard_home_fixture",
      adapter_status: "connected",
      quota: spec.quota,
      control_plane: spec.controlPlane,
      spawn_policy: spec.orchestration,
      index_exists: true,
      raw_index_records: 1,
      unique_runs: 1,
      latest_runs: [{ ...spec.latest, goal_id: spec.id }],
    })),
    recent_runs: goalSpecs.map((spec) => ({ ...spec.latest, goal_id: spec.id })),
  },
  usage_summary: {
    available: true,
    source: "run_history",
    sample_run_count: 4,
    proxy_note: "public-safe dashboard home fixture",
    totals: {
      runs_24h: 8,
      runs_7d: 8,
      quota_spend_slots_24h: 6,
      quota_spend_slots_7d: 6,
      automation_run_count_24h: 5,
      automation_run_count_7d: 5,
      progress_signal_run_count_24h: 4,
      progress_signal_run_count_7d: 4,
    },
    goals: goalSpecs.map((spec, index) => ({
      goal_id: spec.id,
      runs_24h: 2,
      runs_7d: 2,
      quota_spend_slots_24h: index,
      quota_spend_slots_7d: index,
      automation_run_count_24h: 1,
      automation_run_count_7d: 1,
      progress_signal_run_count_24h: 1,
      progress_signal_run_count_7d: 1,
      project_share_24h: 0.25,
    })),
  },
  todo_index: {
    schema_version: "todo_index_v0",
    source: "attention_queue_and_rollout_event_log",
    total_count: 1,
    current_projected_count: 0,
    rollout_event_count: 2,
    item_limit: 240,
    items: [
      {
        schema_version: "todo_index_item_v0",
        goal_id: "loopx-meta",
        index: 0,
        done: false,
        text: "todo update recorded for todo_f2760d7e328f",
        title: "todo update recorded for todo_f2760d7e328f",
        todo_id: "todo_f2760d7e328f",
        role: "agent",
        status: "open",
        source: "rollout_event_log",
        event_count: 2,
        event_kinds: ["todo_add", "todo_update"],
        latest_event_kind: "todo_update",
        latest_event_at: "2026-06-22T12:28:47Z",
        latest_event_status: "open",
        agent_id: "codex-main-control",
      },
    ],
  },
  decision_freshness_summary: {
    available: true,
    source: "run_history",
    sample_run_count: 4,
    window_days: 7,
    proxy_note: "public-safe dashboard home fixture",
    summary: {
      decision_count: 1,
      stale_count: 1,
      rebase_required_count: 1,
      fresh_count: 0,
    },
    items: [
      {
        goal_id: "loopx-meta",
        decision_kind: "operator_gate",
        decision_at: "2025-12-24T00:00:00+00:00",
        classification: "operator_gate_approved",
        age_days: 8.4,
        stale_by_age: true,
        newer_event_count_7d: 2,
        newer_event_classes_7d: {
          accounting: 0,
          decision: 0,
          evidence: 1,
          state: 1,
          work: 0,
        },
        freshness_state: "stale_rebase_required",
        requires_decision_point_rebase: true,
        reason: "fixture stale operator gate",
      },
    ],
  },
  presentation_surfaces: {
    schema_version: "extension_presentation_surfaces_v0",
    count: 1,
    ready_count: 1,
    review_due_count: 0,
    empty_count: 0,
    invalid_count: 0,
    items: [
      {
        extension_id: researchDetailRef.extension_id,
        extension_revision: researchDetailRef.extension_revision,
        surface_id: "investment-research",
        surface_kind: "decision_research_dashboard",
        title: "Investment Research",
        view_schema: "decision_research_dashboard_v0",
        visibility: "public-safe",
        state: "ready",
        goal_id: "loopx-meta",
        generated_at: "2026-01-15T12:00:00+00:00",
        review_due_at: "2026-02-15T12:00:00+00:00",
        diagnostic: null,
        empty_state_title: "No validated research yet",
        empty_state_detail: "Publish a validated projection.",
        detail_ref: researchDetailRef,
      },
    ],
  },
  local_dashboard_api: {
    source: "browser-smoke",
    status_url: `/${fixtureName}`,
    presentation_detail_url: `/${projectionFixtureName}`,
  },
};

const statusWithoutSurfaces = {
  ...statusFixture,
  presentation_surfaces: {
    schema_version: "extension_presentation_surfaces_v0",
    count: 0,
    ready_count: 0,
    review_due_count: 0,
    empty_count: 0,
    invalid_count: 0,
    items: [],
  },
};

const statusWithDuplicateSurfaceIds = {
  ...statusFixture,
  presentation_surfaces: {
    ...statusFixture.presentation_surfaces,
    count: 2,
    ready_count: 2,
    items: [
      ...statusFixture.presentation_surfaces.items,
      {
        ...statusFixture.presentation_surfaces.items[0],
        extension_id: "second-research-extension",
        title: "Second Provider Research",
        detail_ref: {
          ...researchDetailRef,
          extension_id: "second-research-extension",
          payload_sha256:
            "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        },
      },
    ],
  },
};

function loadPlaywright() {
  const candidates = [
    process.env.LOOPX_PLAYWRIGHT_PACKAGE,
    resolve(homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright"),
  ].filter(Boolean);

  try {
    return require("playwright");
  } catch {
    // Try explicit or bundled local packages below.
  }

  for (const candidate of candidates) {
    if (!candidate || !existsSync(candidate)) {
      continue;
    }
    try {
      return require(candidate);
    } catch {
      // Keep looking.
    }
  }

  throw new Error("Playwright package not found; install playwright or set LOOPX_PLAYWRIGHT_PACKAGE");
}

async function launchBrowser(chromium) {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return chromium.launch({ headless: true });
  }
}

async function waitForDashboard(url) {
  const deadline = Date.now() + 20_000;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return;
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolveTimeout) => setTimeout(resolveTimeout, 250));
  }
  throw lastError ?? new Error(`Timed out waiting for ${url}`);
}

function startDashboardServer() {
  const viteBin = resolve(dashboardDir, "node_modules/vite/bin/vite.js");
  if (!existsSync(viteBin)) {
    throw new Error(`Vite package not installed: ${viteBin}`);
  }
  const nodeBin = [
    process.env.LOOPX_NODE_BIN,
    "/opt/homebrew/bin/node",
    "/usr/local/bin/node",
    process.execPath,
  ].find((candidate) => candidate && existsSync(candidate));
  return spawn(nodeBin, [viteBin, "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
    cwd: dashboardDir,
    env: {
      ...process.env,
      PATH: ["/opt/homebrew/bin", "/usr/local/bin", process.env.PATH].filter(Boolean).join(":"),
    },
    stdio: "ignore",
  });
}

function formatOverflowOffender(offender) {
  const id = offender.testid ? `[data-testid="${offender.testid}"]` : offender.tag;
  return `${id} left=${offender.left} right=${offender.right} width=${offender.width} "${offender.text}"`;
}

async function assertNoHorizontalOverflow(page, label) {
  const report = await page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const root = document.documentElement;
    const body = document.body;
    const scrollWidth = Math.max(root.scrollWidth, body?.scrollWidth ?? 0);
    const offenders = [];
    for (const element of Array.from(document.body.querySelectorAll("*"))) {
      const style = window.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity) === 0) {
        continue;
      }
      const rect = element.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) {
        continue;
      }
      if (rect.left < -2 || rect.right > viewportWidth + 2) {
        offenders.push({
          tag: element.tagName.toLowerCase(),
          testid: element.getAttribute("data-testid"),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          text: (element.textContent ?? "").replace(/\s+/g, " ").trim().slice(0, 90),
        });
      }
      if (offenders.length >= 8) {
        break;
      }
    }
    return {
      viewportWidth,
      scrollWidth,
      overflowPx: Math.max(0, scrollWidth - viewportWidth),
      offenders,
    };
  });
  if (report.overflowPx > 2) {
    const offenders = report.offenders.map(formatOverflowOffender).join(" | ");
    throw new Error(`${label} horizontal overflow: viewport=${report.viewportWidth} scroll=${report.scrollWidth} offenders=${offenders || "none"}`);
  }
}

async function assertNoPanelContentOverflow(page, label) {
  const offenders = await page.evaluate(() => Array.from(document.querySelectorAll([
    '[data-testid="agent-management-row"]',
    '[data-testid="usage-goal-table"]',
    '[data-testid="event-ledger-goal-table"]',
    '[data-testid="decision-freshness-table"]',
  ].join(","))).filter((element) => element.scrollWidth > element.clientWidth + 2).map((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    testid: element.getAttribute("data-testid"),
  })));
  if (offenders.length) {
    throw new Error(`${label} panel content overflow: ${JSON.stringify(offenders)}`);
  }
}

async function assertDrawerTitleFocus(page, label) {
  try {
    await page.waitForFunction(
      () => document.activeElement?.id === "personal-drawer-title",
      undefined,
      { timeout: 2_000 },
    );
  } catch {
    throw new Error(`${label} title did not receive focus.`);
  }
}

async function assertDecisionFrameVisible(page, label) {
  const decisionFrame = page.locator('[data-testid^="share-decision-frame-"]').first();
  await decisionFrame.scrollIntoViewIfNeeded();
  const metrics = await decisionFrame.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      left: Math.round(rect.left),
      right: Math.round(rect.right),
      top: Math.round(rect.top),
      bottom: Math.round(rect.bottom),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      text: (element.textContent ?? "").replace(/\s+/g, " ").trim(),
    };
  });
  if (metrics.left < 0 || metrics.right > metrics.viewportWidth || metrics.top < 0 || metrics.bottom > metrics.viewportHeight) {
    throw new Error(`${label} decision frame is not fully visible: ${JSON.stringify(metrics)}`);
  }
  const requiredFrameText = ["等待方", "推荐动作", "安全边界", "首个用户 Todo", "最高优 Agent Todo"];
  const missing = requiredFrameText.filter((text) => !metrics.text.includes(text));
  if (missing.length) {
    throw new Error(`${label} decision frame missing labels: ${missing.join(", ")}`);
  }
}

async function assertResearchTruthFirstScreen(page, label) {
  const summary = page.locator('[data-testid="decision-research-surface-summary"]');
  await summary.waitFor({ state: "visible" });
  const metrics = await summary.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      left: Math.round(rect.left),
      right: Math.round(rect.right),
      top: Math.round(rect.top),
      viewportWidth: window.innerWidth,
      text: (element.textContent ?? "").replace(/\s+/g, " ").trim(),
    };
  });
  if (
    metrics.left < 0
    || metrics.right > metrics.viewportWidth
    || metrics.top < 0
  ) {
    throw new Error(`${label} research summary is not fully first-screen visible: ${JSON.stringify(metrics)}`);
  }
  // The compact status contract exposes the projection pointer and lifecycle
  // fields, never the provider-owned view body.
  const required = [
    "Investment Research",
    "decision_research_dashboard_v0",
    "View schema",
    "Payload SHA-256",
  ];
  const missing = required.filter((text) => !metrics.text.includes(text));
  if (missing.length) {
    throw new Error(`${label} research summary missing: ${missing.join(", ")}`);
  }
}

async function installChatApiFixture(page, { activeTurn = false } = {}) {
  let createdSessionId = "fixture-manager-session";
  const createdTurnId = "fixture-manager-turn";
  const activeSession = {
    session_id: "fixture-manager-recovery",
    goal_id: "showcase-user-gate-safe-side-path",
    agent_id: "codex",
    adapter_kind: "codex_app_server",
    channel_id: "manager",
    status: "busy",
    active_turn_id: "fixture-active-turn",
    last_error_code: null,
    created_at: "2026-08-10T01:00:00Z",
    updated_at: "2026-08-10T02:00:00Z",
    last_activity_at: "2026-08-10T02:00:00Z",
    resumable: true,
  };
  await page.route("**/api/chat/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/chat/capabilities") {
      await route.fulfill({
        contentType: "application/json",
        json: {
          ok: true,
          schema_version: "loopx_chat_capabilities_v1",
          agent_backend: "multi_adapter",
          sandbox: "read-only",
          approval_policy: "never",
          todo_write: "preview_locked",
          goal_id: null,
          streaming: true,
          resume: true,
          interrupt: true,
          adapters: [{
            agent_id: "codex",
            display_name: "Codex",
            adapter_kind: "codex_app_server",
            available: true,
            streaming: true,
            resume: true,
            interrupt: true,
          }],
        },
        status: 200,
      });
      return;
    }
    if (url.pathname === "/api/chat/sessions" && request.method() === "GET") {
      await route.fulfill({
        contentType: "application/json",
        json: {
          ok: true,
          schema_version: "loopx_chat_session_list_v1",
          sessions: activeTurn ? [activeSession] : [],
        },
        status: 200,
      });
      return;
    }
    if (activeTurn && url.pathname === `/api/chat/sessions/${activeSession.session_id}`) {
      await route.fulfill({
        contentType: "application/json",
        json: {
          ok: true,
          schema_version: "loopx_chat_store_v1",
          session: activeSession,
          messages: [{
            message_id: "fixture-recovery-question",
            turn_id: activeSession.active_turn_id,
            role: "user",
            text: "请继续处理恢复中的回合",
            created_at: "2026-08-10T02:00:00Z",
          }],
          active_turn: {
            turn_id: activeSession.active_turn_id,
            status: "running",
          },
        },
        status: 200,
      });
      return;
    }
    if (url.pathname === "/api/chat/sessions" && request.method() === "POST") {
      const body = request.postDataJSON();
      createdSessionId = activeTurn
        ? activeSession.session_id
        : `fixture-${body.context_kind}-${body.goal_id || "global"}`;
      const session = activeTurn ? activeSession : {
        session_id: createdSessionId,
        goal_id: body.goal_id,
        agent_id: body.agent_id,
        adapter_kind: "codex_app_server",
        channel_id: body.context_kind,
        status: "ready",
        active_turn_id: null,
        last_error_code: null,
        created_at: "2026-08-10T02:00:00Z",
        updated_at: "2026-08-10T02:00:00Z",
        last_activity_at: "2026-08-10T02:00:00Z",
        resumable: true,
      };
      await route.fulfill({
        contentType: "application/json",
        json: {
          agent_id: body.agent_id,
          goal_id: body.goal_id,
          ok: true,
          resumed: activeTurn,
          session_id: createdSessionId,
          session,
        },
        status: 201,
      });
      return;
    }
    if (
      !activeTurn
      && url.pathname === `/api/chat/sessions/${createdSessionId}/turns`
      && request.method() === "POST"
    ) {
      await route.fulfill({
        contentType: "application/json",
        json: {
          ok: true,
          session_id: createdSessionId,
          turn_id: createdTurnId,
          created: true,
          status: "queued",
          events_url: `/api/chat/sessions/${createdSessionId}/turns/${createdTurnId}/events`,
        },
        status: 202,
      });
      return;
    }
    if (
      !activeTurn
      && url.pathname === `/api/chat/sessions/${createdSessionId}/turns/${createdTurnId}/events`
    ) {
      const sseEvent = (id, kind, payload) =>
        `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify({
          event_id: id,
          sequence: Number(id),
          kind,
          created_at: "2026-08-10T02:00:01Z",
          payload,
        })}\n\n`;
      await route.fulfill({
        body: sseEvent("1", "answer.delta", { text: "LoopX 管家建议先处理：请确认 owner 选项 A/B 的取舍。" })
          + sseEvent("2", "turn.completed", {
            response: {
              schema_version: "loopx_chat_agent_response_v0",
              message: "LoopX 管家建议先处理：请确认 owner 选项 A/B 的取舍。",
              proposals: [],
              gate: null,
            },
          }),
        contentType: "text/event-stream",
        status: 200,
      });
      return;
    }
    if (
      activeTurn
      && url.pathname === `/api/chat/sessions/${activeSession.session_id}/turns/${activeSession.active_turn_id}/events`
    ) {
      const sseEvent = (id, kind, payload) =>
        `id: ${id}\nevent: ${kind}\ndata: ${JSON.stringify({
          event_id: id,
          sequence: Number(id),
          kind,
          created_at: "2026-08-10T02:00:01Z",
          payload,
        })}\n\n`;
      await route.fulfill({
        body: sseEvent("1", "answer.delta", { text: "恢复中的回答。" })
          + sseEvent("2", "turn.completed", {
            response: {
              schema_version: "loopx_chat_agent_response_v0",
              message: "恢复中的回答。",
              proposals: [],
              gate: null,
            },
          }),
        contentType: "text/event-stream",
        status: 200,
      });
      return;
    }
    await route.fulfill({
      contentType: "application/json",
      json: { error: "unsupported browser smoke Chat request", ok: false },
      status: 404,
    });
  });
}

async function captureHomeVisualAcceptance(page, url, label) {
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="personal-goal-home"]', { timeout: 10_000 });
  const composer = page.getByLabel("向 LoopX 发送消息");
  const composerPlaceholder = await composer.getAttribute("placeholder");
  if (composerPlaceholder !== "问问 LoopX 管家，或描述一个新 Goal…") {
    throw new Error(`${label} Manager composer placeholder changed: ${composerPlaceholder}`);
  }
  const composerForm = page.locator(".personal-channel-composer");
  const composerBox = await composerForm.boundingBox();
  if (!composerBox || composerBox.height > 84) {
    throw new Error(`${label} composer regressed from the compact single-row layout: ${JSON.stringify(composerBox)}`);
  }
  const composerAgentBox = await page.getByRole("combobox", { name: "选择聊天 Runtime" }).boundingBox();
  if (!composerAgentBox || composerAgentBox.width < 40) {
    throw new Error(`${label} composer Agent selector is visually truncated: ${JSON.stringify(composerAgentBox)}`);
  }
  if (await composerForm.locator(".personal-composer-tools > span").count()) {
    throw new Error(`${label} composer rendered a duplicate Goal context chip.`);
  }
  const sendButton = composerForm.getByRole("button", { name: "发送" });
  const sendButtonBackground = await sendButton.evaluate((element) =>
    getComputedStyle(element).backgroundColor
  );
  if (sendButtonBackground === "rgba(0, 0, 0, 0)" || sendButtonBackground === "transparent") {
    throw new Error(`${label} composer send button lost its visible background.`);
  }
  await assertNoHorizontalOverflow(page, `${label} top`);
  await page.screenshot({
    path: resolve(visualOutputDir, `${label}-home-top.png`),
    fullPage: false,
    animations: "disabled",
  });

  const isMobile = label === "mobile";
  if (!isMobile) {
    await page.locator(".personal-composer-tools > summary").click();
    await page.getByRole("button", { name: "询问全局待办", exact: true }).click();
    if (await composer.inputValue()) {
      throw new Error("Manager advice shortcut should send immediately without leaving a draft.");
    }
    const projectedAnswer = page.locator(".personal-manager-conversation-tray .is-assistant").last();
    await projectedAnswer.waitFor({ state: "visible", timeout: 10_000 });
    await projectedAnswer.getByText("请确认 owner 选项 A/B 的取舍。", { exact: false })
      .waitFor({ state: "visible", timeout: 10_000 });
    const projectedAnswerText = await projectedAnswer.innerText();
    for (const text of ["LoopX 管家", "先处理", "请确认 owner 选项 A/B 的取舍"]) {
      if (!projectedAnswerText.includes(text)) {
        throw new Error(`Manager projection answer missing truthful attribution: ${text}; answer=${projectedAnswerText}`);
      }
    }
    if (projectedAnswerText.includes("showcase-user-gate-safe-side-path")) {
      throw new Error(`Manager projection answer exposed a raw Goal id: ${projectedAnswerText}`);
    }

    const agentSelect = page.getByRole("combobox", { name: "选择聊天 Runtime" });
    await agentSelect.click();
    const agentListbox = page.getByRole("listbox", { name: "选择聊天 Runtime" });
    const agentOptions = await agentListbox.getByRole("option").allTextContents();
    for (const text of ["Codex", "仅查状态"]) {
      if (!agentOptions.some((option) => option.includes(text))) {
        throw new Error(`${label} Agent selector missing: ${text}`);
      }
    }
    await agentListbox.getByRole("option", { name: "仅查状态", exact: true }).click();
    await composer.fill("当前健康状态");
    await sendButton.click();
    const statusOnlyAnswer = page.locator(".personal-manager-conversation-tray .is-assistant").last();
    const statusOnlyAnswerText = await statusOnlyAnswer.innerText();
    for (const text of ["仅查状态", "当前需要关注这些健康问题"]) {
      if (!statusOnlyAnswerText.includes(text)) {
        throw new Error(`Status-only answer missing attribution: ${text}`);
      }
    }
    await page.reload({ waitUntil: "networkidle" });
    const persistedAgent = await page.getByRole("combobox", { name: "选择聊天 Runtime" }).getAttribute("data-value");
    if (persistedAgent !== "status-only") {
      throw new Error(`Manager Agent selection did not persist per Chat: ${persistedAgent}`);
    }
    await page.getByRole("combobox", { name: "选择聊天 Runtime" }).click();
    await page.getByRole("listbox", { name: "选择聊天 Runtime" }).getByRole("option", { name: "Codex", exact: true }).click();
  }

  const goalList = page.locator(".personal-goal-list");
  if (isMobile) {
    if (await goalList.isVisible()) {
      throw new Error("Mobile home should show Manager Chat without stacking the Goal list.");
    }
    await page.getByRole("button", { name: "打开 Goal 导航" }).click();
    await goalList.waitFor({ state: "visible", timeout: 10_000 });
    await page.screenshot({
      path: resolve(visualOutputDir, `${label}-goals.png`),
      fullPage: false,
      animations: "disabled",
    });
  }

  const goalRow = goalList.locator(".personal-goal-link").first();
  const selectedGoalTitle = (await goalRow.innerText()).split("\n")[0]?.trim();
  await goalRow.click();
  const goalTabs = page.getByRole("navigation", { name: "Goal 视图" });
  await goalTabs.waitFor({ state: "visible", timeout: 10_000 });
  const goalHeaderText = await page.locator(".personal-channel-header").innerText();
  for (const text of [selectedGoalTitle, "Codex", "概览", "对话", "任务", "成果"]) {
    if (text && !goalHeaderText.includes(text)) {
      throw new Error(`${label} Goal header missing: ${text}`);
    }
  }
  const goalComposerPlaceholder = await page.getByLabel("向 LoopX 发送消息").getAttribute("placeholder");
  if (!goalComposerPlaceholder?.includes(selectedGoalTitle)) {
    throw new Error(`${label} Goal composer lost its Goal context: ${goalComposerPlaceholder}`);
  }

  await goalTabs.getByRole("button", { name: "任务", exact: true }).click();
  const taskBoard = page.locator(".personal-task-kanban");
  await taskBoard.waitFor({ state: "visible", timeout: 10_000 });
  const taskBoardText = await taskBoard.innerText();
  for (const text of ["待你确认", "待执行 / 进行中", "定时与持续", "已完成", "请确认 owner 选项 A/B 的取舍", "推进与 owner 决策独立的 safe side path"]) {
    if (!taskBoardText.includes(text)) {
      throw new Error(`${label} Tasks projection missing: ${text}`);
    }
  }
  const taskBoardBox = await taskBoard.boundingBox();
  const currentComposerBox = await page.locator(".personal-channel-composer").boundingBox();
  if (!taskBoardBox || !currentComposerBox || taskBoardBox.y >= currentComposerBox.y) {
    throw new Error(`${label} Tasks view is obscured by the composer: tasks=${JSON.stringify(taskBoardBox)} composer=${JSON.stringify(currentComposerBox)}`);
  }
  await taskBoard.getByText("请确认 owner 选项 A/B 的取舍；未确认前不推进 gated route。", { exact: true }).click();
  const attentionDrawer = page.getByRole("dialog");
  await attentionDrawer.waitFor({ state: "visible", timeout: 10_000 });
  const attentionText = await attentionDrawer.innerText();
  for (const text of ["正在阻塞 Agent", selectedGoalTitle, "原因", "证据", "查看影响并决定"]) {
    if (text && !attentionText.includes(text)) {
      throw new Error(`${label} Needs You detail missing: ${text}`);
    }
  }
  const closeAttention = page.getByRole("button", { name: new RegExp(`关闭详情：返回${selectedGoalTitle}`) });
  await assertDrawerTitleFocus(page, `${label} Needs You detail`);
  await closeAttention.click();

  await taskBoard.getByText("推进与 owner 决策独立的 safe side path，输出公开可复现证据。", { exact: true }).click();
  const todoDrawer = page.getByRole("dialog");
  await todoDrawer.waitFor({ state: "visible", timeout: 10_000 });
  const todoText = await todoDrawer.innerText();
  for (const text of [selectedGoalTitle, "Owner", "状态", "依赖", "下一转换", "管理任务", "标记完成"]) {
    if (text && !todoText.includes(text)) {
      throw new Error(`${label} Todo lineage detail missing: ${text}; detail=${todoText}`);
    }
  }
  await page.getByRole("button", { name: new RegExp(`关闭详情：返回${selectedGoalTitle}`) }).click();

  await goalTabs.getByRole("button", { name: "成果", exact: true }).click();
  await page.getByText("Files & Outputs", { exact: true }).waitFor({ state: "visible", timeout: 10_000 });
  const filesList = page.getByTestId("personal-goal-outputs");
  const filesText = await filesList.innerText();
  if (!filesText.includes("最近验证") || !(await page.locator(".personal-channel-header").innerText()).includes(selectedGoalTitle)) {
    throw new Error(`${label} Files projection lost its public-safe summary or Goal lineage: ${filesText}`);
  }
  await filesList.getByRole("button").first().click();
  const outputDrawer = page.getByRole("dialog");
  await outputDrawer.waitFor({ state: "visible", timeout: 10_000 });
  const outputText = await outputDrawer.innerText();
  for (const text of ["产出详情", selectedGoalTitle, "Run", "Agent"]) {
    if (text && !outputText.includes(text)) {
      throw new Error(`${label} run evidence detail missing: ${text}`);
    }
  }
  if (!(await outputDrawer.getByLabel("公开安全产出预览").count()) && !outputText.includes("此产出没有可用的公开安全内联预览")) {
    throw new Error(`${label} output detail omitted both its public-safe preview and an explicit unavailable state.`);
  }
  await page.getByRole("button", { name: new RegExp(`关闭详情：返回${selectedGoalTitle}`) }).click();

  await goalTabs.getByRole("button", { name: "对话", exact: true }).click();
  const chatPaneText = await page.locator(".personal-channel-scroll").innerText();
  if (!chatPaneText.includes("Codex")) {
    throw new Error(`${label} Goal Chat does not expose the owning Agent.`);
  }
  const goalSummary = page.locator(".personal-channel-header");
  await goalSummary.waitFor({ state: "visible", timeout: 10_000 });
  const goalSummaryText = await goalSummary.innerText();
  const missingGoalSummary = [selectedGoalTitle, "Codex", "概览", "对话", "任务", "成果"]
    .filter((text) => !goalSummaryText.includes(text));
  if (missingGoalSummary.length) {
    throw new Error(`${label} Goal Chat projection missing labels: ${missingGoalSummary.join(", ")}`);
  }
  await goalTabs.getByRole("button", { name: "任务", exact: true }).click();
  const planRows = page.locator(".personal-task-card");
  if (await planRows.count() === 0) {
    throw new Error(`${label} Goal Chat plan did not preserve Todo lineage.`);
  }
  await goalTabs.getByRole("button", { name: "对话", exact: true }).click();
  const runEvidence = page.locator(".personal-output-row").first();
  await runEvidence.waitFor({ state: "visible", timeout: 10_000 });
  if (!await runEvidence.locator("time").getAttribute("datetime") && !(await runEvidence.locator("time").innerText()).trim()) {
    throw new Error(`${label} Goal Chat run evidence did not preserve run lineage.`);
  }
  const needsYou = page.locator(".personal-attention-row").first();
  const decisionText = await needsYou.innerText();
  for (const text of ["请确认 owner 选项 A/B 的取舍", "需要你"]) {
    if (!decisionText.includes(text)) {
      throw new Error(`${label} Goal Chat decision card missing: ${text}`);
    }
  }
  if (!isMobile && !/(受阻|阻塞|待处理)/u.test(decisionText)) {
    throw new Error(`${label} Goal Chat decision card omitted its actionable status: ${decisionText}`);
  }
  const decisionBox = await needsYou.boundingBox();
  const goalComposerBox = await page.locator(".personal-channel-composer").boundingBox();
  if (!decisionBox || !goalComposerBox || decisionBox.y + decisionBox.height > goalComposerBox.y + 2) {
    throw new Error(`${label} needs-you card is obscured by the composer: decision=${JSON.stringify(decisionBox)} composer=${JSON.stringify(goalComposerBox)}`);
  }
  const executionText = await page.locator(".personal-channel-header").innerText();
  if (!executionText.includes("Codex")) {
    throw new Error(`${label} Goal Chat does not expose the owning Agent.`);
  }

  const agentTrigger = page.getByRole("combobox", { name: "选择聊天 Runtime" });
  await agentTrigger.click();
  const agentMenu = page.getByRole("listbox", { name: "选择聊天 Runtime" });
  const agentMenuText = (await agentMenu.getByRole("option").allTextContents()).join("\n");
  for (const text of ["Codex", "仅查状态"]) {
    if (!agentMenuText.includes(text)) {
      throw new Error(`${label} Agent selector missing: ${text}`);
    }
  }
  if (/token|secret|key|密钥/iu.test(agentMenuText)) {
    throw new Error(`${label} Agent selector leaked credential-oriented text: ${agentMenuText}`);
  }
  const unavailableOptions = agentMenu.getByRole("option").filter({ hasText: "不可用" });
  for (let index = 0; index < await unavailableOptions.count(); index += 1) {
    if (!await unavailableOptions.nth(index).isDisabled()) {
      throw new Error(`${label} unavailable Agent option remained selectable.`);
    }
  }
  await page.keyboard.press("Escape");
  if (isMobile) {
    const menuBox = await agentTrigger.boundingBox();
    if (!menuBox || menuBox.x < 0 || menuBox.x + menuBox.width > await page.evaluate(() => window.innerWidth)) {
      throw new Error(`Mobile Agent selector is outside the viewport: ${JSON.stringify(menuBox)}`);
    }
  }

  await page.locator(".personal-run-row").first().click();
  const runningDetails = page.getByRole("dialog");
  await runningDetails.waitFor({ state: "visible", timeout: 10_000 });
  const closeDetails = page.getByRole("button", { name: new RegExp(`关闭详情：返回${selectedGoalTitle}`) });
  await assertDrawerTitleFocus(page, `${label} running details`);
  const runningDetailsText = await runningDetails.innerText();
  for (const text of ["执行 Session", "执行过程与结果", "详情与操作", "Goal", "进度", "运行记录", "本次运行产出"]) {
    if (!runningDetailsText.includes(text)) {
      throw new Error(`${label} running details missing: ${text}`);
    }
  }
  await runningDetails.getByRole("tab", { name: "详情与操作" }).click();
  const executionDetailsText = await runningDetails.innerText();
  for (const text of ["会话状态", "可恢复", "与 Codex 纠偏", "更多运行操作"]) {
    if (!executionDetailsText.includes(text)) {
      throw new Error(`${label} Session operations missing: ${text}`);
    }
  }
  await closeDetails.click();

  await assertNoHorizontalOverflow(page, `${label} Goal Chat`);
  await page.screenshot({
    path: resolve(visualOutputDir, `${label}-goal-chat.png`),
    fullPage: false,
    animations: "disabled",
  });

  if (!isMobile) {
    await goalList.locator(".personal-goal-link").nth(1).click();
    await goalSummary.waitFor({ state: "visible", timeout: 10_000 });
    if (await page.locator(".personal-attention-row").count()) {
      throw new Error("Healthy Goal Chat rendered an empty user-action card.");
    }
    const healthyChatText = await page.locator(".personal-channel-scroll").innerText();
    for (const text of ["完整 Todo 投影", "Agent 运行记录", "证据与状态源", "quota_slot_spent"]) {
      if (healthyChatText.includes(text)) {
        throw new Error(`Healthy Goal Chat leaked advanced details: ${text}`);
      }
    }

    await goalList.locator(".personal-goal-link").filter({ hasText: "LoopX meta" }).click();
    await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "概览", exact: true }).click();
    await page.getByRole("button", { name: "Goal 信息", exact: true }).click();
    await runningDetails.waitFor({ state: "visible", timeout: 10_000 });
    const repairDetailsText = await runningDetails.innerText();
    for (const text of ["Goal 详情", "需修复", "刷新 LoopX 状态，确认当前进度仍然有效", "执行 Session"]) {
      if (!repairDetailsText.includes(text)) {
        throw new Error(`Repair running details missing: ${text}`);
      }
    }
    if (repairDetailsText.includes("refresh-state") || repairDetailsText.includes("latest_run")) {
      throw new Error(`Repair Goal leaked machine text: ${repairDetailsText}`);
    }
    await page.getByRole("button", { name: /关闭详情：返回LoopX meta/ }).click();
  }
}

async function main() {
  const { chromium } = loadPlaywright();
  await writeFile(fixturePath, JSON.stringify(statusFixture, null, 2) + "\n", "utf-8");
  await writeFile(
    emptyFixturePath,
    JSON.stringify(statusWithoutSurfaces, null, 2) + "\n",
    "utf-8",
  );
  await writeFile(
    duplicateSurfaceFixturePath,
    JSON.stringify(statusWithDuplicateSurfaceIds, null, 2) + "\n",
    "utf-8",
  );
  await writeFile(
    projectionFixturePath,
    JSON.stringify(projectionResponse, null, 2) + "\n",
    "utf-8",
  );
  await mkdir(visualOutputDir, { recursive: true });

  const server = startDashboardServer();
  let browser;
  try {
    const baseUrl = `http://127.0.0.1:${port}`;
    await waitForDashboard(baseUrl);
    browser = await launchBrowser(chromium);
    const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
    await installChatApiFixture(page);
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto(`${baseUrl}/?statusUrl=/${fixtureName}`, { waitUntil: "networkidle" });
    try {
      await page.waitForSelector('[data-testid="personal-goal-home"]', { timeout: 10_000 });
    } catch (error) {
      const diagnostic = await page.locator("body").innerText();
      throw new Error(`Failed waiting for personal-goal-home: ${error.message}; body=${diagnostic.slice(0, 1000)}; pageErrors=${pageErrors.join(" | ")}`);
    }

    await page.locator(".personal-composer-tools > summary").click();
    const body = await page.locator("body").innerText();
    const required = [
      "GOALS",
      "LoopX 管家",
      "需要你",
      "Showcase user gate safe side path",
      "Showcase creator operator",
      "Showcase peer agent self iteration",
      "LoopX meta",
      "询问全局待办",
      "汇总所有 Goal 进展",
      "创建新 Goal",
      "Codex",
    ];
    const missing = required.filter((text) => !body.includes(text));
    if (missing.length) {
      throw new Error(`Missing dashboard home text: ${missing.join(", ")}`);
    }

    const forbidden = [
      "[plugin:vite:oxc]",
      "Transform failed",
      "active internal-slot <= 2",
      "single_surface",
      "quota_slot_spent",
      "focus_wait",
      "状态服务契约过旧",
      "fixture stop condition",
      "latest_run",
      "refresh-state",
    ];
    const present = forbidden.filter((text) => body.includes(text));
    if (present.length) {
      throw new Error(`Dashboard home leaked raw machine/debug text: ${present.join(", ")}`);
    }

    const url = new URL(page.url());
    if (url.searchParams.get("view")) {
      throw new Error(`Canonical home should not keep a view search param: ${page.url()}`);
    }

    await captureHomeVisualAcceptance(page, `${baseUrl}/?statusUrl=/${fixtureName}`, "desktop");
    const mobilePage = await browser.newPage({
      isMobile: true,
      viewport: { width: 390, height: 900 },
    });
    await installChatApiFixture(mobilePage);
    mobilePage.on("pageerror", (error) => pageErrors.push(`mobile: ${error.message}`));
    try {
      await captureHomeVisualAcceptance(mobilePage, `${baseUrl}/?statusUrl=/${fixtureName}`, "mobile");
    } finally {
      await mobilePage.close();
    }

    const recoveryPage = await browser.newPage({ viewport: { width: 1200, height: 900 } });
    await installChatApiFixture(recoveryPage, { activeTurn: true });
    try {
      await recoveryPage.goto(`${baseUrl}/?statusUrl=/${fixtureName}`, { waitUntil: "networkidle" });
      const recoveredAnswer = recoveryPage.locator(".personal-manager-conversation-tray .is-assistant").last();
      await recoveredAnswer.waitFor({ state: "visible", timeout: 10_000 });
      const recoveredText = await recoveredAnswer.innerText();
      for (const text of ["恢复中的回答。", "LoopX 管家"]) {
        if (!recoveredText.includes(text)) {
          throw new Error(`Active Turn recovery did not render ${text}: ${recoveredText}`);
        }
      }
      if (recoveredText.includes("Agent 正在处理")) {
        throw new Error(`Recovered active Turn remained pending after terminal SSE: ${recoveredText}`);
      }
    } finally {
      await recoveryPage.close();
    }

    // Explicitly select Goal to validate Goal workspace and Goal prompts
    await page.goto(`${baseUrl}/?goalId=showcase-user-gate-safe-side-path&statusUrl=/${fixtureName}`, { waitUntil: "networkidle" });
    await page.waitForSelector('[data-testid="personal-goal-home"]', { timeout: 10_000 });
    await page.locator(".personal-composer-tools > summary").click();
    const goalBody = await page.locator("body").innerText();
    const requiredGoalText = [
      "Showcase user gate safe side path",
      "询问下一步",
      "向 Agent 获取进度报告",
      "配置定时检查",
    ];
    const missingGoalText = requiredGoalText.filter((text) => !goalBody.includes(text));
    if (missingGoalText.length) {
      throw new Error(`Missing Goal workspace text: ${missingGoalText.join(", ")}`);
    }

    // Switch to Tasks tab to validate Kanban tasks columns
    await page.getByRole("navigation", { name: "Goal 视图" }).getByRole("button", { name: "任务", exact: true }).click();
    await page.getByRole("button", {name: "看板", exact: true}).click();
    await page.locator(".personal-task-kanban").waitFor({ state: "visible", timeout: 10_000 });
    const tasksBody = await page.locator("body").innerText();
    const requiredTasksText = [
      "待你确认",
      "待执行 / 进行中",
      "定时与持续",
      "已完成",
    ];
    const missingTasksText = requiredTasksText.filter((text) => !tasksBody.includes(text));
    if (missingTasksText.length) {
      throw new Error(`Missing Goal Tasks text: ${missingTasksText.join(", ")}`);
    }

    // Test Context Drawer on Goal
    await page.goto(`${baseUrl}/?goalId=loopx-meta&statusUrl=/${fixtureName}`, { waitUntil: "networkidle" });
    await page.waitForSelector('[data-testid="personal-goal-home"]', { timeout: 10_000 });
    const drawerToggle = page.locator('button[aria-label="打开上下文抽屉"], button[aria-label="关闭上下文抽屉"]').first();
    if (await drawerToggle.count()) {
      await drawerToggle.click();
      await page.waitForTimeout(300);
      const drawerText = await page.locator(".personal-context-drawer").innerText();
      if (!drawerText.includes("Goal 详情") && !drawerText.includes("Repository")) {
        throw new Error(`Context drawer did not render expected details: ${drawerText}`);
      }
    }

    // Missing status URL error state & fallback
    const missingStatusRoutes = [
      `?statusUrl=/status.missing.browser-smoke.json`,
    ];
    for (const route of missingStatusRoutes) {
      await page.goto(`${baseUrl}/${route}`, { waitUntil: "networkidle" });
      const initialStatusState = page.locator('[data-testid="initial-status-state"]');
      await initialStatusState.waitFor({ state: "visible", timeout: 10_000 });
      const initialStatusText = await initialStatusState.innerText();
      const missingInitialStatusText = ["无法加载实时状态", "重试"]
        .filter((text) => !initialStatusText.includes(text));
      if (missingInitialStatusText.length) {
        throw new Error(`Missing explicit initial status error state for ${route}: ${initialStatusText}`);
      }
      const syntheticDashboardCount = await page
        .locator('[data-testid="personal-goal-home"]')
        .count();
      if (syntheticDashboardCount !== 0) {
        throw new Error(`Requested live status fell back to synthetic content for ${route}.`);
      }
      await initialStatusState.getByRole("button", { name: "重试", exact: true }).click();
      const restoredStatusState = page.locator('[data-testid="initial-status-state"]');
      await restoredStatusState.waitFor({ state: "visible", timeout: 10_000 });
      await restoredStatusState.getByText("无法加载实时状态", { exact: true }).waitFor({
        state: "visible",
        timeout: 10_000,
      });
    }

    if (pageErrors.length) {
      throw new Error(`Dashboard page errors: ${pageErrors.join(" | ")}`);
    }

    console.log("dashboard-home-browser-smoke ok");
  } finally {
    if (browser) {
      await browser.close();
    }
    server.kill("SIGTERM");
    await rm(fixturePath, { force: true });
    await rm(emptyFixturePath, { force: true });
    await rm(duplicateSurfaceFixturePath, { force: true });
    await rm(projectionFixturePath, { force: true });
  }
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exit(1);
});
