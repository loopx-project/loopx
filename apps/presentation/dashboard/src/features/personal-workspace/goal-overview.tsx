import { ArrowRight, CircleCheck, CircleDashed, CircleX, Hand, Info } from "lucide-react";
import { DeliveryReview } from "./delivery-review";
import { relativeTime } from "./goal-activity-view";
import { goalAcceptanceContractCopy } from "./delivery-review-copy";
import { useGoalWorkspaceDigest } from "./goal-masthead";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import type { WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceGoalTab, WorkspaceModel, WorkspaceTimelineItem } from "./personal-workspace-model";
import { useWorkspaceI18n } from "./i18n";
import "./goal-overview.css";

export const goalOverviewAttentionId = "goal-overview-attention";

type CriterionState = "verified" | "failing" | "unverified";

/** Owner-recorded results for the current contract revision; anything else is not yet verified. */
function criterionStates(contract: Extract<NonNullable<NonNullable<WorkspaceGoal["acceptanceObservation"]>["goal_acceptance_contract"]>, { enabled: true }>) {
  const verification = contract.verification?.contract_revision === contract.revision ? contract.verification : null;
  const results = new Map((verification?.results ?? []).map((result) => [result.criterion_id, result.passed]));
  return contract.criteria.map((criterion) => ({
    ...criterion,
    state: (results.has(criterion.id) ? (results.get(criterion.id) ? "verified" : "failing") : "unverified") as CriterionState,
  }));
}

export function GoalOverview({ active, goal, items, userTodos, readOnly, onOpenDetails, onSelect, onView }: {
  active: boolean; goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  readOnly: boolean; onOpenDetails: () => void; onSelect: (selection: WorkspaceDrawerSelection) => void; onView: (view: WorkspaceGoalTab) => void;
}) {
  const { t, locale } = useWorkspaceI18n();
  const copy = locale === "zh-CN" ? {
    direction: "方向", current: "当前推进", agentSummary: "Agent 的进展摘要", attention: "需要你", details: "Goal 信息",
    tasks: "查看任务", execution: "执行记录", optional: "{count} 项可选",
    noTarget: "尚未声明可衡量目标。目标通过 Goal 的验收配置声明。",
    verified: "{passed}/{total} 项条件已核验", criterion: { verified: "已核验", failing: "未通过", unverified: "尚未核验" },
    remote: "此来源仅提供同步的状态与验收观察，交付链需要实时本机来源。",
  } : {
    direction: "Direction", current: "Current work", agentSummary: "Agent's progress summary", attention: "Needs you", details: "Goal information",
    tasks: "View tasks", execution: "Execution", optional: "{count} optional",
    noTarget: "No measurable target declared. Targets are declared through the Goal's acceptance configuration.",
    verified: "{passed} of {total} criteria verified", criterion: { verified: "Verified", failing: "Failing", unverified: "Not yet verified" },
    remote: "This source provides synchronized status and acceptance observations. The delivery chain requires the live local source.",
  };
  const attention = userTodos.filter(item => item.goalId === goal.goalId);
  const needs = useGoalWorkspaceDigest(goal, userTodos, items).needsYou;
  const blocking = needs.availability === "ok" ? needs.blocking : null;
  const optional = needs.availability === "ok" ? needs.optional : null;
  const totalAttention = blocking !== null && optional !== null ? blocking + optional : null;
  const showAttention = attention.length > 0 || (totalAttention !== null && totalAttention > 0);
  const moreAttention = needs.availability !== "ok" || needs.coverage !== "complete" || (totalAttention ?? attention.length) > 3;
  const run = items.find((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> => item.kind === "run" && item.run.goalId === goal.goalId);
  const observation = goal.acceptanceObservation?.goal_id === goal.goalId ? goal.acceptanceObservation : null;
  const contract = observation?.goal_acceptance_contract?.enabled === true ? observation.goal_acceptance_contract : null;
  const criteria = contract ? criterionStates(contract) : [];
  const passed = criteria.filter(criterion => criterion.state === "verified").length;
  const acceptanceCopy = goalAcceptanceContractCopy[locale === "zh-CN" ? "zh-CN" : "en"];
  return <section className="goal-overview" aria-label={t("header.overview")}>
    <div className={`goal-overview-summary${showAttention ? " has-attention" : ""}`}>
      <section className="goal-overview-direction" aria-label={copy.direction}>
        <header className="goal-overview-heading">
          <h2>{contract ? copy.direction : copy.current}</h2>
          {contract ? <span>{acceptanceCopy.verificationState[contract.status]}</span> : null}
          <button onClick={onOpenDetails} type="button"><Info aria-hidden size={14} />{copy.details}</button>
        </header>
        {contract ? <>
          <p className="goal-overview-objective">{contract.objective || acceptanceCopy.unknown}</p>
          {criteria.length ? <>
            <div className="goal-overview-progress">
              <span>{copy.verified.replace("{passed}", String(passed)).replace("{total}", String(criteria.length))}</span>
              <span aria-hidden className="goal-criteria-meter">{criteria.map(criterion => <i className={`is-${criterion.state}`} key={criterion.id} />)}</span>
            </div>
            <ul className="goal-overview-criteria">{criteria.map(criterion => <li className={`is-${criterion.state}`} key={criterion.id}>
              {criterion.state === "verified" ? <CircleCheck aria-hidden size={16} /> : criterion.state === "failing" ? <CircleX aria-hidden size={16} /> : <CircleDashed aria-hidden size={16} />}
              <span>{criterion.description}</span><small>{copy.criterion[criterion.state]}</small>
            </li>)}</ul>
          </> : null}
          <p className="goal-overview-note"><small>{copy.agentSummary}</small> {goal.nextSentence}</p>
        </> : <>
          <p className="goal-overview-objective goal-overview-current">{goal.nextSentence}</p>
          {goal.agentSentence && goal.agentSentence !== goal.nextSentence ? <details className="goal-overview-agent-note"><summary>{copy.agentSummary}</summary><p className="goal-overview-note">{goal.agentSentence}</p></details> : null}
          <p className="goal-overview-note">{copy.noTarget}</p>
        </>}
        {run ? <button className="goal-overview-run" type="button" onClick={() => onSelect({ kind: "run", item: run.run })}><small>{copy.execution} · {run.run.agentLabel}</small><ArrowRight aria-hidden size={15} /></button> : null}
      </section>
      {showAttention ? <section className="goal-overview-attention" aria-label={copy.attention} id={goalOverviewAttentionId} tabIndex={-1}>
        <header>
          <h2>{copy.attention}</h2>
          <span className={blocking ? "goal-overview-count is-blocking" : "goal-overview-count"}>{blocking ?? "—"}</span>
          {optional ? <small>{copy.optional.replace("{count}", String(optional))}</small> : null}
        </header>
        <ul>{attention.slice(0, 3).map(item => <li key={item.todoId}><button type="button" onClick={() => onSelect({ kind: "attention", item })}>
          <Hand aria-hidden size={15} />
          <span className="goal-attention-text"><span>{item.text}</span>
            {item.updatedAt ? <time dateTime={item.updatedAt}>{relativeTime(item.updatedAt, locale)}</time> : null}</span>
          <ArrowRight aria-hidden size={14} />
        </button></li>)}</ul>
        {needs.availability === "ok" && needs.coverage !== "complete" ? <small>{t("vitals.needsYouPartial", { count: needs.listed })}</small> : null}
        {moreAttention ? <button className="goal-overview-more" type="button" onClick={() => onView("tasks")}>{copy.tasks}{totalAttention !== null ? ` (${totalAttention})` : ""}<ArrowRight aria-hidden size={14} /></button> : null}
      </section> : null}
    </div>
    {readOnly ? <><p className="goal-overview-source-note">{copy.remote}</p><GoalAcceptanceObservationCard goal={goal} /></>
      : <DeliveryReview active={active} goal={goal} items={items} userTodos={userTodos} onSelect={onSelect} />}
  </section>;
}
