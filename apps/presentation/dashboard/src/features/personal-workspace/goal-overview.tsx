import {useEffect, useState, type ReactNode} from "react";
import {type DeliveryReviewRead} from "./delivery-review-state";
import {GoalWorkspaceWork} from "./goal-workspace-work";
import { ArrowRight, CircleCheck, CircleDashed, CircleX, Hand, Info } from "lucide-react";
import { DeliveryReviewContent, DeliveryWorkMap } from "./delivery-review";
import { relativeTime } from "./goal-activity-view";
import { goalAcceptanceContractCopy } from "./delivery-review-copy";
import type {GoalWorkspaceDigest} from "../../../../../../loopx/control_plane/presentation/goal_workspace_digest.js";
import { GoalAcceptanceObservationCard } from "./goal-acceptance-observation-card";
import type { WorkspaceActionPreview, WorkspaceActionPreviewRequest, WorkspaceDrawerSelection, WorkspaceGoal, WorkspaceGoalTab, WorkspaceModel, WorkspaceTimelineItem } from "./personal-workspace-model";
import { useWorkspaceI18n } from "./i18n";
import {projectGoalAcceptanceCriteria} from "../../data/goal-acceptance-observation";
import "./goal-overview.css";

export const goalOverviewAttentionId = "goal-overview-attention";

export function GoalOverview({ active, goal, items, userTodos, readOnly, onOpenDetails, onSelect, onView, selectedTodoId, board, results, proposals, onPreview, historyRefreshRevision, review, digest, railOpen, onCloseRail }: {
  railOpen: boolean; onCloseRail: () => void;
  review: DeliveryReviewRead; digest: GoalWorkspaceDigest;
  active: boolean; goal: WorkspaceGoal; items: WorkspaceTimelineItem[]; userTodos: WorkspaceModel["userTodos"];
  selectedTodoId: string | null; board: ReactNode; results: ReactNode; historyRefreshRevision: number;
  proposals: Record<string, WorkspaceActionPreview>; onPreview: (request: WorkspaceActionPreviewRequest) => Promise<WorkspaceActionPreview>;
  readOnly: boolean; onOpenDetails: () => void; onSelect: (selection: WorkspaceDrawerSelection | null) => void; onView: (view: WorkspaceGoalTab) => void;
}) {
  const { t, locale } = useWorkspaceI18n();
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(max-width: 640px)").matches);
  const [directionExpanded, setDirectionExpanded] = useState(false);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 640px)");
    const update = () => setNarrow(media.matches);
    update(); media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const copy = locale === "zh-CN" ? {
    direction: "方向", current: "当前推进", agentSummary: "Agent 的进展摘要", attention: "需要你", details: "Goal 信息",
    tasks: "查看任务", execution: "执行记录", optional: "{count} 项可选",
    noTarget: "尚未声明可衡量目标。目标通过 Goal 的验收配置声明。",
    verified: "{passed}/{total} 项条件当前通过", criterion: { accepted: "当前通过", failed: "未通过", unverified: "尚未核验", stale: "依据已过期", held: "关联待确认", partial: "仅任务检查通过" },
    remote: "此来源仅提供同步的状态与验收观察，交付链需要实时本机来源。",
  } : {
    direction: "Direction", current: "Current work", agentSummary: "Agent's progress summary", attention: "Needs you", details: "Goal information",
    tasks: "View tasks", execution: "Execution", optional: "{count} optional",
    noTarget: "No measurable target declared. Targets are declared through the Goal's acceptance configuration.",
    verified: "{passed} of {total} criteria currently passing", criterion: { accepted: "Currently passing", failed: "Failing", unverified: "Not yet verified", stale: "Basis changed", held: "Association held", partial: "Task checks only" },
    remote: "This source provides synchronized status and acceptance observations. The delivery chain requires the live local source.",
  };
  const attention = userTodos.filter(item => item.goalId === goal.goalId);
  const needs = digest.needsYou;
  const blocking = needs.availability === "ok" ? needs.blocking : null;
  const optional = needs.availability === "ok" ? needs.optional : null;
  const totalAttention = blocking !== null && optional !== null ? blocking + optional : null;
  const showAttention = attention.length > 0 || (totalAttention !== null && totalAttention > 0);
  const moreAttention = needs.availability !== "ok" || needs.coverage !== "complete" || (totalAttention ?? attention.length) > 3;
  const run = items.find((item): item is Extract<WorkspaceTimelineItem, { kind: "run" }> => item.kind === "run" && item.run.goalId === goal.goalId);
  const observation = goal.acceptanceObservation?.goal_id === goal.goalId ? goal.acceptanceObservation : null;
  const contract = observation?.goal_acceptance_contract?.enabled === true ? observation.goal_acceptance_contract : null;
  const criteria = contract ? projectGoalAcceptanceCriteria(contract) : [];
  const passed = criteria.filter(criterion => criterion.state === "accepted").length;
  const acceptanceCopy = goalAcceptanceContractCopy[locale === "zh-CN" ? "zh-CN" : "en"];
  return <section className="goal-overview" aria-label={t("header.overview")}>
    <div className={`goal-overview-summary${showAttention ? " has-attention" : ""}`}>
      <section className="goal-overview-direction" aria-label={copy.direction}>
        <header className="goal-overview-heading">
          <h2>{contract ? copy.direction : copy.current}</h2>
          {contract ? <span>{acceptanceCopy.verificationState[contract.status]}</span> : null}
          <button onClick={onOpenDetails} type="button"><Info aria-hidden size={14} />{copy.details}</button>
        </header>
        <button className="goal-direction-toggle" type="button" aria-expanded={!narrow || directionExpanded} onClick={() => setDirectionExpanded(value => !value)}>
          {locale === "zh-CN" ? (directionExpanded ? "收起目标与条件" : "展开目标与条件") : (directionExpanded ? "Hide direction and criteria" : "Show direction and criteria")}
        </button>
        <div className="goal-direction-content" hidden={narrow && !directionExpanded}>
        {contract ? <>
          <p className="goal-overview-objective">{contract.objective || acceptanceCopy.unknown}</p>
          {criteria.length ? <>
            <div className="goal-overview-progress">
              <span>{copy.verified.replace("{passed}", String(passed)).replace("{total}", String(criteria.length))}</span>
              <span aria-hidden className="goal-criteria-meter">{criteria.map(criterion => <i className={`is-${criterion.state}`} key={criterion.id} />)}</span>
            </div>
            <ul className="goal-overview-criteria">{criteria.map(criterion => <li className={`is-${criterion.state}`} key={criterion.id}>
              {criterion.state === "accepted" ? <CircleCheck aria-hidden size={16} /> : criterion.state === "failed" ? <CircleX aria-hidden size={16} /> : <CircleDashed aria-hidden size={16} />}
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
        </div>
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
    <GoalWorkspaceWork goal={goal} digest={digest} items={items} userTodos={userTodos} selectedTodoId={selectedTodoId} onSelect={onSelect}
      readOnly={readOnly} active={active} railOpen={railOpen} onCloseRail={onCloseRail} historyRefreshRevision={historyRefreshRevision} proposals={proposals} onPreview={onPreview} board={board} results={results}
      map={readOnly ? <p className="goal-work-scope">{copy.remote}</p> : <DeliveryWorkMap goal={goal} userTodos={userTodos} review={review} onSelect={onSelect} selectedTodoId={selectedTodoId}/>}/>
    {readOnly ? <><p className="goal-overview-source-note">{copy.remote}</p><GoalAcceptanceObservationCard goal={goal} /></>
      : <DeliveryReviewContent review={review} hideWorkMap active={active} goal={goal} items={items} userTodos={userTodos} onSelect={onSelect} />}
  </section>;
}
