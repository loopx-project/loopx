// @ts-expect-error The smoke compiler intentionally runs without @types/node.
import { readFileSync } from "node:fs";

function assert(condition: boolean, message: string) {
  if (!condition) throw new Error(message);
}

function includes(source: string, snippet: string, label: string) {
  assert(source.includes(snippet), `missing ${label}: ${snippet}`);
}

function excludes(source: string, snippet: string, label: string) {
  assert(!source.includes(snippet), `unexpected ${label}: ${snippet}`);
}

const routerSource = readFileSync("src/router.tsx", "utf8");
const dashboardSource = readFileSync("src/views/dashboard-page.tsx", "utf8");
const workspacePageSource = readFileSync("src/features/personal-workspace/personal-workspace-page.tsx", "utf8");
const actionFormSource = readFileSync("src/features/personal-workspace/workspace-action-form.tsx", "utf8");
const goalCreateRequestSource = readFileSync("src/features/personal-workspace/goal-create-request.ts", "utf8");
const sidebarSource = readFileSync("src/features/personal-workspace/goal-sidebar.tsx", "utf8");
const shellSource = readFileSync("src/features/personal-workspace/workspace-shell.tsx", "utf8");
const drawerSource = readFileSync("src/features/personal-workspace/context-drawer.tsx", "utf8");
const timelineSource = readFileSync("src/features/personal-workspace/channel-timeline.tsx", "utf8");
const modelSource = readFileSync("src/features/personal-workspace/personal-workspace-model.ts", "utf8");
const chatDataSource = readFileSync("src/data/chat.ts", "utf8");
const stylesSource = readFileSync("src/features/personal-workspace/personal-workspace.css", "utf8");
const packageSource = readFileSync("package.json", "utf8");
const viteSource = readFileSync("vite.config.ts", "utf8");
const dashboardDevSource = readFileSync("../../../scripts/dashboard-dev.sh", "utf8");
const designSource = readFileSync("design.md", "utf8");

excludes(routerSource, 'view: z.enum(["ops", "share"])', "legacy dual-view routing removed");
excludes(routerSource, 'chatRoute', "legacy standalone chat route removed");
includes(dashboardSource, 'data-testid="personal-goal-home"', "personal workspace route");
includes(dashboardSource, "<PersonalWorkspacePage", "personal workspace rendering");
includes(dashboardSource, "buildPersonalHomeModel(payload, rows,", "public-safe workspace projection");
includes(dashboardSource, "fetchChatSessions({", "persisted session discovery");
includes(dashboardSource, "interruptChatTurn", "interruptible Agent turn");
includes(dashboardSource, "sendChatTurnStreaming", "streaming Agent turn");

includes(shellSource, "data-workspace-sidebar", "single workspace sidebar");
includes(shellSource, "data-context-drawer", "polymorphic context drawer");
includes(sidebarSource, 't("sidebar.manager")', "manager channel");
includes(sidebarSource, "Goals", "Goal directory");
excludes(sidebarSource, "<span>需要你</span>", "legacy attention sub-navigation");
excludes(sidebarSource, "<span>运行中</span>", "legacy running sub-navigation");
excludes(sidebarSource, "<span>最近产出</span>", "legacy output sub-navigation");
includes(sidebarSource, 'aria-label={t("sidebar.createGoal")}', "Goal creation entry");
excludes(dashboardSource, 'className="personal-global-rail"', "unexplained icon rail");
excludes(dashboardSource, '>查看</button>', "repeated row View button");
excludes(dashboardSource, '>纠偏</button>', "repeated row correction button");

includes(timelineSource, 'aria-live="polite"', "streaming timeline live region");
includes(workspacePageSource, 'kind: "attention"', "attention row projection");
includes(workspacePageSource, 'kind: "run"', "run row projection");
includes(workspacePageSource, 'kind: "output"', "output row projection");
includes(workspacePageSource, 'kind: "schedule"', "schedule row projection");
includes(workspacePageSource, 'kind: "proposal"', "typed proposal projection");
includes(workspacePageSource, "callbacks.onSendMessage?.(message, selectedAgentId, selectedGoalId", "selected conversation receives free text");
includes(actionFormSource, 'from "./goal-create-request"', "Goal form imports the shared Goal create request builder");
includes(actionFormSource, "onPreview(goalCreateRequest(", "Goal form previews through goalCreateRequest");
includes(goalCreateRequestSource, "export function goalCreateRequest", "shared Goal create request builder");
includes(goalCreateRequestSource, 'actionKind: "goal.create"', "explicit Goal create action");
includes(actionFormSource, '"heartbeat.bind" : "monitor.create"', "explicit heartbeat and monitor form actions");
includes(workspacePageSource, 'error.payload.error_code === "protected_action"', "protected host Gate rendering");
includes(workspacePageSource, 'todo.taskClass === "continuous_monitor"', "canonical continuous monitor projection");

includes(drawerSource, 'data-context-kind={selection.kind}', "typed drawer mode");
includes(drawerSource, 'role="dialog"', "accessible drawer dialog");
includes(drawerSource, 'event.key === "Escape"', "drawer Escape handling");
includes(drawerSource, "callbacks.onCorrectRun", "same-session correction action");
includes(drawerSource, "callbacks.onInterruptRun", "turn interruption action");
includes(drawerSource, 't("drawer.setupHeartbeat")', "Goal heartbeat entry");
includes(drawerSource, 't("drawer.scheduleAdd")', "Goal monitor entry");
includes(drawerSource, 't("drawer.advancedDiagnostics")', "collapsed diagnostics");
includes(drawerSource, "session_id:", "diagnostic Session id");
includes(drawerSource, "turn_id:", "diagnostic Turn id");

includes(chatDataSource, "export async function previewTypedAction", "generic action preview client");
includes(chatDataSource, "export async function loadTypedAction", "generic action load client");
includes(chatDataSource, "export async function applyTypedAction", "generic action apply client");
includes(chatDataSource, "export async function cancelTypedAction", "generic action cancel client");
includes(modelSource, '"goal.create"', "Goal action contract");
includes(modelSource, '"agent.bind"', "Agent binding action contract");
includes(modelSource, '"run.correct"', "run correction action contract");

includes(stylesSource, "@media (max-width: 720px)", "mobile workspace layout");
includes(stylesSource, "prefers-reduced-motion", "reduced-motion behavior");
includes(viteSource, '"/status.json"', "status proxy");
includes(viteSource, '"/ssh-hosts"', "local SSH Host discovery proxy");
includes(viteSource, '"/api/actions"', "typed action proxy");
includes(packageSource, '"dev": "bash ../../../scripts/dashboard-dev.sh"', "one-command dashboard launcher");
includes(dashboardDevSource, "serve-status", "status service launcher");
includes(dashboardDevSource, "loopx.cli chat", "Chat service launcher");
includes(dashboardDevSource, "wait_for_service", "launcher readiness gate");

includes(designSource, "## Business Object Mapping", "business object mapping");
includes(designSource, "## Control-Plane API Requirements", "typed control-plane contract");
includes(designSource, "## Acceptance Criteria", "design acceptance criteria");

console.log("home-route smoke ok");
