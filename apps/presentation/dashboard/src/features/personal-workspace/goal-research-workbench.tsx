import {useState, type ReactNode} from "react";
import {GoalResearchHistory} from "./goal-research-history";
import {GoalExploreResults} from "./goal-explore-results";
import {goalHierarchy} from "./goal-hierarchy";
import type {WorkspaceGoal} from "./personal-workspace-model";
import {ResearchChildGoals, type ResearchChildGoal} from "./research-child-goals";
import {GoalSourceDocument} from "./goal-source-document";
import './research-case-study.css';

type ResearchGoal = ResearchChildGoal & Pick<WorkspaceGoal, 'parentGoalId' | 'sourceStrategy'>;

/** Parentage organizes explicit source views; it never combines evidence or execution scope. */
function ResearchGoalNavigation({goalId, goals, zh, onSelect, directoryUrl, active, refreshRevision}: {
  goalId: string; goals: ResearchGoal[]; zh: boolean; onSelect: (goalId: string) => void;
  directoryUrl?: string; active: boolean; refreshRevision: number;
}) {
  const selected = goals.find(g => g.goalId === goalId);
  if (!selected) return null;
  // The directory owns these explicit presentation links. Different source
  // namespaces remain isolated by each destination's read transport.
  const tree = goalHierarchy(goals);
  const parentId = tree.parents.get(goalId), parent = parentId ? tree.byId.get(parentId) : undefined;
  const children = tree.children.get(goalId) ?? [];
  if (!parent && !children.length) return null;
  return <nav className="research-goal-navigation" aria-label={zh ? '研究 Goal 层级' : 'Research Goal hierarchy'}>
    {parent ? <button type="button" className="research-parent-goal" title={parent.title} onClick={() => onSelect(parent.goalId)}>
      <span aria-hidden="true">←</span><span className="research-parent-title">{parent.title}</span>
    </button> : null}
    {children.length ? <ResearchChildGoals goals={children} zh={zh} onSelect={onSelect}
      directoryUrl={directoryUrl} active={active} showTiming refreshRevision={refreshRevision}/> : null}
  </nav>;
}

/** One read surface for the selected original Goal; opening it never starts research. */
export function GoalResearchWorkbench(props: {goalId: string; active: boolean; zh: boolean; refreshRevision: number; readSourceUrl?: string; directoryReadSourceUrl?: string; goals?: ResearchGoal[]; onSelectGoal?: (goalId: string) => void; onOpenTasks?: () => void; statusNotice?: ReactNode}) {
  const selected = props.goals?.find(goal => goal.goalId === props.goalId);
  const source = selected?.sourceAccess;
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  return <div className="goal-research-workbench">
    {props.statusNotice}
    {/* Each child timing read validates its own source independently of the
        selected snapshot, whose failure still blocks this Goal's history. */}
    {props.goals && props.onSelectGoal ? <ResearchGoalNavigation goalId={props.goalId} goals={props.goals}
      zh={props.zh} onSelect={props.onSelectGoal} directoryUrl={props.directoryReadSourceUrl}
      active={props.active} refreshRevision={props.refreshRevision}/> : null}
    {!props.statusNotice ? <>
      {props.readSourceUrl && new URL(props.readSourceUrl, window.location.href).searchParams.has("source_namespace")
        ? <GoalSourceDocument key={JSON.stringify([props.goalId, props.readSourceUrl])} goalId={props.goalId}
          originalGoalId={source?.original_goal_id ?? props.goalId} sourceUrl={props.readSourceUrl} zh={props.zh}/> : null}
      <GoalResearchHistory {...props} originalGoalId={source?.original_goal_id}
        preferHistory={Boolean(source)}
        onOpenSourceTasks={source ? props.onOpenTasks : undefined}/>
      <details className="research-saved-evidence" open={evidenceOpen} onToggle={event => setEvidenceOpen(event.currentTarget.open)}>
        <summary>{props.zh ? "阅读已保存的结论" : "Read saved findings"}</summary>
        {evidenceOpen ? <GoalExploreResults originalGoalId={source?.original_goal_id} goalId={props.goalId} zh={props.zh} active={props.active} readSourceUrl={props.readSourceUrl}/> : null}
      </details>
    </> : null}
  </div>;
}
