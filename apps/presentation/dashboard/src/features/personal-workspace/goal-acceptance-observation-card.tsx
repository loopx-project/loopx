import type { WorkspaceGoal } from "./personal-workspace-model";
import { useWorkspaceI18n } from "./i18n";
import { ContextHelp } from "./context-help";

export function GoalAcceptanceObservationCard({ goal, overview = false }: { goal: WorkspaceGoal; overview?: boolean }) {
  const { t, locale } = useWorkspaceI18n();
  const checkLabels = locale === "zh-CN" ? {
    checkpoint_satisfied: "检查点满足", checkpoint_fresh: "检查点有效",
    path_outcome_valid: "路径决策有效", evidence_refs_present: "证据引用齐全",
    final_outcome_claim_present: "最终成果声明齐全", no_reported_outcome_gap: "无已报告成果缺口",
  } : {
    checkpoint_satisfied: "Checkpoint satisfied", checkpoint_fresh: "Checkpoint current",
    path_outcome_valid: "Valid path decision", evidence_refs_present: "Evidence refs present",
    final_outcome_claim_present: "Final outcome claim present", no_reported_outcome_gap: "No reported outcome gap",
  };
  const labels = {
    connected: t("acceptance.connected"), mapped: t("acceptance.mapped"), refreshed: t("acceptance.refreshed"),
    adapter_inspected: t("acceptance.inspected"), run_recorded: t("acceptance.recorded"), reward_judged: t("acceptance.judged"),
    operator_approved: t("acceptance.approved"), controller_ready: t("acceptance.ready"),
    attention_queue: t("acceptance.attentionSource"), agent_vision: t("acceptance.visionSource"),
    todo_projection: t("acceptance.todoSource"), current_run: t("acceptance.runSource"),
  };
  const label = (key: string) => labels[key as keyof typeof labels] ?? t("acceptance.unknown");
  const projection = goal.acceptanceObservation;
  const unavailable = goal.loadState || !projection || projection.goal_id !== goal.goalId || projection.coverage === "unavailable";
  const content = <section className="personal-detail-card personal-goal-acceptance" aria-label={t("acceptance.title")}>
    {!overview ? <div className="personal-detail-card-title"><h3>{t("acceptance.title")}</h3><em>{t("common.readOnly")}</em></div> : null}
    <div className="personal-acceptance-coverage"><p role="status">{t(unavailable ? "acceptance.unavailable" : "acceptance.partialShort")}</p>
      {!unavailable ? <ContextHelp label={t("acceptance.title")}><p>{t("acceptance.partial")}</p></ContextHelp> : null}</div>
    {!unavailable && projection ? <>
      {projection.missing_sources.length ? <p>{t("acceptance.missing")} {projection.missing_sources.map(label).join(", ")}</p> : null}
      {projection.truncated ? <p>{t("acceptance.truncated")}</p> : null}
      <div className="personal-acceptance-next"><h4>{t("acceptance.next")}</h4><p>{projection.next_action ?? t("acceptance.unknown")}</p></div>
      <h4>{t("acceptance.gaps")}</h4>
      {projection.acceptance_gaps.length ? projection.acceptance_gaps.map((gap, index) => <div className="personal-acceptance-observation" key={`${gap.kind}:${gap.owner}:${index}`}>
        <p>{gap.reason ?? t("acceptance.reasonUnknown")}</p>
        {gap.resolution_hint ? <p>{gap.resolution_hint}</p> : null}
        <dl><div><dt>{t("acceptance.required")}</dt><dd>{gap.evidence_required ?? t("acceptance.unknown")}</dd></div></dl>
        {gap.component_checks && Object.values(gap.component_checks).some(passed => !passed) ? <p className="personal-acceptance-check-failure">{t("acceptance.failedChecks", { count: Object.values(gap.component_checks).filter(passed => !passed).length })}</p> : null}
        <details><summary>{t("acceptance.details")}</summary>
          <dl><div><dt>{t("common.owner")}</dt><dd>{gap.owner ?? t("acceptance.unknown")}</dd></div>
            <div><dt>{t("acceptance.observed")}</dt><dd>{gap.observed_at ?? t("acceptance.unknown")}</dd></div></dl>
          {gap.component_checks ? <div>{Object.entries(gap.component_checks).map(([key, passed]) =>
            <p key={key}>{checkLabels[key as keyof typeof checkLabels]}: {locale === "zh-CN" ? (passed ? "通过" : "未通过") : (passed ? "Passed" : "Failed")}</p>
          )}</div> : null}
        </details>
      </div>) : <p>{t("acceptance.noGaps")}</p>}
      <h4>{t("acceptance.guards")}</h4>
      {projection.guards.length ? projection.guards.map((guard, index) => <div className="personal-acceptance-observation" key={`${guard.todo_id}:${index}`}>
        <p>{guard.reason ?? t("acceptance.reasonUnknown")}</p>
        <dl><div><dt>{t("acceptance.required")}</dt><dd>{guard.evidence_required ?? t("acceptance.unknown")}</dd></div>
          <div><dt>{t("acceptance.scope")}</dt><dd>{guard.decision_scope ?? t("acceptance.unknown")}</dd></div></dl>
        <details><summary>{t("acceptance.details")}</summary>
          <dl><div><dt>{t("common.owner")}</dt><dd>{guard.owner ?? t("acceptance.unknown")}</dd></div>
            {guard.blocks_agent ? <div><dt>{t("common.agent")}</dt><dd>{guard.blocks_agent}</dd></div> : null}
            {guard.todo_id ? <div><dt>{t("common.task")}</dt><dd>{guard.todo_id}</dd></div> : null}</dl>
        </details>
      </div>) : <p>{t("acceptance.noGuards")}</p>}
      <details><summary>{t("acceptance.historical_progress")} · {projection.historical_progress.length}</summary>
        <p>{t("acceptance.historical")}</p>
        {projection.historical_progress.map((observation) => <p key={observation.kind}><strong>{label(observation.kind)}</strong> · {observation.observed_at ?? t("acceptance.unknown")} {observation.evidence_refs.join(", ")}</p>)}
      </details>
    </> : null}
  </section>;
  if (!overview) return content;
  const zh = locale === "zh-CN";
  return <details className="goal-overview-evidence goal-observation-details" key={goal.goalId}>
    <summary><span>{t("acceptance.title")}</span><small>{unavailable ? t("acceptance.unavailable")
      : `${projection!.acceptance_gaps.length} ${zh ? "项缺口" : "gaps"} · ${projection!.guards.length} ${zh ? "项限制" : "guards"} · ${zh ? "仅部分观测" : "Partial observations"}`}</small></summary>
    {content}
  </details>;
}
