import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import { AttentionDetailCard } from "./attention-detail-card";
import { attentionSuccessor, canReviewAttention } from "./attention-details";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Bell,
  Bot,
  CalendarClock,
  Check,
  ChevronDown,
  Copy,
  Download,
  ExternalLink,
  GitBranch,
  MessageCircleQuestion,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Pause,
  Play,
  Radio,
  RotateCcw,
  Send,
  Square,
  X,
} from "lucide-react";

import type {
  PersonalWorkspaceCallbacks,
  WorkspaceAgentOption,
  WorkspaceAttention,
  WorkspaceDrawerSelection,
  WorkspaceGoal,
  WorkspaceGoalNotification,
  WorkspaceGoalSubagentConfiguration,
  WorkspaceRun,
  WorkspaceTodo,
} from "./personal-workspace-model";
import type { LarkGoalConnection } from "../../data/chat";
import { localizedAttentionAge, localizedGoalState, localizedSessionStatus, useWorkspaceI18n } from "./i18n";
import { formatCostUsd, formatDurationMs, formatTokenCount, formatUsageValue } from "./personal-workspace-model";
import { TeamPlanResult } from "./team-plan-result";
import { parseTodoResumeCondition } from "./todo-resume-condition";

function subagentModelRequest(include: boolean, model: string, effort: string) {
  if (!include) return {};
  if (!model.trim()) return { modelConfig: null };
  const modelConfig: { model: string; reasoning_effort?: string } = { model: model.trim() };
  if (effort) modelConfig.reasoning_effort = effort;
  return { modelConfig };
}

const focusableSelector = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "select:not([disabled])",
  "input:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

type TodoOperation = "block" | "complete" | "defer" | "successor_create";

type GoalSubagentPreview = {
  codexHostCapacity?: WorkspaceGoalSubagentConfiguration["codexHostCapacity"];
  modelConfig?: { model: string; reasoning_effort?: string } | null;
  executionConfig?: string;
  allowedDomains: string[];
  changed: boolean;
  enabled: boolean;
  goalId: string;
  maxChildren: number;
  previewId: string;
};

const todoTransitions = [
  { key: "drawer.taskBlock", operation: "block" },
  { key: "drawer.taskSuccessor", operation: "successor_create" },
] as const satisfies readonly { key: "drawer.taskBlock" | "drawer.taskSuccessor"; operation: TodoOperation }[];

const decisionTransitions = [
  { key: "drawer.decisionReject", resolution: "reject" },
  { key: "drawer.decisionDefer", resolution: "defer" },
] as const;

const subagentChildLimits = Array.from({ length: 32 }, (_, index) => index + 1);
const subagentDomainPattern = /^[a-z][a-z0-9_.-]{0,63}$/u;

function normalizeSubagentDomain(value: string | null | undefined) {
  const normalized = String(value ?? "").trim().toLowerCase();
  return subagentDomainPattern.test(normalized) ? normalized : null;
}

function subagentConfigurationsMatch(
  left: WorkspaceGoalSubagentConfiguration,
  right: WorkspaceGoalSubagentConfiguration,
) {
  return left.enabled === right.enabled
    && left.maxChildren === right.maxChildren
    && JSON.stringify(left.modelConfig ?? null) === JSON.stringify(right.modelConfig ?? null)
    && (left.executionConfig ?? "") === (right.executionConfig ?? "")
    && [...left.allowedDomains].sort((a, b) => a.localeCompare(b)).join("\u0000")
      === [...right.allowedDomains].sort((a, b) => a.localeCompare(b)).join("\u0000");
}

type ContextDrawerSelection = Exclude<WorkspaceDrawerSelection, { kind: "settings" }>;

export function ContextDrawer({ agents, attentionHistory = [], onSelectAttention, callbacks, goalNotifications = [], goals = [], inspectorExpanded = false, larkConnections = [], onClose, onToggleInspectorSize, readOnly = false, runs = [], selection }: {
  agents: WorkspaceAgentOption[];
  attentionHistory?: WorkspaceAttention[];
  onSelectAttention?: (item: WorkspaceAttention) => void;
  callbacks: PersonalWorkspaceCallbacks;
  goalNotifications?: WorkspaceGoalNotification[];
  goals?: WorkspaceGoal[];
  inspectorExpanded?: boolean;
  larkConnections?: LarkGoalConnection[];
  onClose: () => void;
  onToggleInspectorSize?: () => void;
  readOnly?: boolean;
  runs?: WorkspaceRun[];
  selection: ContextDrawerSelection;
}) {
  const { locale, t } = useWorkspaceI18n();
  const [correction, setCorrection] = useState("");
  const [diagnosticsOpen, setDiagnosticsOpen] = useState(false);
  const [repositoryCopyState, setRepositoryCopyState] = useState<"idle" | "copied" | "error">("idle");
  const [runDrawerTab, setRunDrawerTab] = useState<"record" | "details">("record");
  const [subagentAllowedDomains, setSubagentAllowedDomains] = useState<string[]>([]);
  const [subagentFeedback, setSubagentFeedback] = useState<string | null>(null);
  const [subagentMaxChildren, setSubagentMaxChildren] = useState(2);
  const [subagentModel, setSubagentModel] = useState("");
  const [subagentEffort, setSubagentEffort] = useState("");
  const [subagentExecutionConfig, setSubagentExecutionConfig] = useState("");
  const [subagentMutationState, setSubagentMutationState] = useState<"idle" | "previewing" | "ready" | "applying" | "success" | "warning" | "error">("idle");
  const [subagentPreview, setSubagentPreview] = useState<GoalSubagentPreview | null>(null);
  const [verifiedSubagentConfiguration, setVerifiedSubagentConfiguration] = useState<WorkspaceGoalSubagentConfiguration | null>(null);
  const lastAuthoritativeSubagentConfigurationRef = useRef<WorkspaceGoalSubagentConfiguration | null>(null);
  const verifiedSubagentBaselineRef = useRef<WorkspaceGoalSubagentConfiguration | null>(null);
  const [todoAgentId, setTodoAgentId] = useState(agents.find((agent) => agent.available)?.agentId ?? "codex");
  const [todoPriority, setTodoPriority] = useState("");
  const [todoResumeWhen, setTodoResumeWhen] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const selectionIdentity = selection.kind === "run" ? `run:${selection.item.runId}`
    : selection.kind === "proposal" ? `proposal:${selection.item.previewId}`
      : selection.kind === "todo" ? `todo:${selection.item.todoId}`
        : selection.kind === "attention" ? `attention:${selection.item.todoId}`
          : selection.kind === "output" ? `output:${selection.item.outputId}`
            : selection.kind === "schedule" ? `schedule:${selection.item.scheduleId}`
                : `goal:${selection.item.goalId}`;

  useEffect(() => {
    setRepositoryCopyState("idle");
    setDiagnosticsOpen(false);
    setRunDrawerTab("record");
    setTodoResumeWhen("");
    const configuration = selection.kind === "goal" ? selection.item.subagentExecution : undefined;
    setSubagentAllowedDomains(configuration?.allowedDomains ?? []);
    setSubagentModel(configuration?.modelConfig?.model ?? "");
    setSubagentEffort(configuration?.modelConfig?.reasoning_effort ?? "");
    setSubagentExecutionConfig(configuration?.executionConfig ?? "");
    setSubagentMaxChildren(configuration?.maxChildren ? Math.min(configuration.maxChildren, 32) : 2);
    setSubagentFeedback(null);
    setSubagentMutationState("idle");
    setSubagentPreview(null);
    setVerifiedSubagentConfiguration(null);
    lastAuthoritativeSubagentConfigurationRef.current = configuration ?? null;
    verifiedSubagentBaselineRef.current = null;
  }, [selectionIdentity]);

  const authoritativeSubagentConfiguration = selection.kind === "goal"
    ? selection.item.subagentExecution
    : undefined;

  useEffect(() => {
    const previousAuthoritativeConfiguration = lastAuthoritativeSubagentConfigurationRef.current;
    const authoritativeConfigurationChanged = authoritativeSubagentConfiguration
      ? !previousAuthoritativeConfiguration
        || !subagentConfigurationsMatch(
          previousAuthoritativeConfiguration,
          authoritativeSubagentConfiguration,
        )
      : previousAuthoritativeConfiguration !== null;
    lastAuthoritativeSubagentConfigurationRef.current = authoritativeSubagentConfiguration
      ?? null;
    if (!verifiedSubagentConfiguration) {
      if (authoritativeConfigurationChanged && authoritativeSubagentConfiguration) {
        setSubagentAllowedDomains(authoritativeSubagentConfiguration.allowedDomains);
        setSubagentModel(authoritativeSubagentConfiguration.modelConfig?.model ?? "");
        setSubagentEffort(authoritativeSubagentConfiguration.modelConfig?.reasoning_effort ?? "");
        setSubagentExecutionConfig(authoritativeSubagentConfiguration.executionConfig ?? "");
        setSubagentMaxChildren(authoritativeSubagentConfiguration.maxChildren || 2);
        setSubagentFeedback(null);
        setSubagentMutationState("idle");
        setSubagentPreview(null);
      }
      return;
    }
    const baseline = verifiedSubagentBaselineRef.current;
    const authoritativeSupersedesReceipt = !authoritativeSubagentConfiguration
      || subagentConfigurationsMatch(
        verifiedSubagentConfiguration,
        authoritativeSubagentConfiguration,
      )
      || Boolean(
        baseline
        && !subagentConfigurationsMatch(
          baseline,
          authoritativeSubagentConfiguration,
        ),
      );
    if (authoritativeSupersedesReceipt) {
      if (authoritativeSubagentConfiguration) {
        setSubagentAllowedDomains(authoritativeSubagentConfiguration.allowedDomains);
        setSubagentModel(authoritativeSubagentConfiguration.modelConfig?.model ?? "");
        setSubagentEffort(authoritativeSubagentConfiguration.modelConfig?.reasoning_effort ?? "");
        setSubagentExecutionConfig(authoritativeSubagentConfiguration.executionConfig ?? "");
        setSubagentMaxChildren(
          authoritativeSubagentConfiguration.maxChildren || 2,
        );
      }
      verifiedSubagentBaselineRef.current = null;
      setVerifiedSubagentConfiguration(null);
    }
  }, [authoritativeSubagentConfiguration, verifiedSubagentConfiguration]);

  useEffect(() => {
    const activeElement = document.activeElement;
    if (activeElement instanceof HTMLElement && !drawerRef.current?.contains(activeElement)) {
      returnFocusRef.current = activeElement;
    }
    const frame = window.requestAnimationFrame(() => titleRef.current?.focus());
    return () => window.cancelAnimationFrame(frame);
  }, [selectionIdentity]);

  const closeDrawer = useCallback(() => {
    const returnFocus = returnFocusRef.current;
    onClose();
    window.requestAnimationFrame(() => returnFocus?.focus());
  }, [onClose]);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeDrawer();
        return;
      }
      if (event.key === "Tab" && selection.kind !== "todo") {
        const focusable = Array.from(drawerRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [])
          .filter((element) => !element.hasAttribute("disabled") && element.getAttribute("aria-hidden") !== "true");
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [closeDrawer, selection.kind]);

  const title = selection.kind === "attention" ? t("drawer.titleAttention")
    : selection.kind === "todo" ? t("drawer.taskDetails")
      : selection.kind === "run" ? t("drawer.runDetails")
        : selection.kind === "output" ? t("drawer.titleOutput")
          : selection.kind === "proposal" && selection.item.actionKind === "team.plan" && selection.item.status === "applied" ? t("proposal.teamPlan.resultTitle")
          : selection.kind === "proposal" ? t(selection.item.reviewPlan?.retryOriginal ? "drawer.recoverEditResult" : selection.item.status === "applied" ? "drawer.titleProposalApplied" : "drawer.titleProposalConfirm")
            : selection.kind === "schedule" ? (selection.item.scheduleKind === "heartbeat" ? "Heartbeat" : t("drawer.titleSchedule"))
              : t("drawer.goalDetails");
  const goalId = selection.kind === "proposal" ? selection.item.goalId ?? "manager"
      : selection.item.goalId;
  const contextLabel = selection.kind === "attention" ? selection.item.goalTitle ?? t("drawer.currentGoal")
    : selection.kind === "todo" ? selection.item.goalTitle
      : selection.kind === "run" ? selection.item.goalTitle
        : selection.kind === "output" ? selection.item.goalTitle ?? t("drawer.currentGoal")
          : selection.kind === "goal" ? selection.item.title
            : selection.kind === "schedule" ? t("drawer.goalAutoRun")
              : selection.kind === "proposal" && selection.item.status === "applied" && selection.item.actionKind === "team.plan" ? selection.item.goalId ?? t("drawer.currentGoal")
              : selection.item.goalId ? t("drawer.goalChanges") : t("drawer.managerChanges");
  const selectedGoalRun = selection.kind === "goal"
    ? runs.find((run) => run.goalId === selection.item.goalId && Boolean(run.sessionId))
      ?? runs.find((run) => run.goalId === selection.item.goalId)
    : null;
  const hasProjectedRunActivity = selection.kind === "run" && (
    selection.item.completedSteps > 0
    || Boolean(selection.item.latestActivity)
    || Boolean(selection.item.outputs?.length)
  );
  const attentionAge = selection.kind === "attention" ? localizedAttentionAge(selection.item.updatedAt, t) : null;
  const normalizedTodoResumeWhen = parseTodoResumeCondition(todoResumeWhen);

  async function sendCorrection() {
    if (selection.kind !== "run" || !correction.trim()) return;
    await callbacks.onCorrectRun?.(selection.item, correction.trim());
    setCorrection("");
  }

  async function previewTodoTransition(todo: WorkspaceTodo, operation: TodoOperation, label: string, resumeWhen?: string) {
    if (operation === "successor_create") {
      await callbacks.onPreviewAction?.({
        actionKind: "todo.create",
        context: { goal_id: todo.goalId, kind: "todo", todo_id: todo.todoId },
        idempotencyKey: `workspace-todo-successor-${todo.todoId}-${Date.now().toString(36)}`,
        normalizedParameters: { goal_id: todo.goalId, text: t("drawer.taskSuccessorText", { task: todo.text }) },
        summary: t("drawer.taskSuccessorSummary", { task: todo.text }),
      });
      return;
    }
    await callbacks.onPreviewAction?.({
      actionKind: "todo.update",
      context: { goal_id: todo.goalId, kind: "todo", todo_id: todo.todoId },
      idempotencyKey: `workspace-todo-${todo.todoId}-${operation}-${Date.now().toString(36)}`,
      normalizedParameters: {
        agent_id: todo.claimedBy ?? todoAgentId,
        goal_id: todo.goalId,
        operation,
        ...(operation === "block" ? { note: t("drawer.taskSuccessorNote") } : {}),
        ...(operation === "defer" && resumeWhen ? { resume_when: resumeWhen } : {}),
        todo_id: todo.todoId,
      },
      summary: `${label}：${todo.text}`,
    });
  }

  async function previewDecision(attention: WorkspaceAttention, decision: "approve" | typeof decisionTransitions[number]["resolution"], label: string) {
    if (readOnly || !canReviewAttention(attention)) return;
    await callbacks.onPreviewAction?.({
      actionKind: "gate.resolve",
      context: { goal_id: attention.goalId, kind: "todo", todo_id: attention.todoId },
      idempotencyKey: `workspace-decision-${attention.todoId}-${decision}-${Date.now().toString(36)}`,
      normalizedParameters: {
        goal_id: attention.goalId,
        decision,
        todo_id: attention.todoId,
      },
      summary: `${label}：${attention.text}`,
    });
  }

  const currentSubagentConfiguration: WorkspaceGoalSubagentConfiguration = selection.kind === "goal"
    ? verifiedSubagentConfiguration
      ?? selection.item.subagentExecution
      ?? { allowedDomains: [], domainCandidates: [], enabled: false, maxChildren: 0 }
    : { allowedDomains: [], domainCandidates: [], enabled: false, maxChildren: 0 };
  const subagentBusy = subagentMutationState === "previewing" || subagentMutationState === "applying";
  const subagentDomainOptions = (() => {
    if (selection.kind !== "goal") return [];
    const options = new Map<string, { matchingTodoCount: number; value: string }>();
    for (const configuredDomain of currentSubagentConfiguration.allowedDomains) {
      const value = normalizeSubagentDomain(configuredDomain);
      if (value) options.set(value, { matchingTodoCount: 0, value });
    }
    if (currentSubagentConfiguration.domainCandidates) {
      for (const candidate of currentSubagentConfiguration.domainCandidates) {
        const value = normalizeSubagentDomain(candidate.domain);
        if (!value) continue;
        const existing = options.get(value);
        options.set(value, {
          matchingTodoCount: (existing?.matchingTodoCount ?? 0) + candidate.matchingTodoCount,
          value,
        });
      }
    } else {
      for (const todo of selection.item.agentTodos) {
        if (todo.done || todo.taskClass !== "advancement_task") continue;
        const value = normalizeSubagentDomain(todo.taskDomain);
        if (!value) continue;
        const existing = options.get(value);
        options.set(value, {
          matchingTodoCount: (existing?.matchingTodoCount ?? 0) + 1,
          value,
        });
      }
    }
    return [...options.values()];
  })();

  function resetSubagentDraft() {
    setSubagentAllowedDomains(currentSubagentConfiguration.allowedDomains);
    setSubagentModel(currentSubagentConfiguration.modelConfig?.model ?? "");
    setSubagentEffort(currentSubagentConfiguration.modelConfig?.reasoning_effort ?? "");
    setSubagentExecutionConfig(currentSubagentConfiguration.executionConfig ?? "");
    setSubagentMaxChildren(currentSubagentConfiguration.maxChildren || 2);
    setSubagentFeedback(null);
    setSubagentMutationState("idle");
    setSubagentPreview(null);
  }

  function normalizedSubagentDomains() {
    const domains = [...new Set(subagentAllowedDomains.map((value) => normalizeSubagentDomain(value)))];
    return domains.every((domain): domain is string => Boolean(domain)) ? domains : null;
  }

  function toggleSubagentDomain(domain: string, selected: boolean) {
    setSubagentAllowedDomains((current) => selected
      ? [...current, domain].filter((value, index, values) => values.indexOf(value) === index)
      : current.filter((value) => value !== domain));
    setSubagentPreview(null);
    setSubagentMutationState("idle");
    setSubagentFeedback(null);
  }

  async function previewGoalSubagentConfiguration(enabled: boolean, includeModel = enabled) {
    if (selection.kind !== "goal" || !callbacks.onPreviewGoalSubagentConfiguration) return;
    const allowedDomains = enabled ? normalizedSubagentDomains() : [];
    if (includeModel && !subagentModel.trim() && subagentEffort) {
      setSubagentMutationState("error");
      setSubagentFeedback(t("drawer.subagentModelRequired"));
      return;
    }
    if (enabled && !allowedDomains) {
      setSubagentMutationState("error");
      setSubagentFeedback(t("drawer.subagentDomainInvalid"));
      setSubagentPreview(null);
      return;
    }
    const request = {
      alignCodexHostCapacity: enabled,
      allowedDomains: allowedDomains ?? [],
      enabled,
      goalId: selection.item.goalId,
      maxChildren: enabled ? subagentMaxChildren : 0,
      executionConfig: subagentExecutionConfig.trim(),
      ...subagentModelRequest(includeModel, subagentModel, subagentEffort),
    };
    setSubagentMutationState("previewing");
    setSubagentFeedback(t("drawer.subagentPreviewing"));
    setSubagentPreview(null);
    try {
      const preview = await callbacks.onPreviewGoalSubagentConfiguration(request);
      if (!preview.changed) {
        verifiedSubagentBaselineRef.current = authoritativeSubagentConfiguration
          ?? null;
        setVerifiedSubagentConfiguration({
          ...preview.configuration,
          domainCandidates: currentSubagentConfiguration.domainCandidates,
        });
        setSubagentAllowedDomains(preview.configuration.allowedDomains);
        setSubagentModel(preview.configuration.modelConfig?.model ?? "");
        setSubagentEffort(preview.configuration.modelConfig?.reasoning_effort ?? "");
        setSubagentExecutionConfig(preview.configuration.executionConfig ?? "");
        setSubagentMaxChildren(preview.configuration.maxChildren || 2);
        setSubagentMutationState("success");
        setSubagentFeedback(t("drawer.subagentNoChange"));
        return;
      }
      setSubagentPreview({
        ...request,
        codexHostCapacity: preview.configuration.codexHostCapacity,
        changed: preview.changed,
        previewId: preview.previewId,
      });
      setSubagentMutationState("ready");
      setSubagentFeedback(t("drawer.subagentPreviewReady"));
    } catch (error) {
      setSubagentMutationState("error");
      setSubagentFeedback(error instanceof Error ? error.message : t("drawer.subagentPreviewFailed"));
    }
  }

  async function applyGoalSubagentConfiguration() {
    if (!subagentPreview || !callbacks.onApplyGoalSubagentConfiguration) return;
    setSubagentMutationState("applying");
    setSubagentFeedback(t("drawer.subagentApplying"));
    try {
      const verifiedConfiguration = await callbacks.onApplyGoalSubagentConfiguration({
        allowedDomains: subagentPreview.allowedDomains,
        alignCodexHostCapacity: subagentPreview.enabled,
        enabled: subagentPreview.enabled,
        goalId: subagentPreview.goalId,
        maxChildren: subagentPreview.maxChildren,
        modelConfig: subagentPreview.modelConfig,
        executionConfig: subagentPreview.executionConfig,
        previewId: subagentPreview.previewId,
      });
      verifiedSubagentBaselineRef.current = authoritativeSubagentConfiguration
        ?? null;
      setVerifiedSubagentConfiguration({
        ...verifiedConfiguration,
        domainCandidates: currentSubagentConfiguration.domainCandidates,
      });
      setSubagentAllowedDomains(verifiedConfiguration.allowedDomains);
      setSubagentModel(verifiedConfiguration.modelConfig?.model ?? "");
      setSubagentEffort(verifiedConfiguration.modelConfig?.reasoning_effort ?? "");
      setSubagentExecutionConfig(verifiedConfiguration.executionConfig ?? "");
      setSubagentMaxChildren(verifiedConfiguration.maxChildren || 2);
      setSubagentMutationState("success");
      setSubagentFeedback(t(
        verifiedConfiguration.codexHostCapacity?.newSessionRequired
          ? "drawer.subagentAppliedRestart"
          : "drawer.subagentApplied",
      ));
      setSubagentPreview(null);
      try {
        await callbacks.onRefresh?.();
      } catch {
        setSubagentMutationState("warning");
        setSubagentFeedback(t("drawer.subagentAppliedRefreshFailed"));
      }
    } catch (error) {
      setSubagentMutationState("error");
      setSubagentFeedback(error instanceof Error ? error.message : t("drawer.subagentApplyFailed"));
    }
  }

  return (
    <div
      aria-labelledby="personal-drawer-title"
      aria-modal={selection.kind === "todo" ? undefined : "true"}
      className="personal-context-drawer"
      data-context-kind={selection.kind}
      ref={drawerRef}
      role="dialog"
    >
      <header className={`personal-drawer-header${selection.kind === "todo" ? " is-task-inspector" : ""}`}>
        <div><h2 id="personal-drawer-title" ref={titleRef} tabIndex={-1}>{title}</h2><p>{contextLabel}</p></div>
        <div className="personal-drawer-header-actions">
          {selection.kind === "todo" && onToggleInspectorSize ? <button aria-label={inspectorExpanded ? t("drawer.inspectorHalf") : t("drawer.inspectorFull")} className="personal-icon-button personal-inspector-size" onClick={onToggleInspectorSize} title={inspectorExpanded ? t("drawer.inspectorHalfView") : t("drawer.inspectorFullView")} type="button">
            {inspectorExpanded ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
          </button> : null}
          <button aria-label={t("drawer.closeDetail", { context: contextLabel })} className="personal-icon-button personal-drawer-close" onClick={closeDrawer} ref={closeRef} type="button">
            <ArrowLeft className="personal-mobile-back" size={18} />
            <X className="personal-desktop-close" size={18} />
          </button>
        </div>
      </header>

      <div className="personal-drawer-body">
        {selection.kind === "attention" ? (
          <>
            <section className="personal-detail-card is-attention">
              <small>{selection.item.blocking ? t("drawer.attentionBlocking") : t("drawer.attentionWaiting")}</small>
              <h3>{selection.item.text}</h3>
              <dl>
                <div><dt>Goal</dt><dd>{selection.item.goalTitle ?? selection.item.goalId}</dd></div>
                <div><dt>{t("drawer.priority")}</dt><dd>{selection.item.priority ?? "medium"}</dd></div>
                {attentionAge ? <div><dt>{t("common.waiting")}</dt><dd>{t("tasks.waitingAge", { age: attentionAge })}</dd></div> : null}

              </dl>
            </section>
            <AttentionDetailCard item={selection.item} onSelect={onSelectAttention} successor={attentionSuccessor(selection.item, attentionHistory)} />
            {!readOnly && canReviewAttention(selection.item) ? <>
              <button className="personal-primary-action" onClick={() => void previewDecision(selection.item, "approve", t("common.confirm"))} type="button"><Check size={17} />{t("drawer.decisionReview")}</button>
              <details className="personal-compact-menu">
                <summary><MoreHorizontal size={17} />{t("drawer.decisionMore")}</summary>
                <div>
                  <button onClick={() => void callbacks.onExplainDecision?.(selection.item)} type="button"><MessageCircleQuestion size={16} />{t("drawer.explainDecision")}</button>
                  {decisionTransitions.map((transition) => (
                    <button key={transition.resolution} onClick={() => void previewDecision(selection.item, transition.resolution, t(transition.key))} type="button">{t(transition.key)}</button>
                  ))}
                </div>
              </details>
            </> : null}
          </>
        ) : null}

        {selection.kind === "todo" ? (
          <>
            <section className="personal-task-inspector-summary">
              <div className="personal-task-inspector-status">
                <span className={selection.item.done ? "is-done" : selection.item.status === "blocked" ? "is-blocked" : "is-open"}>
                  <i />{selection.item.done ? t("drawer.taskStatusCompleted") : selection.item.status === "deferred" ? t("drawer.taskStatusDeferred") : selection.item.status === "blocked" ? t("drawer.taskStatusBlocked") : t("drawer.taskStatusOpen")}
                </span>
                {selection.item.priority ? <span>{selection.item.priority}</span> : null}
                <span>{selection.item.taskClass === "advancement_task" ? t("drawer.taskAdvancement") : selection.item.taskClass ?? t("drawer.taskOrdinary")}</span>
              </div>
              <h3>{selection.item.text}</h3>
            </section>
            <section aria-label={t("drawer.taskInfo")} className="personal-task-inspector-fields">
              <h4>{t("drawer.taskInfo")}</h4>
              <dl>
                <div><dt>Goal</dt><dd>{selection.item.goalTitle}</dd></div>
                <div><dt>{t("common.owner")}</dt><dd>{selection.item.ownerLabel ?? selection.item.claimedBy ?? t("drawer.notAssigned")}</dd></div>
                <div><dt>{t("common.status")}</dt><dd>{selection.item.done ? t("drawer.taskStatusCompleted") : selection.item.status === "deferred" ? t("drawer.taskStatusDeferred") : selection.item.status === "blocked" ? t("drawer.taskStatusBlocked") : t("drawer.taskStatusOpen")}</dd></div>
                <div><dt>{t("drawer.priority")}</dt><dd>{selection.item.priority ?? t("drawer.notSet")}</dd></div>
                <div><dt>{t("drawer.dependencies")}</dt><dd>{selection.item.dependencies?.join(" · ") || t("common.none")}</dd></div>
                {selection.item.status === "deferred" || selection.item.resumeWhen ? <div><dt>{t("drawer.resumeWhen")}</dt><dd>{selection.item.resumeWhen || t("drawer.notSet")}</dd></div> : null}
                {selection.item.resumeWhen ? <div><dt>{t("drawer.resumeState")}</dt><dd>{selection.item.resumeReady ? t("drawer.resumeReady") : t("drawer.resumePending")}</dd></div> : null}
                {selection.item.resumeReceiptId ? <div><dt>{t("drawer.resumeReceipt")}</dt><dd>{selection.item.resumeReceiptId}</dd></div> : null}
                {selection.item.validationDigest ? <>
                  <div><dt>{t("drawer.validationRevision")}</dt><dd>{selection.item.validationRevision ?? 0}</dd></div>
                  <div><dt>{t("drawer.validationDigest")}</dt><dd><code>{selection.item.validationDigest}</code></dd></div>
                  {selection.item.validationRevisionActor ? <div><dt>{t("drawer.validationRevisionActor")}</dt><dd>{selection.item.validationRevisionActor}</dd></div> : null}
                </> : null}
                <div><dt>{t("drawer.nextTransition")}</dt><dd>{selection.item.nextTransition ?? (selection.item.done ? t("drawer.taskNextCompleted") : selection.item.resumeReady ? t("drawer.taskNextResumeReady") : selection.item.status === "deferred" ? t("drawer.taskNextDeferred") : t("drawer.taskNextOpen"))}</dd></div>
              </dl>
            </section>
            {!readOnly && !selection.item.done ? <div className="personal-task-inspector-actions" aria-label={t("drawer.taskActions")}>
              <details className="personal-task-management">
                <summary><MoreHorizontal size={16} />{t("drawer.taskManage")}</summary>
                <div>
                  <strong>{t("drawer.reassign")}</strong>
                  <label className="personal-inline-agent-select">{t("drawer.reassign")}
                    <select aria-label={t("drawer.reassign")} onChange={(event) => setTodoAgentId(event.target.value)} value={todoAgentId}>
                      {agents.filter((agent) => agent.available).map((agent) => <option key={agent.agentId} value={agent.agentId}>{agent.label}</option>)}
                    </select>
                    <button className="personal-secondary-action" onClick={() => void callbacks.onPreviewAction?.({
                      actionKind: "todo.update",
                      context: { goal_id: selection.item.goalId, kind: "todo", todo_id: selection.item.todoId },
                      idempotencyKey: `workspace-todo-${selection.item.todoId}-reassign-${todoAgentId}-${Date.now().toString(36)}`,
                      normalizedParameters: { agent_id: todoAgentId, goal_id: selection.item.goalId, operation: "reassign", todo_id: selection.item.todoId },
                      summary: t("drawer.reassignSummary", { task: selection.item.text }),
                    })} type="button">{t("timeline.review")}</button>
                  </label>
                  <label className="personal-inline-agent-select">{t("drawer.taskPriority")}
                    <select aria-label={t("drawer.taskPriority")} value={todoPriority} onChange={(event) => setTodoPriority(event.target.value)}>
                      <option value="">{t("drawer.taskPriorityChoose")}</option>
                      {["P0", "P1", "P2", "P3", "P4"].map((priority) => <option key={priority} value={priority}>{priority}</option>)}
                      <option value="clear">{t("drawer.taskPriorityClear")}</option>
                    </select>
                    <button className="personal-secondary-action" disabled={!todoPriority} onClick={() => void callbacks.onPreviewAction?.({
                      actionKind: "todo.update",
                      context: {goal_id: selection.item.goalId, kind: "todo", todo_id: selection.item.todoId},
                      idempotencyKey: `workspace-todo-${selection.item.todoId}-priority-${todoPriority}-${Date.now().toString(36)}`,
                      normalizedParameters: {goal_id: selection.item.goalId, todo_id: selection.item.todoId, operation: "edit",
                        ...(todoPriority === "clear" ? {clear_priority: true} : {priority: todoPriority})},
                      summary: `${t("drawer.taskPriority")}: ${todoPriority === "clear" ? t("drawer.taskPriorityClear") : todoPriority}`,
                    })} type="button">{t("timeline.review")}</button>
                  </label>
                  <strong>{t("drawer.taskDeferUntil")}</strong>
                  <label className="personal-inline-agent-select personal-inline-resume-when">{t("drawer.taskDeferUntil")}
                    <input
                      aria-label={t("drawer.taskDeferCondition")}
                      aria-invalid={Boolean(todoResumeWhen.trim()) && !normalizedTodoResumeWhen}
                      onChange={(event) => setTodoResumeWhen(event.target.value)}
                      placeholder={t("drawer.taskDeferPlaceholder")}
                      value={todoResumeWhen}
                    />
                    <button className="personal-secondary-action" disabled={!normalizedTodoResumeWhen} onClick={() => void previewTodoTransition(selection.item, "defer", t("drawer.taskDefer"), normalizedTodoResumeWhen ?? undefined)} type="button">{t("drawer.taskDeferReview")}</button>
                    <small>{todoResumeWhen.trim() && !normalizedTodoResumeWhen ? t("drawer.taskDeferInvalid") : t("drawer.taskDeferSupported")}</small>
                  </label>
                  <div className="personal-task-management-secondary">
                    {todoTransitions.map((transition) => (
                      <button key={transition.operation} onClick={() => void previewTodoTransition(selection.item, transition.operation, t(transition.key))} type="button">{t(transition.key)}</button>
                    ))}
                  </div>
                </div>
              </details>
              <button className="personal-primary-action" onClick={() => void previewTodoTransition(selection.item, "complete", t("drawer.taskComplete"))} type="button"><Check size={17} />{t("drawer.taskComplete")}</button>
            </div> : null}
            {selection.item.done ? <div className="personal-task-completed-note"><Check size={16} /><span><strong>{t("drawer.taskCompletedTitle")}</strong><small>{t("drawer.taskCompletedNote")}</small></span></div> : null}
          </>
        ) : null}

        {selection.kind === "goal" ? (
          <>
            <section className="personal-detail-card">
              <small>{localizedGoalState(selection.item.state, locale)}</small>
              <h3>{selection.item.title}</h3>
              <p>{selection.item.agentSentence}</p>
              <dl>
                <div><dt>{t("drawer.tokens")}</dt><dd>{formatUsageValue(selection.item.usage?.tokens24h, t("drawer.usageNotMeasured"), formatTokenCount)} / {formatUsageValue(selection.item.usage?.tokens7d, t("drawer.usageNotMeasured"), formatTokenCount)}</dd></div>
                <div><dt>{t("drawer.cost")}</dt><dd>{formatUsageValue(selection.item.usage?.costUsd24h, t("drawer.usageNotMeasured"), formatCostUsd)} / {formatUsageValue(selection.item.usage?.costUsd7d, t("drawer.usageNotMeasured"), formatCostUsd)}</dd></div>
                <div><dt>{t("drawer.duration")}</dt><dd>{formatUsageValue(selection.item.usage?.durationMs24h, t("drawer.usageNotMeasured"), formatDurationMs)} / {formatUsageValue(selection.item.usage?.durationMs7d, t("drawer.usageNotMeasured"), formatDurationMs)}</dd></div>
              </dl>
            </section>
            <GoalAcceptanceObservationCard goal={selection.item} />
            {(() => {
              const notification = goalNotifications.find((row) => row.goalId === selection.item.goalId);
              const connection = larkConnections.find((row) => row.goal_id === selection.item.goalId);
              return (
                <>
                  {selection.item.repository ? (
                    <section className="personal-detail-card personal-goal-repository">
                      <div className="personal-detail-card-title"><small>{t("drawer.repository")}</small><em>{t("common.readOnly")}</em></div>
                      <h3><GitBranch size={16} />{selection.item.repository.label}</h3>
                      <dl>
                        <div><dt>{t("drawer.branch")}</dt><dd>{selection.item.repository.branch || "detached"}</dd></div>
                        <div><dt>Role</dt><dd>{t("drawer.repositoryRole")}</dd></div>
                      </dl>
                      <button className="personal-secondary-action" onClick={() => {
                        const write = navigator.clipboard?.writeText(selection.item.repository?.identity ?? "");
                        if (!write) {
                          setRepositoryCopyState("error");
                          return;
                        }
                        void write.then(() => setRepositoryCopyState("copied")).catch(() => setRepositoryCopyState("error"));
                      }} type="button"><Copy size={15} />{repositoryCopyState === "copied" ? t("drawer.copyRepositoryDone") : t("drawer.copyRepository")}</button>
                      {repositoryCopyState === "error" ? <p className="personal-copy-feedback is-error" role="status">{t("drawer.copyRepositoryError")}</p> : repositoryCopyState === "copied" ? <p className="personal-copy-feedback" role="status">{t("drawer.copyRepositorySuccess")}</p> : null}
                    </section>
                  ) : null}
                  {!readOnly ? <section className="personal-detail-card personal-goal-notification">
                  <small>{t("drawer.larkConnection")}</small>
                  {connection ? (
                    <>
                      <h3>{connection.app_label}<span className="personal-connection-status">{t("drawer.connected")}</span></h3>
                      <dl>
                        <div><dt>{t("drawer.group")}</dt><dd>{connection.chat_name}</dd></div>
                        <div><dt>{t("drawer.topic")}</dt><dd># {connection.topic_name}</dd></div>
                        <div><dt>{t("drawer.trigger")}</dt><dd>{connection.incoming_mode === "mentions" ? t("lark.someoneMentions") : t("lark.allMessages")}</dd></div>
                        <div><dt>{t("drawer.replyMode")}</dt><dd>{t("lark.topicReply")}</dd></div>
                        <div><dt>{t("drawer.autoNotify")}</dt><dd>{notification?.humanGateAutoNotifyEnabled ? t("common.on") : t("common.off")}</dd></div>
                        {notification?.lastNotifiedAt ? (
                          <div><dt>{t("drawer.lastNotification")}</dt><dd>{notification.lastNotifiedAt}</dd></div>
                        ) : null}
                      </dl>
                    </>
                  ) : (
                    <>
                      <h3>{t("drawer.larkNotConfigured")}</h3>
                      <p>{t("drawer.larkNotConfiguredDescription")}</p>
                    </>
                  )}
                  <button className="personal-secondary-action" onClick={() => callbacks.onOpenNotificationSettings?.(selection.item.goalId)} type="button"><Bell size={16} />{connection ? t("drawer.larkConfigure") : t("drawer.larkConnect")}</button>
                  </section> : <section className="personal-detail-card personal-goal-notification"><small>{t("drawer.larkConnection")}</small><h3>{t("drawer.remoteDetailsUnavailable")}</h3><p>{t("drawer.remoteDetailsDescription")}</p></section>}
                </>
              );
            })()}
            <section className="personal-detail-card personal-goal-session">
              <small>{t("drawer.runDetails")}</small>
              {selectedGoalRun?.sessionId ? (
                <>
                  <h3>{localizedSessionStatus(selectedGoalRun.sessionStatus ?? selectedGoalRun.status, t)}</h3>
                  <p>{selectedGoalRun.title}</p>
                  {callbacks.onOpenRunSession ? <button className="personal-primary-action" onClick={() => void callbacks.onOpenRunSession?.(selectedGoalRun)} type="button"><Play size={16} />{t("drawer.runLatest")}</button> : null}
                </>
              ) : (
                <>
                  <h3>{t("drawer.noRun")}</h3>
                  <p>{t("drawer.noRunDescription")}</p>
                </>
              )}
            </section>
            {!readOnly ? <div className="personal-drawer-action-grid">
              <button className="personal-secondary-action" onClick={() => callbacks.onRequestScheduleConfig?.("heartbeat", selection.item.goalId)} type="button"><Radio size={16} />{t("drawer.setupHeartbeat")}</button>
              <button className="personal-secondary-action" onClick={() => callbacks.onRequestScheduleConfig?.("monitor", selection.item.goalId)} type="button"><CalendarClock size={16} />{t("drawer.scheduleAdd")}</button>
            </div> : null}
            {selection.item.subagentExecution ? <section className="personal-detail-card personal-goal-subagents">
              <div className="personal-subagent-heading">
                <div>
                  <small>{t("drawer.subagentLabel")}</small>
                  <h3><Bot size={16} />{t("drawer.subagentTitle")}</h3>
                </div>
                <button
                  aria-checked={currentSubagentConfiguration.enabled}
                  aria-label={t(subagentPreview && subagentMutationState === "ready"
                    ? "drawer.subagentPending"
                    : currentSubagentConfiguration.enabled ? "drawer.subagentDisable" : "drawer.subagentEnable")}
                  className="personal-subagent-switch"
                  data-pending={subagentPreview && subagentMutationState === "ready" ? "true" : undefined}
                  disabled={readOnly
                    || subagentBusy
                    || Boolean(subagentPreview)
                    || !callbacks.onPreviewGoalSubagentConfiguration}
                  onClick={() => void previewGoalSubagentConfiguration(!currentSubagentConfiguration.enabled)}
                  role="switch"
                  type="button"
                >
                  <span />
                  {t(subagentPreview && subagentMutationState === "ready"
                    ? "drawer.subagentPending"
                    : currentSubagentConfiguration.enabled ? "common.on" : "common.off")}
                </button>
              </div>
              <p>{t("drawer.subagentDescription")}</p>
              {subagentPreview && subagentMutationState === "ready" ? (
                <div className="personal-subagent-preview">
                  <strong>{t(subagentPreview.enabled ? "drawer.subagentConfirmEnable" : "drawer.subagentConfirmDisable")}</strong>
                  <p>{subagentPreview.enabled
                    ? t("drawer.subagentPreviewSummary", {
                        count: subagentPreview.maxChildren,
                        domains: subagentPreview.allowedDomains.join(" · ") || t("drawer.subagentDomainsUnrestricted"),
                      })
                    : t("drawer.subagentDisableSummary")}</p>
                  {subagentPreview.modelConfig !== undefined ? <p>{t("drawer.subagentModel")}: {subagentPreview.modelConfig?.model || t("drawer.subagentModelDefault")} · {subagentPreview.modelConfig?.reasoning_effort || t("drawer.subagentModelDefault")}</p> : null}
                  <p>{t("drawer.subagentExecutionConfig")}: {subagentPreview.executionConfig || t("drawer.subagentExecutionConfigNone")}</p>
                  {subagentPreview.enabled && subagentPreview.codexHostCapacity ? <p>{t(
                    subagentPreview.codexHostCapacity.writeRequired
                      ? "drawer.subagentHostCapacityRaise"
                      : "drawer.subagentHostCapacityReady",
                    {
                      configured: subagentPreview.codexHostCapacity.configuredChildren ?? t("drawer.subagentHostCapacityImplicit"),
                      required: subagentPreview.codexHostCapacity.requiredChildren,
                    },
                  )}</p> : null}
                  <div>
                    <button className="personal-primary-action" onClick={() => void applyGoalSubagentConfiguration()} type="button">{t("common.confirm")}</button>
                    <button className="personal-secondary-action" onClick={resetSubagentDraft} type="button">{t("common.cancel")}</button>
                  </div>
                </div>
              ) : null}
              {subagentFeedback ? (
                <p className={`personal-subagent-feedback is-${subagentMutationState}`} role="status">
                  {subagentMutationState === "previewing" || subagentMutationState === "applying" ? <RotateCcw className="personal-spin" size={13} /> : null}
                  {subagentFeedback}
                </p>
              ) : null}
              <dl>
                <div><dt>{t("drawer.subagentModel")}</dt><dd>{currentSubagentConfiguration.modelConfig?.model || t("drawer.subagentModelDefault")}</dd></div>
                <div><dt>{t("drawer.subagentEffort")}</dt><dd>{currentSubagentConfiguration.modelConfig?.reasoning_effort || t("drawer.subagentModelDefault")}</dd></div>
                <div><dt>{t("drawer.subagentCurrentBoundary")}</dt><dd>{currentSubagentConfiguration.allowedDomains.join(" · ") || t("drawer.subagentDomainsUnrestricted")}</dd></div>
                <div><dt>{t("drawer.subagentChildLimit")}</dt><dd>{currentSubagentConfiguration.maxChildren || 0}</dd></div>
                <div><dt>{t("drawer.subagentExecutionConfig")}</dt><dd>{currentSubagentConfiguration.executionConfig || t("drawer.subagentExecutionConfigNone")}</dd></div>
              </dl>
              {readOnly ? (
                <p className="personal-subagent-read-only">{t("drawer.subagentRemoteReadOnly")}</p>
              ) : (
                <div className="personal-subagent-fields">
                  <fieldset className="personal-subagent-domain-picker" disabled={subagentBusy}>
                    <legend>{t("drawer.subagentDomains")}</legend>
                    {subagentDomainOptions.length > 0 ? (
                      <div className="personal-subagent-domain-options">
                        {subagentDomainOptions.map((option) => {
                          const selected = subagentAllowedDomains.includes(option.value);
                          return (
                            <label className={`personal-subagent-domain-option${selected ? " is-selected" : ""}`} key={option.value}>
                              <input
                                aria-label={option.value}
                                checked={selected}
                                onChange={(event) => toggleSubagentDomain(option.value, event.target.checked)}
                                type="checkbox"
                              />
                              <span>
                                <strong>{option.value}</strong>
                                <small>{t("drawer.subagentDomainTodoCount", { count: option.matchingTodoCount })}</small>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="personal-subagent-domain-empty">{t("drawer.subagentDomainsEmpty")}</p>
                    )}
                    <small>{t("drawer.subagentDomainsHint")}</small>
                  </fieldset>
                  <label>
                    <span>{t("drawer.subagentModel")}</span>
                    <input aria-label={t("drawer.subagentModel")} disabled={subagentBusy} value={subagentModel} placeholder="gpt-5.6-luna" onChange={(event) => { setSubagentModel(event.target.value); setSubagentPreview(null); setSubagentMutationState("idle"); setSubagentFeedback(null); }} />
                  </label>
                  <label>
                    <span>{t("drawer.subagentEffort")}</span>
                    <select aria-label={t("drawer.subagentEffort")} disabled={subagentBusy} value={subagentEffort} onChange={(event) => { setSubagentEffort(event.target.value); setSubagentPreview(null); setSubagentMutationState("idle"); setSubagentFeedback(null); }}>
                      <option value="">{t("drawer.subagentModelDefault")}</option>
                      {["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"].map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  </label>
                  <button className="personal-secondary-action" disabled={subagentBusy} type="button" onClick={() => { setSubagentModel("gpt-5.6-luna"); setSubagentEffort("max"); setSubagentPreview(null); setSubagentMutationState("idle"); setSubagentFeedback(null); }}>{t("drawer.subagentLunaPreset")}</button>
                  <button className="personal-secondary-action" disabled={subagentBusy} type="button" onClick={() => { setSubagentModel(""); setSubagentEffort(""); setSubagentPreview(null); setSubagentMutationState("idle"); setSubagentFeedback(null); }}>{t("drawer.subagentClearModel")}</button>
                  <p>{t("drawer.subagentModelHint")}</p>
                  <label>
                    <span>{t("drawer.subagentExecutionConfig")}</span>
                    <input
                      aria-label={t("drawer.subagentExecutionConfig")}
                      disabled={subagentBusy}
                      onChange={(event) => { setSubagentExecutionConfig(event.target.value); setSubagentPreview(null); setSubagentMutationState("idle"); setSubagentFeedback(null); }}
                      placeholder=".loopx/config/delegations.json"
                      value={subagentExecutionConfig}
                    />
                  </label>
                  <p>{t("drawer.subagentExecutionConfigHint")}</p>
                  <label className="personal-subagent-limit-field">
                    <span>{t("drawer.subagentMaxChildren")}</span>
                    <select
                      aria-label={t("drawer.subagentMaxChildren")}
                      disabled={subagentBusy}
                      onChange={(event) => {
                        setSubagentMaxChildren(Number(event.target.value));
                        setSubagentPreview(null);
                        setSubagentMutationState("idle");
                        setSubagentFeedback(null);
                      }}
                      value={subagentMaxChildren}
                    >
                      {subagentChildLimits.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  </label>
                  <button
                      className="personal-secondary-action"
                      disabled={subagentBusy}
                      onClick={() => void previewGoalSubagentConfiguration(currentSubagentConfiguration.enabled, true)}
                      type="button"
                    >{t("drawer.subagentPreviewBoundary")}</button>
                </div>
              )}
            </section> : null}
          </>
        ) : null}

        {selection.kind === "run" ? (
          <>
            <div aria-label={t("drawer.runView")} className="personal-run-drawer-tabs" role="tablist">
              <button aria-selected={runDrawerTab === "record"} onClick={() => setRunDrawerTab("record")} role="tab" type="button">{t("drawer.executionRecordAndResult")}</button>
              <button aria-selected={runDrawerTab === "details"} onClick={() => setRunDrawerTab("details")} role="tab" type="button">{t("drawer.detailsAndActions")}</button>
            </div>
            {runDrawerTab === "record" ? (
              <>
                <section className="personal-detail-card personal-session-summary">
                  <small>{selection.item.agentLabel} · {localizedSessionStatus(selection.item.sessionStatus ?? selection.item.status, t)}</small>
                  <h3>{selection.item.title}</h3>
                  <p>{selection.item.latestActivity}</p>
                  <dl>
                    <div><dt>Goal</dt><dd>{selection.item.goalTitle}</dd></div>
                    <div><dt>{t("drawer.progress")}</dt><dd>{selection.item.completedSteps}/{selection.item.totalSteps}</dd></div>
                  </dl>
                </section>
                <section aria-label={t("drawer.executionRecord")} className="personal-session-message-record">
                  <h3>{t("drawer.executionRecord")}</h3>
                  {selection.item.sessionMessages?.length ? (
                    <ol>{selection.item.sessionMessages.map((message) => (
                      <li className={`is-${message.role}`} key={message.messageId}>
                        <i />
                        <div>
                          <header><strong>{message.role === "user" ? t("drawer.runRoleUser") : message.role === "assistant" ? t("drawer.runRoleAssistant") : t("drawer.runRoleSystem")}</strong>{message.createdAt ? <time>{new Date(message.createdAt).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", hour12: false })}</time> : null}</header>
                          <p>{message.text}</p>
                        </div>
                      </li>
                    ))}</ol>
                  ) : (
                    <p className="personal-session-empty">{hasProjectedRunActivity
                      ? t("drawer.runRecordProjected", {
                          completed: selection.item.completedSteps,
                          outputs: selection.item.outputs?.length ? t("drawer.runRecordProjectedOutputs", { count: selection.item.outputs.length }) : "",
                          total: selection.item.totalSteps,
                        })
                      : t("drawer.runRecordEmpty")}</p>
                  )}
                  {selection.item.status === "running" ? <div className="personal-session-active-step"><i /><span><strong>{t("drawer.analysis")}</strong><small>{t("drawer.agentWorking")}</small></span></div> : null}
                </section>
                {selection.item.outputs?.length ? (
                  <section className="personal-execution-history" aria-labelledby="personal-run-outputs-title">
                    <h3 id="personal-run-outputs-title">{t("drawer.outputs")}</h3>
                    <ol>{selection.item.outputs.map((output) => <li key={output.outputId}><span><strong>{output.title}</strong><small>{output.createdAt ?? output.kind ?? t("files.emptySummary")}</small></span></li>)}</ol>
                  </section>
                ) : null}
              </>
            ) : (
              <>
                <section className="personal-detail-card">
                  <small>{selection.item.agentLabel} · {localizedSessionStatus(selection.item.status, t)}</small>
                  <h3>{selection.item.title}</h3>
                  <p>{selection.item.latestActivity}</p>
                  <dl>
                    <div><dt>Goal</dt><dd>{selection.item.goalTitle}</dd></div>
                    <div><dt>{t("drawer.progress")}</dt><dd>{selection.item.completedSteps}/{selection.item.totalSteps}</dd></div>
                    <div><dt>{t("drawer.sessionStatus")}</dt><dd>{localizedSessionStatus(selection.item.sessionStatus ?? selection.item.status, t)}</dd></div>
                    <div><dt>{t("drawer.sessionRecoverable")}</dt><dd>{selection.item.resumable === false ? t("drawer.resumeNo") : t("drawer.resumeYes")}</dd></div>
                  </dl>
                </section>
                {selection.item.sessionStatus === "resume_failed" && !readOnly ? (
                  <section className="personal-recovery-panel" aria-label={t("drawer.recoveryFailed")}>
                    <strong>{t("drawer.recoveryFailed")}</strong>
                    <p>{t("drawer.recoveryDescription")}</p>
                    <button className="personal-primary-action" onClick={() => void callbacks.onRetryResumeRun?.(selection.item)} type="button"><RotateCcw size={16} />{t("drawer.recoveryRetry")}</button>
                    <button className="personal-secondary-action" onClick={() => void callbacks.onStartNewRunSession?.(selection.item)} type="button"><Play size={16} />{t("drawer.recoveryNewSession")}</button>
                  </section>
                ) : null}
                {!readOnly ? <section className="personal-correction-panel">
                  <header><span><Bot size={16} />{t("drawer.correctionLabel", { agent: selection.item.agentLabel })}</span></header>
                  <p>{t("drawer.correctionDescription")}</p>
                  <div className="personal-correction-composer">
                    <textarea
                      aria-label={t("drawer.correctionTextarea", { agent: selection.item.agentLabel, goal: selection.item.goalTitle, run: selection.item.title })}
                      onChange={(event) => setCorrection(event.target.value)}
                      placeholder={t("drawer.correctionPlaceholder")}
                      rows={3}
                      value={correction}
                    />
                    <button aria-label={t("drawer.correctionSend")} disabled={!correction.trim()} onClick={() => void sendCorrection()} type="button"><Send size={16} /></button>
                  </div>
                </section> : null}
                {!readOnly ? <details className="personal-compact-menu personal-run-more">
                  <summary><MoreHorizontal size={17} />{t("drawer.moreRunActions")}</summary>
                  <div>
                    <button disabled={!selection.item.canInterrupt} onClick={() => void callbacks.onInterruptRun?.(selection.item)} type="button"><Pause size={16} />{t("drawer.runInterrupt")}</button>
                    <button disabled={selection.item.resumable === false} onClick={() => void callbacks.onRetryResumeRun?.(selection.item)} type="button"><RotateCcw size={16} />{t("drawer.recoveryRetry")}</button>
                    <button onClick={() => void callbacks.onStartNewRunSession?.(selection.item)} type="button"><Play size={16} />{t("drawer.runNewSession")}</button>
                    <button onClick={() => void callbacks.onCloseRunSession?.(selection.item)} type="button"><Square size={16} />{t("drawer.runCloseSession")}</button>
                  </div>
                </details> : null}
              </>
            )}
          </>
        ) : null}

        {selection.kind === "output" ? (
          <>
            <section className="personal-detail-card">
              <small>{selection.item.kind ?? "output"}</small>
              <h3>{selection.item.title}</h3>
              <p>{selection.item.summary ?? t("drawer.outputRecorded")}</p>
              <dl>
                <div><dt>Goal</dt><dd>{selection.item.goalTitle ?? selection.item.goalId}</dd></div>
                <div><dt>{t("drawer.outputTodo")}</dt><dd>{selection.item.todoId ?? t("drawer.notLinked")}</dd></div>
                <div><dt>{t("drawer.outputRun")}</dt><dd>{selection.item.runId ?? t("drawer.notLinked")}</dd></div>
                <div><dt>Agent</dt><dd>{selection.item.agentLabel ?? selection.item.agentId ?? "LoopX"}</dd></div>
              </dl>
            </section>
            {selection.item.report ? (
              <section className="personal-report-detail" data-testid="personal-periodic-report-detail">
                <header>
                  <span><strong>+{selection.item.report.addedCount}</strong>{t("files.reportAdded")}</span>
                  <span><strong>{selection.item.report.changedCount}</strong>{t("files.reportChanged")}</span>
                </header>
                <p>{selection.item.report.periodStartAt} → {selection.item.report.periodEndAt}</p>
                <ol>
                  {selection.item.report.items.map((item) => (
                    <li data-change-kind={item.changeKind} key={item.sourceRef}>
                      <small>{item.changeKind} · {item.status}</small>
                      <strong>{item.title}</strong>
                      <p>{item.summary}</p>
                    </li>
                  ))}
                </ol>
                <footer>
                  <span>{t("files.reportPublication")}: {selection.item.report.publicationId}</span>
                  <span>{t("files.reportGeneration")}: {selection.item.report.generationId}</span>
                </footer>
              </section>
            ) : null}
            {selection.item.safePreview ? <pre aria-label={t("drawer.outputSafePreview")} className="personal-safe-preview">{selection.item.safePreview}</pre> : <p className="personal-preview-unavailable">{t("drawer.previewUnavailable")}</p>}
            <div className="personal-drawer-action-grid">
              <button className="personal-primary-action" disabled={!callbacks.onOpenOutput} onClick={() => { callbacks.onOpenOutput?.(selection.item); onClose(); }} type="button"><ExternalLink size={16} />{t("files.openConversation")}</button>
              <button className="personal-secondary-action" disabled={!callbacks.onExportOutput} onClick={() => void callbacks.onExportOutput?.(selection.item)} type="button"><Download size={16} />{t("files.exportSummary")}</button>
            </div>
          </>
        ) : null}

        {selection.kind === "proposal" ? (
          <>
            {selection.item.actionKind === "team.plan" && selection.item.status === "applied" ? <TeamPlanResult proposal={selection.item} t={t} /> : <section className="personal-proposal-card">
              <small>{selection.item.actionKind} · {selection.item.status}</small>
              <h3>{selection.item.title}</h3>
              {selection.item.impact ? <p>{selection.item.impact}</p> : null}
              {selection.item.reviewPlan && !selection.item.reviewPlan.retryOriginal && selection.item.actionKind !== "team.plan" ? <p className="personal-proposal-explainer" data-action-review={selection.item.reviewPlan.interaction}>{selection.item.actionKind === "operation.execute" && selection.item.status === "gated"
                ? t("actionReview.operation_group_confirmation")
                : selection.item.actionKind === "operation.execute" && selection.item.reviewPlan.reason === "readback_unverified"
                  ? t("actionReview.operation_result_delivery_pending")
                  : t(`actionReview.${selection.item.reviewPlan.reason}`)}</p> : null}
              {selection.item.status === "ready" && selection.item.actionKind !== "team.plan" ? <p className="personal-proposal-explainer">{t("drawer.proposalExplainer")}</p> : null}
              <dl>{selection.item.fields.map((field) => <div key={field.key}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl>
            </section>}
            {selection.item.status === "applied" && selection.item.actionKind !== "team.plan" ? <p className={`personal-proposal-state ${selection.item.actionKind === "operation.execute" && selection.item.reviewPlan?.reason === "readback_unverified" ? "is-gated" : "is-applied"}`}><Check size={16} />{selection.item.actionKind === "operation.execute" ? selection.item.primaryLabel : t("drawer.proposalApplied")}</p> : null}
            {selection.item.status === "applied" && selection.item.actionKind !== "operation.execute" && selection.item.goalId ? <button className="personal-primary-action" onClick={() => { const goalId = selection.item.goalId!; onClose(); void callbacks.onOpenGoal?.(goalId); }} type="button"><ExternalLink size={16} />{selection.item.actionKind === "goal.create" ? t("drawer.proposalEnterGoal") : t(selection.item.actionKind === "team.plan" ? "proposal.teamPlan.openGoal" : "drawer.proposalViewGoal")}</button> : null}
            {selection.item.status === "stale" ? <p className="personal-proposal-state is-stale">{t("drawer.proposalStale")}</p> : null}
            {selection.item.status === "error" && !selection.item.reviewPlan?.retryOriginal ? <div className="personal-proposal-state is-error"><span>{selection.item.reviewPlan?.reason === "readback_unverified" ? t("actionReview.readback_unverified") : t("drawer.proposalApplyFailed")}</span>{selection.item.errorMessage ? <small>{selection.item.errorMessage}</small> : null}<small>{t(selection.item.actionKind === "team.plan" ? "proposal.teamPlan.retryHint" : "drawer.proposalApplyFailedHint")}</small></div> : null}
            {selection.item.status === "rejected" ? <p className="personal-proposal-state is-error">{t("drawer.proposalRejected")}</p> : null}
            {selection.item.status === "deferred" ? <p className="personal-proposal-state is-gated">{t("drawer.proposalDeferred")}</p> : null}
            {selection.item.status === "gated" ? <div className="personal-proposal-state is-gated"><span><strong>{selection.item.actionKind === "operation.execute" ? selection.item.primaryLabel : selection.item.workspaceCandidates?.length ? selection.item.title : t("drawer.gateRequiresHost")}</strong>{selection.item.actionKind === "operation.execute" || selection.item.workspaceCandidates?.length ? selection.item.impact : t("drawer.gateRequiresHostDescription")}</span>{selection.item.gate?.nextAction ? <small>{selection.item.gate.nextAction}</small> : null}</div> : null}
            {selection.item.status === "gated" && selection.item.actionKind === "gate.resolve" ? (() => {
              const fieldValue = (key: string) => selection.item.fields.find((field) => field.key === key)?.value;
              const gateGoalId = fieldValue("goal_id");
              const gateTodoId = fieldValue("todo_id");
              if (!gateGoalId || !gateTodoId) return null;
              return (
                <section className="personal-detail-card personal-gate-cli-hint">
                  <small>{t("drawer.gateApproveHint")}</small>
                  <code>loopx todo complete --goal-id {gateGoalId} --todo-id {gateTodoId} --decision-outcome approve</code>
                  <small>{t("drawer.gateRejectHint")}</small>
                </section>
              );
            })() : null}
            {!readOnly && selection.item.workspaceCandidates?.length ? <div className="personal-workspace-candidates" aria-label={t("drawer.workspaceCandidates")}>{selection.item.workspaceCandidates.map((candidate) => <button key={candidate.workspaceRef} onClick={() => void callbacks.onSelectWorkspaceCandidate?.(selection.item, candidate.workspaceRef)} type="button"><strong>{candidate.label}</strong><small>{candidate.workspaceRef}</small></button>)}</div> : null}
            {!readOnly && selection.item.actionKind !== "operation.execute" && selection.item.status === "error" ? <button className="personal-primary-action" onClick={() => void ((selection.item.actionKind === "team.plan" || selection.item.reviewPlan?.retryOriginal) ? callbacks.onApplyProposal?.(selection.item) : callbacks.onTransitionProposal?.(selection.item, "regenerate"))} type="button"><RotateCcw size={17} />{t(selection.item.reviewPlan?.retryOriginal ? "drawer.retryOriginal" : selection.item.actionKind === "team.plan" ? "proposal.teamPlan.retry" : "drawer.proposalRegenerate")}</button> : !readOnly && selection.item.actionKind !== "operation.execute" && selection.item.status !== "gated" && !(selection.item.actionKind === "team.plan" && selection.item.status === "applied") ? <button className="personal-primary-action" disabled={!['ready', 'deferred'].includes(selection.item.status) || selection.item.reviewPlan?.canApply === false} onClick={() => void callbacks.onApplyProposal?.(selection.item)} type="button"><Check size={17} />{selection.item.status === "applying" ? t("drawer.applying") : selection.item.primaryLabel ?? t("drawer.apply")}</button> : null}
            {!readOnly && selection.item.actionKind !== "operation.execute" && (["stale", "gated", "rejected"].includes(selection.item.status) || (selection.item.status === "ready" && selection.item.reviewPlan?.canApply === false)) ? <button className="personal-secondary-action" onClick={() => void callbacks.onTransitionProposal?.(selection.item, "regenerate")} type="button"><RotateCcw size={16} />{t("drawer.proposalRecheck")}</button> : null}
            {!readOnly && selection.item.actionKind !== "operation.execute" && ["ready", "gated"].includes(selection.item.status) ? <div className="personal-drawer-action-grid"><button className="personal-secondary-action" onClick={() => void callbacks.onTransitionProposal?.(selection.item, "defer")} type="button">{t("drawer.proposalDefer")}</button><button className="personal-secondary-action" onClick={() => void callbacks.onTransitionProposal?.(selection.item, "reject")} type="button">{t("drawer.decisionReject")}</button></div> : null}
            {!["applied", "applying"].includes(selection.item.status) ? <button className="personal-secondary-action" onClick={onClose} type="button">{t("drawer.proposalClose")}</button> : null}
          </>
        ) : null}

        {selection.kind === "schedule" ? (
          <>
            <section className="personal-detail-card">
              <small>{selection.item.scheduleKind === "heartbeat" ? "Goal Heartbeat" : "continuous_monitor"} · {selection.item.status ?? "active"}</small>
              <h3>{selection.item.label}</h3>
              <p>{selection.item.target ?? selection.item.schedule ?? t("drawer.scheduleDefaultTarget")}</p>
              <dl>
                <div><dt>{t("drawer.scheduleTimezone")}</dt><dd>{selection.item.timezone ?? t("drawer.scheduleLocalTimezone")}</dd></div>
                <div><dt>{t("drawer.scheduleNext")}</dt><dd>{selection.item.nextRunAt ?? t("drawer.schedulePending")}</dd></div>
                <div><dt>{t("drawer.scheduleLast")}</dt><dd>{selection.item.previousRunAt ?? t("drawer.scheduleNeverRun")}</dd></div>
                <div><dt>{t("drawer.scheduleNotification")}</dt><dd>{selection.item.notificationRule ?? t("drawer.scheduleDefaultNotification")}</dd></div>
                <div><dt>{t("drawer.scheduleStopCondition")}</dt><dd>{selection.item.stopCondition ?? t("drawer.scheduleDefaultStop")}</dd></div>
              </dl>
            </section>
            {!readOnly && selection.item.scheduleKind === "monitor" ? <button className="personal-primary-action" onClick={() => void callbacks.onUpdateSchedule?.(selection.item, "run_now")} type="button"><Play size={16} />{t("drawer.scheduleRunNow")}</button> : null}
            {!readOnly ? <div className="personal-drawer-action-grid">
              <button className="personal-secondary-action" onClick={() => void callbacks.onUpdateSchedule?.(selection.item, selection.item.status === "paused" ? "resume" : "pause")} type="button">{selection.item.status === "paused" ? <Play size={16} /> : <Pause size={16} />}{selection.item.status === "paused" ? t("drawer.scheduleResume") : t("drawer.schedulePause")}</button>
              <button className="personal-secondary-action" onClick={() => void callbacks.onUpdateSchedule?.(selection.item, "edit")} type="button"><CalendarClock size={16} />{t("drawer.scheduleEdit")}</button>
            </div> : null}
            {!readOnly ? <button className="personal-danger-action" onClick={() => void callbacks.onUpdateSchedule?.(selection.item, "stop")} type="button"><Square size={16} />{t("drawer.scheduleStop", { kind: selection.item.scheduleKind === "heartbeat" ? " Heartbeat" : t("drawer.titleSchedule") })}</button> : null}
            <section className="personal-execution-history" aria-labelledby="personal-execution-history-title">
              <h3 id="personal-execution-history-title">{t("drawer.executionHistory")}</h3>
              {selection.item.executionHistory?.length ? (
                <ol>{selection.item.executionHistory.map((entry, index) => <li key={`${entry.timestamp}:${entry.runId ?? index}`}><span><strong>{entry.label}</strong><small>{entry.timestamp}</small></span><em className={`is-${entry.status}`}>{entry.status}</em></li>)}</ol>
              ) : <p>{t("drawer.noExecutionHistory")}</p>}
            </section>
          </>
        ) : null}

        {selection.kind === "run" || selection.kind === "proposal" || selection.kind === "schedule" ? (
          <>
            <button aria-expanded={diagnosticsOpen} className="personal-diagnostics-trigger" onClick={() => setDiagnosticsOpen((value) => !value)} type="button">
              <span>{t("drawer.advancedDiagnostics")}</span><ChevronDown className={diagnosticsOpen ? "is-open" : ""} size={16} />
            </button>
            {diagnosticsOpen ? (
              <div className="personal-diagnostics">
                <code>goal_id: {goalId}</code><code>kind: {selection.kind}</code>
                {selection.kind === "run" ? <><code>session_id: {selection.item.sessionId ?? t("drawer.notLinked")}</code><code>turn_id: {selection.item.turnId ?? t("common.none")}</code><code>adapter: {selection.item.agentId}</code><code>status: {selection.item.sessionStatus ?? selection.item.status}</code></> : selection.kind === "proposal" ? <><code>action: {selection.item.actionKind}</code><code>status: {selection.item.status}</code></> : <><code>schedule_id: {selection.item.scheduleId}</code><code>status: {selection.item.status ?? "active"}</code></>}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
