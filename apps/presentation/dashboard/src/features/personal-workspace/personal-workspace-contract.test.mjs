import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = (name) => readFileSync(new URL(name, import.meta.url), "utf8");
const model = source("./personal-workspace-model.ts");
const drawer = source("./context-drawer.tsx");
const header = source("./channel-header.tsx");
const sidebar = source("./goal-sidebar.tsx");
const page = source("./personal-workspace-page.tsx");
const router = source("./personal-workspace-router.ts");
const shell = source("./workspace-shell.tsx");
const timeline = source("./channel-timeline.tsx");
const returnDelivery = source("./return-delivery-status.tsx");
const runRow = source("./cards/run-row.tsx");
const larkSettings = source("./lark-settings-page.tsx");
const machineSettings = source("./machine-configuration-settings.tsx");
const goalCapabilitySettings = source("./goal-capability-settings.tsx");
const notificationSettings = source("./notification-settings-panel.tsx");
const capabilityFields = source("./capability-configuration-fields.tsx");
const capabilityLocalization = source("./capability-localization.ts");
const capabilityWorkbench = source("./capability-workbench.tsx");
const i18n = source("./i18n.tsx");
const workspaceTheme = source("./workspace-theme.ts");
const statusSourceSwitcher = source("./status-source-switcher.tsx");
const workspaceSettings = source("./workspace-settings-page.tsx");
const styles = source("./personal-workspace.css");
const dashboard = source("../../views/dashboard-page.tsx");
const tasks = source("./goal-tasks-view.tsx");
const status = source("../../data/status.ts");
const chatData = source("../../data/chat.ts");
const actionReview = source("../../../../../../loopx/control_plane/presentation/action_review_plan.ts");

assert.match(model, /kind: "todo"/, "Todo has its own drawer selection");
for (const field of ["dependencies", "nextTransition", "ownerLabel", "todoId", "taskClass"]) {
  assert.match(model, new RegExp(`${field}\\??:`), `Todo exposes ${field}`);
}
assert.match(drawer, /actionKind: "todo\.update"/, "Todo mutations use typed previews");
for (const operation of ["reassign", "block"]) {
  assert.match(drawer, new RegExp(`operation:\\s*"${operation}"`), `Todo supports ${operation}`);
}
assert.match(drawer, /previewTodoTransition\(selection\.item, "defer"/, "Todo defer collects a resume condition before preview");
assert.match(drawer, /resume_when:\s*resumeWhen/, "Todo defer sends its explicit resume condition");
assert.doesNotMatch(drawer + page, /owner_resume/, "Personal Workspace never emits the unsupported owner_resume sentinel");
assert.match(drawer, /previewTodoTransition\(selection\.item, "complete"/, "Todo complete is the primary drawer action");
assert.match(drawer, /actionKind: "todo\.create"/, "Todo successor uses the canonical create action");

for (const field of ["evidence", "explanation"]) {
  assert.match(model, new RegExp(`${field}\\??:`), `Decision exposes ${field}`);
}
for (const decision of ["reject", "defer"]) {
  assert.match(drawer, new RegExp(`resolution:\\s*"${decision}"`), `Decision previews ${decision}`);
}
assert.match(drawer, /previewDecision\(selection\.item, "approve"/, "Decision approval uses a typed preview");

for (const callback of ["onRetryResumeRun", "onStartNewRunSession", "onCloseRunSession"]) {
  assert.match(model, new RegExp(`${callback}\\??:`), `Run exposes ${callback}`);
  assert.match(drawer, new RegExp(`callbacks\\.${callback}`), `Run menu calls ${callback}`);
}
assert.match(drawer, /resume_failed/, "Run recovery presents resume failure explicitly");
assert.match(drawer, /personal-run-more/, "Run secondary actions live in a compact menu");
assert.match(page, /item\.run\.runId === selection\.item\.runId/, "Run drawer refresh keeps the selected run identity");
assert.match(model, /todoId\??:/, "An execution Run keeps its Task identity");
assert.match(tasks, /t\("tasks\.viewExecution"\)/, "A running Task exposes a localized execution entry");
assert.match(runRow, /t\("tasks\.viewExecution"\)/, "Run rows expose one localized entry for both progress and returned results");
assert.match(drawer, /drawer\.runDetails[\s\S]*drawer\.runLatest[\s\S]*drawer\.noRun/, "Every Goal detail exposes a stable localized execution entry or explicit empty state");
assert.doesNotMatch(drawer, /agentLabel\} · \{selection\.item\.status\}/, "Run details do not expose raw status codes in the heading");
assert.match(page, /activeSessionRun/, "Opening a Session preserves the selected run in Goal state");
assert.match(page, /personal-session-record/, "Goal chat visibly identifies the loaded Session record");
assert.match(header, /personal-goal-tabs/, "Goal Chat, Tasks, and Files stay one click away in the header");
assert.match(chatData, /output_token_budget:[\s\S]*scope: z\.literal\("per_model_request"\)/, "Manager binding keeps the typed per-request output budget");
assert.match(header, /managerOutputTokenBudgetLabel/, "Manager header renders the shared output-budget projection");
assert.equal((i18n.match(/"header\.managerOutputTokenBudget"/g) ?? []).length, 2, "Output budget has English and Simplified Chinese product copy");
for (const view of ["Chat", "Tasks", "Files"]) {
  assert.match(header, new RegExp(`\"${view.toLowerCase()}\"|>${view}<`), `Goal header exposes ${view}`);
}
assert.match(
  page,
  /function selectGoal\(goalId: string \| null\)[\s\S]*?setSelectedGoalTab\("tasks"\)/,
  "Selecting a Goal opens its Tasks view first",
);
assert.match(model, /onOpenGoalView\??:/, "Goal detail can switch the center workspace view");
for (const label of ["执行中", "已安排", "等待条件", "可继续"]) {
  assert.match(model + drawer + page, new RegExp(label), `Session and Run status language includes ${label}`);
}
assert.match(dashboard, /actionKind:\s*"run\.correct"/, "Run correction uses the scoped typed action");
assert.doesNotMatch(
  dashboard,
  /onCorrectRun:[\s\S]{0,240}sendManagerQuestion/,
  "Run correction does not fall back to the read-only manager Chat",
);
assert.match(page, /routeWorkspaceInput\(message,/, "Every free-text send enters the unified Router contract");
assert.match(router, /route: "projection" \| "typed_action" \| "agent_chat" \| "clarify"/, "Router exposes the constrained route contract");
assert.match(router, /function executionIntent/, "Execution intent stays inside the Router implementation");
assert.match(router, /function negates/, "Router can honor explicit negation");
assert.doesNotMatch(router, /protectedActionIntent|protectedActionRules/, "Free-text protected operations are not interpreted by browser keyword rules");
assert.doesNotMatch(router, /"goal\.update"/, "The browser Router type cannot emit a protected Goal action");
assert.doesNotMatch(page, /intentRoute\.actionKind === "goal\.update"|workspace-protected-/, "Free-text send has no legacy protected-action preview branch");
assert.match(chatData, /protected_action: protectedActionProposalSchema/, "Chat accepts one narrow semantic protected-action proposal");
assert.match(chatData, /"operation\.execute"/, "Dashboard accepts canonical operation proposals");
assert.match(chatData, /operation: typedOperationEnvelopeSchema\.nullable\(\)\.optional\(\)/, "Dashboard retains the canonical operation lifecycle");
assert.match(page, /function operationProposalFields/, "Operation details have a dedicated safe projection");
assert.doesNotMatch(page.match(/function operationProposalFields[\s\S]*?\n\}/)?.[0] ?? "", /authorized_principals|payload_digest|parameters\.payload/, "Operation details do not expose private authority or inline payloads");
assert.match(page, /t\("proposal\.primary\.operationGroup"\)/, "Operation confirmation routes users to the bound group");
assert.match(chatData, /result_delivery:/, "Dashboard retains operation result-delivery readback");
assert.match(chatData, /return_delivery\??:/, "Chat messages retain manager return-delivery readback");
assert.match(dashboard, /deliveryByMessage/, "Manager return polling refreshes delivery state after the message arrives");
assert.match(timeline + page, /ReturnDeliveryStatus/, "Both manager conversation surfaces render return delivery state");
for (const state of ["delivered", "verification_required", "explicit_unverified"]) {
  assert.match(returnDelivery, new RegExp(state), `Return delivery renders ${state}`);
}
assert.doesNotMatch(returnDelivery, /message_ref|provider_receipt|intent_digest/, "Provider-private locator facts never enter the return status badge");
assert.match(actionReview, /proposal\.action_kind !== "operation\.execute" \|\| objectValue\(objectValue\(proposal\.operation\)\?\.result_delivery\) !== null/, "An operation is not complete in the Dashboard until result delivery is verified");
assert.match(page, /reviewPlan\.operationFrame/, "Dashboard operation details consume the shared TS review frame");
assert.match(page, /operation\.execute" && proposal\.status === "applied"/, "Dashboard restores terminal operation receipts from the canonical action store");
assert.match(page, /proposal\.action_kind !== "operation\.execute"[\s\S]*reviewPlan\.interaction !== "completed"/, "Pending operation result-card readback remains visible instead of becoming a generic apply error");
assert.match(drawer, /selection\.item\.actionKind !== "operation\.execute"/, "Dashboard hides generic local controls for authenticated group operations");
assert.match(dashboard, /response\.protected_action/, "Agent semantic protected intent is projected only after the Chat response");
assert.match(dashboard, /normalizedMessage\.includes\(normalizedTarget\)/, "A model-invented protected target cannot reach typed preview");
assert.match(page, /if \(semanticPreview\) await createPreview\(semanticPreview\)/, "Semantic intent still enters the typed preview boundary");
for (const legacyClassifier of ["hasHeartbeatIntent", "hasMonitorIntent", "hasTodoCreationIntent", "isExecutionIntent"]) {
  assert.doesNotMatch(page, new RegExp(`function ${legacyClassifier}`), `${legacyClassifier} no longer bypasses the Router contract`);
}
assert.match(dashboard, /Agent 已返回结果/, "A completed task Session advertises its result instead of looking stalled");
assert.match(dashboard, /sessionStatus: hasResult \? "completed"/, "A returned answer outranks a stale transport label in the visible Session status");
assert.match(dashboard, /function visibleAgentMessage[\s\S]*GOAL_\(STATUS\|PROGRESS\)/, "Internal Session protocol markers stay out of the user-facing result");
assert.match(dashboard, /GOAL_EVIDENCE[\s\S]*验证依据：/, "Session evidence uses a readable heading instead of an internal protocol marker");
assert.match(dashboard, /NEXT_ACTION[\s\S]*下一步：/, "Session next actions use a readable heading instead of an internal protocol marker");
assert.match(tasks, /t\("tasks\.viewResult"\)/, "Tasks expose a direct result entry when a Session has answered");
assert.match(tasks, /t\("tasks\.pendingAndRunning"\)/, "Tasks do not imply that every uncompleted Todo already has an active Run");
assert.match(tasks, /t\("tasks\.chatRecent"\)/, "Tasks surface the latest Goal conversation without forcing a tab switch");
// The browser journey verifies that ordinary Chat is read-only and the
// explicit draft action changes only the composer, before any confirmed apply.
assert.doesNotMatch(tasks, /personal-task-capability-callout|tab: "capabilities"/, "Goal Tasks does not spend a full-width row on capability settings");
assert.match(header, /aria-label=\{t\("header\.goalSettings"\)\}/, "The Goal settings entry has an accessible label");
assert.match(header, /onClick=\{onOpenGoalCapabilities\}/, "The Goal settings entry opens the existing capability owner directly");
assert.match(page, /onOpenGoalCapabilities=.*tab: "capabilities"/, "The unified Goal entry opens the selected Goal capability settings directly");
assert.match(goalCapabilitySettings, /fetchGoalConfiguration\(goalId\)/, "Goal capability settings inspect the selected Goal through the path-free API");
assert.match(capabilityWorkbench, /personal-capability-editor-status/, "Shared capability details distinguish editable contracts from read-only capabilities");
for (const settings of [goalCapabilitySettings, machineSettings]) {
  assert.match(settings, /<CapabilityEditorStatus/, "Both scopes use the shared editor status");
  assert.match(settings, /<CapabilityConfigurationSummary/, "Both scopes use the shared value and provenance view");
  assert.match(settings, /canEditCapability\(/, "Both scopes enforce the same editor availability contract");
}
assert.match(goalCapabilitySettings, /previewGoalConfiguration\(goalId/, "Goal capability changes start with a typed preview");
assert.match(goalCapabilitySettings, /preview\.plan_revision/, "Goal capability apply is locked to the reviewed plan revision");
assert.match(goalCapabilitySettings, /applyGoalConfiguration\(/, "Goal capability settings apply only through the revision-locked API");
assert.match(machineSettings, /<CapabilityConfigurationFields/, "Machine settings use the shared typed capability field renderer");
assert.match(goalCapabilitySettings, /<CapabilityConfigurationFields/, "Goal settings use the shared typed capability field renderer");
for (const inputKind of ["boolean", "number", "select", "string_list"]) {
  assert.match(capabilityFields, new RegExp(`input_kind === "${inputKind}"`), `Shared capability fields render ${inputKind}`);
}
assert.match(tasks, /t\("tasks\.convertToTask"\)/, "Tasks offer an explicit preview-first bridge from a reply to task management");
assert.match(page, /function todoTextFromMessage[\s\S]*标题[\s\S]*内容/, "Todo parsing preserves structured title and content fields");
assert.match(page, /t\("home\.taskCount"/, "Home cards expose durable activity when a new Goal has Todos but no run timestamp yet");
assert.match(page, /t\("proposal\.primary\.goalCreate"\)/, "Goal creation names the localized immediate first-turn effect");
assert.match(router, /const asksForMutation/, "Execution routing remains explicit inside the Router contract");
assert.match(timeline, /t\("timeline\.waitingConfirmation"\)/, "Historical gated proposals are grouped into a compact summary");
assert.match(timeline, /gatedItems\.length/, "The compact Gate summary exposes the pending count");
assert.match(timeline, /Boolean\(item\.run\.sessionId\)/, "Running count requires a discovered execution Session");
assert.match(timeline, /Boolean\(item\.run\.canInterrupt\)/, "Running count requires an active interruptible turn");
assert.match(page, /accept="image\/png,image\/jpeg,image\/webp,image\/gif"/, "Composer accepts bounded image types");
assert.match(page, /imageInputRef\.current\?\.click\(\)/, "Attachment button explicitly opens the native file chooser");
assert.match(page, /onDrop=\{\(event\)/, "Composer accepts dragged image files");
assert.match(page, /onPaste=\{[^}]*handleComposerPaste/s, "Composer accepts pasted clipboard images");
assert.match(page, /clipboardData\.items/, "Pasted images use the browser clipboard file payload");
assert.match(page, /maxImageAttachmentCount = 4/, "Composer limits image count");
assert.match(page, /maxImageAttachmentBytes = 5 \* 1024 \* 1024/, "Composer limits image size");
assert.match(timeline, /personal-message-images/, "Sent images remain visible in the conversation");
assert.match(dashboard, /attachments: route\?\.attachments/, "Image attachments enter the selected Agent Session");
assert.match(page, /sendMessage\(t\("composer\.agentProgressPrompt"\)\)/, "Progress report shortcut sends a scoped read-only request immediately");
assert.match(page, /t\("composer\.agentProgress"\)/, "Progress report shortcut makes its immediate-send behavior explicit");
assert.match(page, /t\("composer\.nextAction"\)[\s\S]*sendMessage\(t\("composer\.nextActionPrompt"\)\)/, "Advice shortcut sends its scoped question immediately");
assert.match(page, /t\("composer\.monitor"\)[\s\S]*sendMessage\(t\("composer\.monitorShortcutTemplate", \{ target: t\("schedule\.defaultTarget"\) \}\)\)/, "Monitor shortcut sends its bounded template with a named check target immediately");
assert.match(page, /goalDraftActive[\s\S]*t\("composer\.createGoalDraft"\)[\s\S]*t\("composer\.createGoal"/, "Create Goal mode is visibly distinct from a normal chat draft");
assert.match(page, /setComposerDraft\(`manager:\$\{selectedAgentId\}`,\s*t\("composer\.createGoalTemplate"\)\)/, "Create Goal writes the localized template to the manager draft even when invoked from a Goal");
assert.match(page, /personal-action-feedback/, "Typed actions surface a persistent visible receipt");
assert.match(page, /visibleTimelineItems[\s\S]*item\.run\.runId === activeSessionRun\.runId/, "Session record mode filters unrelated Goal activity");
assert.match(page, /function openGoalConversation\(\)[\s\S]*setActiveSessionRun\(null\)[\s\S]*scrollIntoView\(\{ block: "start" \}\)/, "Opening a Goal reply exits a nested Session and reveals the latest answer");
assert.match(page, /onOpenChat=\{openGoalConversation\}/, "The task reply button uses the same conversation navigation as other Goal entries");
assert.match(header, /header\.refreshing[\s\S]*header\.refreshDone[\s\S]*header\.refreshFailed/, "Refresh exposes localized loading, success, and failure feedback");
assert.match(drawer, /t\("drawer\.proposalExplainer"\)/, "Preview explains what confirmation will do");
assert.match(drawer, /t\("drawer\.proposalApplyFailed"\)/, "Failed preview communicates its no-write result clearly");
assert.match(drawer, /onClick=\{onClose\} type="button">\{t\("drawer\.proposalClose"\)\}/, "Closing a proposal is a pure UI action with zero state transition");
assert.match(drawer, /drawer\.copyRepositoryDone[\s\S]*drawer\.copyRepositorySuccess/, "Repository copy action exposes a visible receipt");
assert.doesNotMatch(drawer, />打开 Goal</, "Goal details do not repeat navigation to the already-open Goal");
assert.match(page, /function prepareScheduleDraft[\s\S]*composer\.monitorTemplateWithoutGoal[\s\S]*composer\.monitorTemplate/, "Manager monitor action opens a localized complete editable configuration draft");
assert.match(page, /function prepareScheduleDraft[\s\S]*composer\.heartbeatTemplateWithoutGoal[\s\S]*composer\.heartbeatTemplate/, "Goal heartbeat action opens a localized editable configuration draft before preview");
assert.match(page, /function structuredGoalIntentFromMessage/, "Goal creation parses the visible form as structured fields");
assert.match(page, /\["目标", "Objective"\]/, "Goal creation accepts Chinese and English objective fields");
assert.match(page, /"Execution boundary \(optional\)"/, "Goal creation accepts an English execution boundary");
assert.match(page, /t\("schedule\.unsupportedCalendar"\)/, "Unsupported calendar schedules use localized fail-closed feedback");
assert.match(page, /function monitorTargetFromMessage/, "Monitor creation preserves the user's requested check target");
assert.match(model, /fields: Array<\{ key: string; label: string; value: string \}>/, "Every action preview field retains a stable semantic key beside its localized label");
assert.match(page, /\.map\(\(\[key, value\]\) => \(\{[\s\S]*key,[\s\S]*label: fieldLabels\[key\]/, "Typed action projection preserves semantic parameter keys while localizing labels");
assert.match(page, /field\.key === "cadence"[\s\S]*field\.key === "stop_condition"[\s\S]*field\.key === "timezone"/, "Applied Heartbeat readback consumes stable semantic keys");
assert.doesNotMatch(page, /field\.label === "cadence"|field\.label === "stop condition"/, "Schedule semantics never depend on localized display labels");
assert.match(page, /defaultTimeline\(model, managerProjectionId, t\)/, "Default schedule projection uses the active locale authority");
assert.match(page, /onOpenGoal: \(goalId\) => \{\s*selectGoal\(goalId\);\s*void reconcileStatus\(\[goalId\]\)/, "Applied-action Goal navigation is immediate and reconciles that Goal in the background");
assert.match(drawer, /onClose\(\); void callbacks\.onOpenGoal\?\.\(goalId\)/, "Applied-action Goal navigation closes its result drawer before asynchronous reconciliation");
assert.match(tasks, /aria-busy=\{quickCompletingTodoIds\?\.has\(todo\.todoId\)/, "Quick Todo completion exposes accessible pending state while its typed preview is prepared");
assert.match(tasks, /disabled=\{quickCompletingTodoIds\?\.has\(todo\.todoId\)\}/, "Quick Todo completion rejects duplicate clicks while preview creation is pending");
assert.match(page, /callbacks\.onGoalActivationStateChange\?\.\(lifecycleChange\.goalId, lifecycleChange\.next\)/, "Goal lifecycle apply projects the requested state before the server responds");
assert.match(page, /model\.goals\.find\(\(goal\) => goal\.goalId === proposal\.goalId\)\?\.activationState/, "Goal lifecycle rollback captures the rendered state instead of assuming the operation inverse");
assert.match(page, /callbacks\.onGoalActivationStateChange\?\.\(lifecycleChange\.goalId, lifecycleChange\.previous\)/, "Rejected Goal lifecycle apply rolls back the optimistic projection");
assert.match(page, /if \(applied\.actionKind === "goal\.lifecycle"\) \{\s*void reconcileStatus\(applied\.goalId \? \[applied\.goalId\] : undefined\)/, "Successful Goal lifecycle apply reconciles the affected Goal without blocking the sidebar");
assert.match(dashboard, /onReconcileStatus=\{\(options\) => loadFromUrl\([\s\S]*\{ background: true, invalidateGoalIds: options\?\.invalidateGoalIds, reuseSnapshots: true \}/, "Lifecycle reconciliation uses the non-fatal background status path");
assert.match(dashboard, /statusRequestCanCommit\(statusRequestFenceRef\.current, request\)/, "A stale background response cannot overwrite a newer optimistic transition");
assert.match(sidebar, /Trash2/, "Stopped Goals expose a delete icon");
assert.match(sidebar, /onRequestGoalLifecycle\(goal, "delete"\)/, "Goal deletion stays behind the lifecycle request boundary");
assert.match(page, /\{ select: operation !== "stop" \}/, "Goal stop suppresses the confirmation drawer while other lifecycle actions retain it");
assert.match(page, /await applyProposal\(proposal, \{[\s\S]*lifecycleProjection: stopProjection \?\? undefined,[\s\S]*presentation: "feedback",[\s\S]*\}\)/, "Goal stop reuses the canonical apply state machine with its optimistic lifecycle projection and surfaces the receipt as feedback");
assert.match(page, /if \(proposal\.reviewPlan\?\.interaction === "direct"\)[\s\S]*setSelection\(\{ item: proposal, kind: "proposal" \}\)/, "A stop action only bypasses review when its typed preview is ready");
assert.match(page, /A newly discovered authority gate always deserves review/, "A direct action escalates a newly discovered authority gate to the drawer");
assert.match(sidebar, /disabled=\{lifecycleBusyGoalIds\?\.has\(goal\.goalId\)\}/, "An in-flight Goal stop cannot be submitted twice from the sidebar");
assert.match(page, /lifecycleOperation === "delete"/, "Goal deletion has an explicit lifecycle operation");
assert.match(page, /callbacks\.onGoalDeleted/, "Successful Goal deletion removes the optimistic sidebar projection");
assert.match(status, /function withoutGoal/, "Status projection can remove a deleted Goal");
assert.match(page, /applied\.reviewPlan\?\.interaction !== "completed"/, "Non-applied typed action results never project as successful Goal deletion");
assert.match(page, /t\("feedback\.stale"\)/, "Stale Goal deletion remains visible with a localized actionable error");
assert.match(page, /activityTimeLabel\(item\.output\.createdAt,\s*locale,\s*t\)/, "Files render locale-aware human-readable output timestamps");
assert.match(drawer, /selection\.kind === "run" \|\| selection\.kind === "proposal" \|\| selection\.kind === "schedule"/, "Advanced diagnostics only appears on objects with actionable runtime details");

for (const field of ["agentId", "todoId", "runId", "safePreview"]) {
  assert.match(model, new RegExp(`${field}\\??:`), `Output exposes ${field}`);
}
assert.match(model, /onExportOutput\??:/, "Output exposes export callback");
assert.match(drawer, /personal-safe-preview/, "Output drawer renders a safe preview");

for (const field of ["timezone", "nextRunAt", "previousRunAt", "notificationRule", "stopCondition", "executionHistory"]) {
  assert.match(model, new RegExp(`${field}\\??:`), `Schedule exposes ${field}`);
}
assert.match(drawer, /personal-execution-history/, "Schedule drawer renders execution history");
assert.match(page, /const heartbeat = schedule\.scheduleKind === "heartbeat"/, "Schedule distinguishes heartbeat lifecycle type");
assert.match(page, /actionKind: heartbeat \? "heartbeat\.bind" : "monitor\.update"/, "Schedule previews preserve heartbeat lifecycle type");

assert.match(drawer, /event\.key === "Tab"/, "Drawer traps keyboard focus");
assert.match(shell, /event\.key !== "Tab"/, "Mobile Goal navigation traps keyboard focus");
assert.match(shell, /restoreFocusRef\.current\?\.focus\(\)/, "Mobile Goal navigation restores focus on close");
assert.match(shell, /aria-modal=\{mobileSidebarOpen \? true : undefined\}/, "Mobile Goal navigation exposes modal semantics");
assert.match(shell, /inert=\{mobileSidebarOpen \|\| undefined\}/, "Mobile Goal navigation removes background content from keyboard navigation");
assert.doesNotMatch(timeline, /<div aria-live="polite" className="personal-channel-timeline">/, "The full timeline is not a live region");
assert.match(timeline, /className="personal-live-region"/, "Timeline has a dedicated live region");
assert.match(drawer, /t\("drawer\.correctionTextarea", \{ agent:[\s\S]*goal:[\s\S]*run:/, "Correction label includes its scoped context");
assert.match(styles, /env\(safe-area-inset-bottom\)/, "Mobile composers respect the safe area");
assert.match(styles, /\.personal-mobile-back/, "Mobile drawer exposes a context back affordance");

assert.match(model, /repository\??:\s*WorkspaceRepositoryContext/, "Goal exposes one repository context");
assert.match(drawer, /t\("drawer\.repository"\)/, "Goal settings display the localized repository label");
assert.match(drawer, /t\("common\.readOnly"\)/, "Repository is visibly read-only");
assert.doesNotMatch(drawer, /Add repository/, "Goal settings do not imply repository binding controls");
assert.match(model, /subagentExecution\??:\s*WorkspaceGoalSubagentConfiguration/, "Goal exposes the projected sub-agent execution boundary");
assert.match(model, /executionConfig\??:\s*string/, "Goal sub-agent settings expose the shared delegation binding pointer");
for (const callback of ["onPreviewGoalSubagentConfiguration", "onApplyGoalSubagentConfiguration"]) {
  assert.match(model, new RegExp(`${callback}\\??:`), `Goal sub-agent settings expose ${callback}`);
  assert.match(drawer, new RegExp(`callbacks\\.${callback}`), `Goal drawer calls ${callback}`);
}
assert.match(drawer, /role="switch"/, "Goal sub-agent control uses an accessible switch");
assert.match(model, /domainCandidates\??:\s*Array/, "Goal carries finite domain choices projected from current Todos");
assert.match(drawer, /type="checkbox"/, "Goal sub-agent domains use an accessible multi-select instead of free text");
assert.match(drawer, /subagentDomainsEmpty/, "Goal sub-agent domains expose an explicit optional empty state");
assert.doesNotMatch(drawer, /!currentSubagentConfiguration\.enabled && subagentAllowedDomains\.length === 0/, "Goal sub-agent execution does not require a task-domain selection");
assert.match(drawer, /subagentPreview && subagentMutationState === "ready"/, "Goal sub-agent writes require a visible preview state");
assert.match(drawer, /normalize.*SubagentDomains|normalizedSubagentDomains/, "Goal sub-agent domains are validated before preview");
assert.match(chatData, /\/api\/chat\/goal-subagents\/dry-run/, "Dashboard uses the local preview-locked Goal sub-agent API");
assert.match(chatData, /\/api\/chat\/goal-subagents\/apply/, "Dashboard applies Goal sub-agent settings through the same local API");
assert.match(chatData, /global_sync\.readback\.verified/, "Goal sub-agent success requires shared-state readback verification");
assert.match(chatData, /execution_config:\s*request\.executionConfig/, "Goal sub-agent writes use the canonical execution-config field");
assert.match(drawer, /subagentExecutionConfigHint/, "Goal sub-agent settings explain the local-private delegation binding boundary");
assert.match(dashboard, /goal\.spawn_policy\?\.mode === "multi_subagent"/, "Rendered switch state comes from the status spawn-policy projection");
assert.match(dashboard, /capabilities\.goal_subagent_configuration === "preview_locked"/, "Goal sub-agent UI requires the authoritative Chat capability opt-in");
assert.match(dashboard, /goalSubagentConfigurationEnabled \? \{[\s\S]*subagentExecution:/, "Capability-off models omit the Goal sub-agent UI contract");
assert.match(dashboard, /personalSubagentDomainCandidates\(payload, row, goalAgentTodos\)/, "Goal domain choices use the full Todo index with compact-row fallback");
assert.match(dashboard, /previewGoalSubagentConfiguration/, "Goal setting preview delegates to the canonical Chat data adapter");
assert.match(dashboard, /applyGoalSubagentConfiguration/, "Goal setting apply delegates to the canonical Chat data adapter");
assert.match(page, /selection\?\.kind === "goal"[\s\S]*workspaceGoals\.find/, "An open Goal drawer follows refreshed status readback");
assert.doesNotMatch(drawer, /localStorage[\s\S]{0,120}subagent|subagent[\s\S]{0,120}localStorage/i, "Goal sub-agent state is never stored in browser-local authority");
assert.match(drawer, /authoritativeSupersedesReceipt/, "A newer authoritative status supersedes an apply receipt without closing the drawer");
assert.match(drawer, /!subagentConfigurationsMatch\([\s\S]*baseline,[\s\S]*authoritativeSubagentConfiguration/, "Status changes away from the pre-apply baseline supersede the receipt even when they do not echo it");
assert.match(i18n, /drawer\.subagentDescription/, "Sub-agent authority boundaries are localized in the Goal drawer");
assert.match(chatData, /align_codex_host_capacity/, "Goal sub-agent confirmation carries the explicit Codex host-capacity request");
assert.match(chatData, /codexHostCapacitySchema/, "Codex host-capacity receipts are schema validated before UI projection");
assert.match(drawer, /subagentHostCapacityRaise/, "Goal sub-agent preview discloses a required Codex capacity raise");
assert.match(drawer, /subagentAppliedRestart/, "Applied host capacity tells the operator that a new Session is required");

for (const lane of ["needs_you", "running", "observing", "scheduled", "history"]) {
  assert.match(model, new RegExp(`"${lane}"`), `Manager home models the ${lane} lane`);
}
assert.match(model, /function workspaceHomeLaneForGoal/, "Manager lane projection is centralized and testable");
assert.match(model, /goal\.state === "推进中" \|\| goal\.state === "需修复"/, "Agent-owned repair work stays in the running lane");
for (const key of ["needsYou", "running", "observing", "scheduled"]) {
  assert.match(page, new RegExp(`home\\.lane\\.${key}`), `Manager home renders localized ${key} lane copy`);
}
assert.match(page, /home\.history/, "Manager home renders localized history copy");
assert.match(page, /personal-home-board/, "Manager home uses the four-lane workspace board");
assert.doesNotMatch(page, /personal-worker-strip/, "Manager home omits the redundant Agent worker strip");
assert.doesNotMatch(header, /切换到野兽主题|切换到默认主题/, "Workspace header does not expose theme switching");
assert.match(workspaceTheme, /workspaceThemeStorageKey = "loopx-pw-theme"/, "Theme preference persists across reloads");
assert.doesNotMatch(dashboard, /isManagerProjectionQuestion/, "Ordinary manager questions do not silently bypass the selected model by matching phrases");
assert.match(dashboard, /if \(selectedRoute\.agentId === "status-only" \|\| \(!targetGoal && targetContextId !== "manager"\)\)/, "Projection answers require the explicit status-only route or a missing Goal fallback");
assert.match(dashboard, /const asksForNextAction[\s\S]*if \(asksForNextAction\)[\s\S]*personalManagerMatches\(question, \["状态"/, "A next-step question outranks a read-only boundary that mentions state");
assert.match(dashboard, /先处理「\$\{personalGoalTitle\(nextTodo\.goalId\)\}」：\$\{nextTodo\.text\}/, "The compact manager answer names the Goal and concrete blocking action");
assert.match(drawer, /t\("drawer\.decisionReview"\)/, "Blocked items preview their decision boundary before any write");
assert.match(drawer, /const hasProjectedRunActivity = selection\.kind === "run"[\s\S]*selection\.item\.completedSteps > 0/, "Session empty-state copy distinguishes projected progress from a truly idle run");
assert.match(drawer, /t\("drawer\.runRecordProjected"/, "A projected run does not claim that the Agent never started");
assert.match(drawer, /t\("drawer\.runRecordEmpty"\)/, "A truly empty Session still explains why there is no timeline yet");
assert.match(page, /function ManagerConversationTray/, "Manager home has a dedicated conversation tray instead of burying replies in the board timeline");
assert.match(page, /managerMessages/, "Manager conversation is derived from the active manager message context");
assert.match(page, /managerConversationReceiptVisible/, "Manager conversation is a send-triggered receipt instead of permanent history chrome");
assert.match(page, /setManagerConversationReceiptVisible\(true\)/, "A new manager send reveals the temporary conversation receipt");
assert.match(page, /setManagerConversationReceiptVisible\(false\)/, "Opening or leaving the receipt clears it from the overview");
assert.match(page, /personal-manager-conversation-tray/, "Manager conversation stays anchored beside the composer");
assert.match(page, /aria-live="polite"/, "Manager conversation announces streamed replies without moving focus");
assert.match(page, /t\("conversation\.full"\)/, "The compact manager conversation can expand in place");
assert.match(page, /onOpenConversation/, "The compact manager conversation has a dedicated full-chat navigation action");
assert.match(page, /managerChatOpen/, "Manager full conversation uses a dedicated Chat view instead of stretching the home tray");
assert.match(page, /managerChatItems/, "Manager Chat only renders conversation and confirmation items");
assert.match(page, /sessionProposalIds\.includes\(item\.proposal\.previewId\)/, "Manager Chat only shows proposals created in the current UI session");
assert.match(page, /\["preview_ready", "gated", "deferred", "applying"\]\.includes\(proposal\.status\)[\s\S]*compileActionReviewPlan\(proposal\)\.retryOriginal === true/, "Restored proposal history uses wire states and preserves retryable original operations");
assert.doesNotMatch(page, /proposal\.title, proposal\.status/, "Proposal dedupe does not split one action into duplicate cards by lifecycle status");
assert.match(header, /header\.managerView/, "Manager Chat exposes explicit overview and Chat navigation");
assert.match(header, /header\.managerOverview/, "Manager Chat can return to the cross-Goal overview");
assert.match(page, /scrollTo\(\{ behavior: "smooth", top: 0 \}\)/, "Returning to the manager overview restores the board's first screen");
assert.match(header, /onOpenManagerChat/, "Manager overview exposes a stable one-click Chat entry");
assert.match(header, /aria-current=\{!managerChatOpen \? "page"/, "Manager overview remains visibly selected before Chat opens");
assert.match(header, /aria-current=\{managerChatOpen \? "page"/, "Manager Chat remains visibly selected after navigation");
assert.doesNotMatch(sidebar, /personal-manager-channels/, "The sidebar does not expose manager state filters as navigation channels");
assert.doesNotMatch(sidebar, /onSelectChannel/, "The sidebar only navigates to the manager or a concrete Goal");
assert.doesNotMatch(page, /selectedChannel|selectChannel\(|__manager_channel__/, "Manager state lanes do not create hidden conversation channels");
assert.match(page, /personal-digest-stats[\s\S]*t\("digest\.completed"\)/, "The away digest is a non-interactive summary");
assert.match(page, /!selectedGoal && !managerChatOpen && managerConversationReceiptVisible[\s\S]*<ManagerConversationTray/, "The tray only appears on the manager overview after a send");
assert.match(page, /managerMessages\.some\(\(message\) => message\.pending\)[\s\S]*setManagerConversationReceiptVisible\(true\)/, "A recovered active Manager turn restores its compact conversation tray");
assert.match(page, /goal\.needsYou \?\? goal\.nextSentence/, "Needs-you cards keep the source Goal action visible");
assert.match(page, /onClick=\{\(\) => onSelectGoal\(goal\.goalId\)\}/, "Manager cards open their source Goal instead of a hidden lane channel");
assert.doesNotMatch(page, /managerConversationActive/, "Sending from the manager never replaces the overview with a separate conversation page");
assert.match(model, /export type WorkspaceSessionMessage/, "Workspace runs expose Session messages for the execution record");
assert.match(model, /sessionMessages\?: WorkspaceSessionMessage\[\]/, "Workspace runs carry Session messages into the control plane");
assert.match(drawer, /t\("drawer\.executionRecordAndResult"\)/, "The Session drawer names its localized primary record view");
assert.match(drawer, /t\("drawer\.detailsAndActions"\)/, "The Session drawer separates metadata and correction controls");
assert.match(drawer, /runDrawerTab/, "The Session drawer keeps an explicit record/details tab state");
assert.match(drawer, /selection\.item\.sessionMessages/, "The Session drawer renders the selected run's real message record");
assert.match(drawer, /t\("drawer\.runRoleUser"\)/, "The execution record labels the initiating user request");
assert.match(drawer, /t\("drawer\.runRoleAssistant"\)/, "The execution record labels completed Agent output");
assert.match(runRow, /t\("tasks\.viewExecution"\)/, "Each run row exposes an explicit localized execution record action");
assert.match(page, /item\.output\.summary \?\? item\.output\.safePreview/, "Files surfaces explain what each output contains");
assert.match(dashboard, /executionSessionSnapshots/, "The dashboard preserves polled Session snapshots for the drawer");
assert.match(dashboard, /const sessionId = run\.sessionId;[\s\S]*setExecutionSessionSnapshots\(\(current\) => \(\{[\s\S]*?\[sessionId\]: snapshot/s, "Opening a Session promotes the fetched snapshot into the authoritative execution projection");
assert.match(dashboard, /\["agent", "assistant"\]\.includes\(role\.trim\(\)\.toLowerCase\(\)\)/, "LoopX agent-store replies and provider assistant replies both count as Session results");
assert.match(page, /latestRun[\s\S]*activeSessionRun[\s\S]*setActiveSessionRun\(latestRun\.run\)/s, "An open Session drawer follows the latest projected run instead of freezing its opening state");
assert.match(page, /<details className="personal-home-history"/, "Completed work is collapsed into history");
assert.match(page, /function activityTimeLabel/, "Manager cards format raw ISO activity time for people");
assert.doesNotMatch(page, />接下来</, "The ambiguous 接下来 lane is not rendered");
assert.match(page, /managerNeedsYouCount[\s\S]*workspaceHomeLaneForGoal\(goal\) === "needs_you"/, "Manager greeting derives attention from the same lane projection");
assert.doesNotMatch(page, /activeRunCount=\{managerRunningCount\}/, "The sidebar no longer duplicates the running lane count");
assert.match(page, /t\("home\.waitingCount", \{ count: managerNeedsYouCount \}\)/, "Manager greeting shows the localized projected needs-you count");
assert.match(dashboard, /targetContextId === "manager"\s*\? model\s*:\s*targetGoal/, "Manager answers use the full cross-Goal projection");
assert.match(page, /managerBlockingCount[\s\S]*goal\.needsYouBlocking \|\| goal\.state === "等你"/, "Manager blocking count includes projected user waits without a parsed Todo");
assert.match(page, /t\("home\.blockingSummary", \{ count: managerBlockingCount \}\)/, "Manager greeting shows the localized projected blocking count");
assert.match(dashboard, /function personalGoalState[\s\S]*hasOpenUserTodo[\s\S]*if \(\["user_or_controller", "controller"\][\s\S]*if \(personalGoalNeedsRepair/, "User gates outrank agent-owned health repair in Goal state projection");
assert.match(dashboard, /if \(goal\.registry_member === false\)[\s\S]*return \[\]/, "Unregistered historical Goals are excluded from the interactive workspace");
assert.match(status, /runGoalSchema[\s\S]*display_name:\s*z\.string\(\)/, "Run history preserves the registered Goal display name");
assert.match(dashboard, /title:\s*personalGoalTitle\(goal\.id,\s*goal\.display_name\)/, "Personal workspace prefers the registered Goal display name");
assert.match(dashboard, /function personalGoalHasPendingOperatorGate/, "Pending operator gates have a durable state projection helper");
assert.match(dashboard, /personalGoalHasPendingOperatorGate\(row\)/, "Pending operator gates project into the needs-you state");
assert.match(dashboard, /explicitUserWait/, "Explicit user-approval language repairs incomplete gate projections");
assert.match(page, /proposal\.status === "applied"/, "Unconfirmed Heartbeat previews never project as active schedules");
assert.match(drawer, /actionKind === "goal\.create" \? t\("drawer\.proposalEnterGoal"\) : t\(selection\.item\.actionKind === "team\.plan" \? "proposal\.teamPlan\.openGoal" : "drawer\.proposalViewGoal"\)/, "Applied actions offer scoped refreshed navigation labels");
assert.doesNotMatch(sidebar, /Agent 设置/, "The sidebar omits the read-only Agent settings dead end");
assert.doesNotMatch(sidebar, /野兽主题|默认主题/, "The sidebar keeps one owner-reviewed visual theme");
for (const key of ["composer.createGoalTemplate", "composer.monitorTemplate", "composer.heartbeatTemplate", "proposal.primary.goalCreate", "proposal.impact.goalCreate"]) {
  assert.match(i18n, new RegExp(`"${key.replaceAll(".", "\\.")}"`), `${key} has a typed locale resource`);
}
assert.match(i18n, /Create a long-term Goal:[\s\S]*Completion criteria:[\s\S]*Related repository \(optional\):[\s\S]*Notification method \(optional\):/, "English Create Goal starts with a useful objective form instead of a host gate");
assert.match(i18n, /我想创建一个长期 Goal：[\s\S]*完成标准：[\s\S]*关联仓库（可选）：[\s\S]*通知方式（可选）：/, "Chinese Create Goal keeps its useful objective form");
assert.match(page, /workspace_ref:\s*"current"/, "Create Goal does not leak another Goal id as its execution workspace");
assert.match(page, /t\("proposal\.workspace\.current"\)/, "Create Goal localizes its execution workspace explanation");
assert.match(i18n, /Current local workspace \(no Repository bound\)/, "English workspace copy explains that no repository is bound");
assert.match(i18n, /当前本地工作区（未绑定 Repository）/, "Chinese workspace copy explains that no repository is bound");
assert.doesNotMatch(model, /kind: "agent"/, "The drawer model omits the read-only Agent settings variant");
assert.match(
  dashboard,
  /statusRequestActive = source\.kind === "example"\s*&& !exampleModeRequested;/,
  "Initial real-status loading must not display bundled example tasks; explicit example mode remains available",
);

assert.match(page, /const settingsPage = settingsOpen \?[\s\S]*<WorkspaceSettingsPage[\s\S]*<div hidden=\{settingsOpen\}>[\s\S]*<WorkspaceShell/, "Settings hide the workspace while preserving its mounted conversation state");
assert.match(sidebar, /t\("settings\.open"\)/, "The sidebar exposes one localized Settings entry");
assert.doesNotMatch(sidebar, /个人工作区/, "The sidebar footer no longer renders a static personal workspace row");
assert.doesNotMatch(workspaceSettings, /NotificationSettingsPanel/, "Settings do not render the old per-Goal notification binding panel");
assert.match(workspaceSettings, /key: "lark"/, "Settings expose a Lark tab");
assert.match(workspaceSettings, /key: "appearance"/, "Settings expose an appearance tab");
assert.match(workspaceSettings, /key: "language"/, "Settings expose a language tab");
assert.match(workspaceSettings, /key: "machine"/, "Settings expose generic machine configuration");
assert.match(workspaceSettings, /<MachineConfigurationSettings/, "Settings mount the machine configuration registry");
assert.match(workspaceSettings, /<LarkSettingsPage[\s\S]*embedded/, "Settings embed the Lark management page");
assert.match(larkSettings, /state: "ready" \| "unverified" \| "not_ready"/, "Lark readiness keeps unverified routes separate from actual failures");
assert.match(larkSettings, /lark\.routesUnverified[\s\S]*unverifiedRouteCount/, "Lark settings summarize only unverified routes with their exact count");
assert.match(i18n, /\{count\} Lark routes pending verification/, "English Lark readiness copy names routes instead of extensions");
assert.match(i18n, /\{count\} 条 Lark 路由尚未验证/, "Chinese Lark readiness copy names routes instead of extensions");
assert.match(workspaceSettings, /t\("settings\.back"\)/, "Settings page has a localized back action");
assert.match(styles, /personal-settings-sidebar/, "Settings page owns its own sidebar navigation");
assert.match(styles, /personal-settings-page\[data-pw-theme="brutal"\]/, "Settings page owns its high-contrast theme styles");
assert.match(workspaceSettings, /role="radiogroup"/, "Settings expose theme and language selection as accessible radio groups");
assert.match(workspaceSettings, /setLocale\(option\.value\)/, "Settings updates the workspace locale");
assert.match(machineSettings, /available_scopes\.includes\("machine"\)/, "Machine configuration only renders capabilities that grant machine-scope configuration");
assert.match(machineSettings, /selected\.capability_id === "periodic_report"/, "Periodic reports expose their governed activation semantics");
assert.match(machineSettings, /selected\.capability_id === "pull_request_review"/, "PR review exposes its queue-ordering-only semantics");
assert.match(machineSettings, /previewMachineConfiguration\(/, "Machine settings require a preview before apply");
assert.match(machineSettings, /applyMachineConfiguration\([\s\S]*preview\.plan_revision/, "Machine settings apply the exact reviewed revision");
assert.match(machineSettings, /previewMachineConfigurationRollback\(/, "Machine settings preview rollback before execution");
assert.match(machineSettings, /liveDefaultDescription/, "Live defaults and Goal overrides are explained together");
assert.match(machineSettings, /inspection\?\.capability_catalog\.capabilities/, "Machine settings discover capabilities from the shared registry catalog");
assert.match(machineSettings, /inspection\?\.invalid_namespaces\[0\]/, "Invalid machine state identifies the affected namespace without reading its stored values");
assert.match(machineSettings, /machine-invalid-repair[\s\S]*role="alert"/, "Invalid machine state exposes a visible guided repair path");
assert.match(machineSettings, /capability\.machine_namespace === invalidNamespace/, "Invalid machine state opens the affected capability editor first");
assert.match(chatData, /status: z\.enum\(\["configured", "absent", "invalid"\]\)/, "Machine inspection accepts the safe invalid repair projection");
assert.match(chatData, /invalid_namespaces: z\.array\(z\.string\(\)\)/, "Machine inspection parses value-free invalid namespace IDs");
assert.match(machineSettings, /personal-capability-json-editor/, "Every machine-configurable capability keeps an advanced JSON fallback");
assert.match(machineSettings, /selected\.machine_namespace, desiredConfiguration/, "Preview targets the selected capability namespace");
assert.match(machineSettings, /previewMachineConfigurationRemoval\(selected\.machine_namespace\)/, "Configured capabilities expose a typed removal preview");
assert.match(machineSettings, /applyMachineConfigurationRemoval\(selected\.machine_namespace, preview\.plan_revision\)/, "Removal applies the exact reviewed revision");
assert.match(machineSettings, /const configured = Boolean\(/, "Only configured capabilities expose removal");
assert.match(machineSettings, /periodicReportActivationDescription/, "Machine periodic reports explain stage-triggered automatic delivery");
assert.match(i18n, /Enabled means automatic delivery at validated stage boundaries/, "English machine settings name automatic stage delivery");
assert.match(i18n, /开启后将在已验证的阶段节点自动投递/, "Chinese machine settings name automatic stage delivery");
assert.match(machineSettings, /localizedCapabilityFieldCopy\(locale\)/, "Machine capability fields follow the selected locale");
assert.match(goalCapabilitySettings, /localizedCapabilityFieldCopy\(locale\)/, "Goal capability fields follow the selected locale");
assert.match(machineSettings, /<CapabilityCatalogNavigation/, "Machine settings use the shared capability catalog navigation");
assert.match(goalCapabilitySettings, /<CapabilityCatalogNavigation/, "Goal settings use the shared capability catalog navigation");
assert.match(goalCapabilitySettings, /capability_id === "lark_event_inbox"[\s\S]*<GoalAutoNotifyToggle/, "Lark inbox capability exposes the independent human-gate notification control");
assert.match(notificationSettings, /disabled=\{busy \|\| notification\?\.configured !== true/, "Gate notification control stays disabled until a Goal Channel is configured");
assert.match(workspaceSettings, /goalNotifications\.find\(\(row\) => row\.goalId === initialGoalId\)/, "Goal capability settings receive the live Goal Channel notification state");
assert.match(machineSettings, /<CapabilityDetailHeader/, "Machine settings use the shared capability detail header");
assert.match(goalCapabilitySettings, /<CapabilityDetailHeader/, "Goal settings use the shared capability detail header");
assert.match(capabilityWorkbench, /localizeCapability\(rawCapability, locale\)/, "Shared navigation localizes capability metadata without changing capability ids");
assert.match(capabilityWorkbench, /capability\.capability_id === "multi_subagent"\) return 3/, "Immature child-agent capacity stays behind mature supported capabilities");
assert.match(capabilityWorkbench, /availability\?\.includes\("experimental"\)/, "Experimental capabilities share a stable late presentation tier");
assert.match(capabilityWorkbench, /configuration_editor\.writable_scopes\.length === 0/, "Provider-bound read-only capabilities follow directly actionable settings");
assert.match(capabilityWorkbench, /orderCapabilitiesForPresentation\(capabilities, locale\)/, "Machine and Goal catalogs use one presentation-order policy");
for (const capabilityId of [
  "change_quality_qualification",
  "coordination_runtime_shadow",
  "explore_graph",
  "explore_harness",
  "lark_event_inbox",
  "lark_kanban_heartbeat_sync",
  "local_authority_shadow",
  "multi_subagent",
  "peer_task_coordination",
  "periodic_report",
  "pull_request_review",
  "reward_memory",
  "steward_executor",
]) {
  const matches = capabilityLocalization.match(new RegExp(`${capabilityId}:`, "g")) ?? [];
  assert.equal(matches.length, 2, `${capabilityId} has English and Simplified Chinese metadata`);
}
for (const fieldKey of ["allowed_domains", "coordinator_agent_id", "eligible_endpoints", "enabled", "executor_endpoint", "executor_model", "executor_reasoning_effort", "max_children", "profile", "profile_preset", "review_priority", "route_ref", "safe_fix", "selection_policy", "strict_receipt", "timezone"]) {
  const matches = capabilityLocalization.match(new RegExp(`^\\s+${fieldKey}:`, "gm")) ?? [];
  assert.equal(matches.length, 2, `${fieldKey} has English and Simplified Chinese field copy`);
}
assert.doesNotMatch(machineSettings, /password|secret|credential/i, "Machine settings do not collect credentials");
assert.match(chatData, /machineConfigurationSchema/, "Machine configuration uses a typed frontend contract");
assert.match(chatData, /machineConfigurationCatalogSchema/, "The frontend validates the generic namespace catalog");
assert.match(chatData, /capabilityConfigurationCatalogSchema/, "Machine and Goal configuration share one capability catalog contract");
assert.match(chatData, /capability_catalog: capabilityConfigurationCatalogSchema/, "Machine inspection preserves the shared catalog for the settings UI");
assert.match(chatData, /goalConfigurationInspectionSchema/, "Goal settings validate the shared capability catalog inspection");
assert.match(chatData, /fetchGoalConfiguration\(goalId: string\)/, "Goal settings use a dedicated path-free configuration inspection endpoint");
assert.match(goalCapabilitySettings, /restoreInheritance/, "Goal overrides expose an explicit path back to live machine defaults");
assert.match(goalCapabilitySettings, /projectEditableCapabilityConfiguration/, "Goal writes project read models onto editor-owned fields");
assert.match(goalCapabilitySettings, /status === "partial_write"/, "Goal settings preserve partial-write recovery outcomes");
assert.match(goalCapabilitySettings, /codex_host_capacity\.write_required/, "Generic Goal capability preview shows the same Codex host-capacity adjustment");
assert.match(goalCapabilitySettings, /host_capacity_pending/, "Generic Goal capability recovery distinguishes a host-capacity partial write from shared-sync failure");
assert.match(chatData, /namespace_configuration:\s*namespaceConfiguration/, "The browser patches one owned namespace without round-tripping private namespaces");
assert.match(chatData, /operation:\s*"remove"/, "The browser uses an explicit typed removal operation");
assert.match(chatData, /z\.enum\(\["create", "update", "delete", "unchanged"\]\)/, "Machine previews recognize deletion as a first-class action");
assert.match(chatData, /\/api\/chat\/machine-configuration\/preview/, "Machine configuration uses a generic preview API");
assert.match(styles, /personal-settings-sidebar[^{]*\{[^}]*overflow-y:\s*auto/, "The settings rail remains scrollable on short screens");
assert.match(i18n, /workspaceLocaleStorageKey = "loopx-pw-locale"/, "Locale persistence uses a stable local key");
assert.match(i18n, /window\.localStorage\.setItem\(workspaceLocaleStorageKey, nextLocale\)/, "Locale selection persists across reloads");
assert.match(i18n, /document\.documentElement\.lang = locale/, "The selected locale updates document language metadata");
assert.match(i18n, /type WorkspaceLocale = "en" \| "zh-CN"/, "Desktop supports English and Simplified Chinese");
assert.match(statusSourceSwitcher, /useWorkspaceI18n/, "Status source controls use the workspace locale");
assert.match(statusSourceSwitcher, /t\("source\.controlPlane"\)/, "Status source controls expose localized accessible labels");
assert.match(larkSettings, /t\("lark\.apps"\)/, "Lark management exposes localized reusable Apps");
assert.match(larkSettings, /t\("lark\.connections"\)/, "Lark management exposes localized Goal Topic connections");
for (const label of ["Connect Lark App", "Group chat", "Bind to Goal", "Create Goal topic automatically", "Topic reply"]) {
  assert.match(i18n, new RegExp(label), `English locale contains ${label}`);
}
assert.match(i18n, /One Lark App · many Goals · one isolated route per Agent/, "Connection cardinality is explicit");
assert.match(larkSettings, /lark_message_permissions_required/, "Missing message permissions receive an actionable error");
assert.match(larkSettings, /selectedApp\?\.reply_ready/, "Connect stays disabled until automatic replies are healthy");
assert.match(larkSettings, /lark\.health\.unavailable/, "Existing unhealthy connections expose localized reply health");
assert.match(larkSettings, /listener_status/, "Connections expose the Goal Topic event listener state");
assert.match(larkSettings, /lark\.health\.listening/, "Healthy Goal Topic listeners have a readable localized status");
assert.match(larkSettings, /lark\.health\.eventUnverified/, "Connections with no received events are not presented as healthy");
assert.match(i18n, /im\.message\.receive_v1/, "Unverified event delivery names the required Feishu event");
assert.match(i18n, /im:message\.group_at_msg:readonly/, "Group mention permission guidance uses the bot scope");
assert.match(i18n, /发布新版/, "Permission guidance reminds operators to publish a new app version");
assert.match(i18n, /未收到消息事件/, "Connections explain when Feishu event delivery has not been observed");
assert.match(larkSettings, /message_context_permission_required/, "Received events with missing context permissions get an actionable repair hint");
assert.match(chatData, /im:message\.group_msg/, "Bot group-history preflight names the application scope");
assert.match(chatData, /im:message\.group_msg\.include_bot:read/, "Bot group-history preflight includes Bot-authored messages");
assert.match(larkSettings, /t\("lark\.historyPermission"\)/, "Bot group-history preflight stays distinct from realtime message context");
assert.match(chatData, /last_event_reason:\s*z\.enum\(larkTopicEventRejectionReasons\)/, "Listener rejection reasons use a typed UI contract");
assert.match(larkSettings, /lark\.health\.notAddressed/, "Ignored unaddressed messages explain why LoopX did not reply");
assert.match(larkSettings, /lark\.health\.routeMismatch/, "Route mismatches receive an actionable connection repair hint");
assert.match(larkSettings, /connectLarkGoalTopic\([^)]*execute:\s*false/s, "Connect flow previews before execution");
assert.match(larkSettings, /connectLarkGoalTopic\([^)]*execute:\s*true/s, "Connect flow performs the approved external write");
assert.match(
  larkSettings,
  /connectAllAgents[\s\S]*targetAgentBindings\s*=\s*goalAgents\.map\(\(agent\)\s*=>\s*\(\{[\s\S]*agentId:\s*agent\.agentId,[\s\S]*appRef:\s*agentAppRefs\[agent\.agentId\]\s*\?\?\s*appRef/,
  "One guided action creates one binding per registered Agent with an explicit-App override and default-App fallback",
);
assert.match(
  larkSettings,
  /targetAgentIds\s*=\s*targetAgentBindings\.map\(\(binding\)\s*=>\s*binding\.agentId\)/,
  "Batch cardinality is derived from the complete per-Agent binding set",
);
assert.match(
  larkSettings,
  /targetAppsReady\s*=\s*Boolean\(editingConnection\)\s*\|\|\s*targetAgentBindings\.length\s*>\s*0\s*&&\s*targetAgentBindings\.every/,
  "New connections require every selected Agent App to be reply-ready while edits preserve their stored identity",
);
assert.match(
  larkSettings,
  /const input\s*=\s*\{[\s\S]*agentBindings:\s*targetAgentBindings,/,
  "Preview and execution receive the complete per-Agent binding set",
);
assert.match(i18n, /Connect every registered Agent/, "Multi-Agent Goal Channel onboarding is explicit");
assert.match(i18n, /Register another Lark App/, "App chooser exposes localized Feishu registration");
assert.match(larkSettings, /startLarkAppSetup/, "Registration starts through the local setup API");
assert.match(larkSettings, /fetchLarkAppSetup/, "Registration polls the local setup session");
assert.match(larkSettings, /window\.open\(/, "Registration opens the provider flow from a user gesture");
assert.match(larkSettings, /window\.open\(window\.location\.href,\s*"_blank"\)/, "Registration never leaves a blank waiting tab");
assert.match(larkSettings, /setAppRef\(snapshot\.app_ref\)/, "Completed registration selects the new App");
assert.match(larkSettings, /loading \? "…" : apps\.length/, "Lark App count does not flash a false zero while loading");
assert.match(larkSettings, /openConnectionEditor\(connection\)/, "Every Lark connection settings button opens a scoped editor");
assert.match(larkSettings, /focusGoalConnection[\s\S]*openConnectionEditor\(connection\)[\s\S]*openConnect\(goals\.find/, "Goal-level Lark entry opens the scoped connection editor or create flow");

// Completed-Todo projection (issue: Personal Workspace hides completed Todo progress).
assert.match(status, /recent_completed_advancement_items/, "The status schema accepts the bounded recent-completed lane");
assert.match(model, /doneTodoCount\??:/, "A Goal exposes the payload completed-Todo count");
assert.match(dashboard, /personalAgentTodoFacts/, "Goal projection derives completion facts from the payload, not open-only item lists");
assert.match(dashboard, /agentTodos:\s*\[\.\.\.goalAgentTodos,\s*\.\.\.agentTodoFacts\.recentCompleted\]/, "Recent completed Todos stay visible in the Goal board");
assert.match(tasks, /<CompletedTaskLane/, "Completed history is owned by its paginated lane");
assert.match(source("./completed-task-lane.tsx"), /setTotal\(page\.total\)/, "The history count comes from the scoped full-history page, not its visible window");
assert.doesNotMatch(tasks, /<span>\{doneAgentTodos\.length\}<\/span>/, "The completed column never reports a false zero");
assert.match(
  dashboard,
  /agentTodoFacts\.nextTodoText,\s*\n\s*row\.queueItem\?\.recommended_action,/,
  "The Goal header prefers the current projected Todo over stale recommended_action strings",
);

console.log("personal workspace drawer contract smoke passed");
