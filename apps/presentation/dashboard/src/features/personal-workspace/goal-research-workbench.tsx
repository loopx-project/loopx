import {useState} from "react";
import {GoalResearchHistory} from "./goal-research-history";
import {GoalExploreResults} from "./goal-explore-results";
import {goalHierarchy} from "./goal-hierarchy";
import type {WorkspaceGoal} from "./personal-workspace-model";
import './research-case-study.css';

type ResearchGoal = Pick<WorkspaceGoal, 'goalId' | 'parentGoalId' | 'title' | 'sourceAccess' | 'sourceStrategy'>;

/** Parentage organizes explicit source views; it never combines evidence or execution scope. */
export function ResearchGoalNavigation({goalId, goals, zh, onSelect}: {
  goalId: string; goals: ResearchGoal[]; zh: boolean; onSelect: (goalId: string) => void;
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
    {parent ? <button type="button" className="research-parent-goal" onClick={() => onSelect(parent.goalId)}>← {parent.title}</button> : null}
    {children.length ? <><h2>{zh ? '子 Goal 研究' : 'Research in child Goals'}</h2>
      <div className="research-child-goals">{children.map(child => <button type="button" key={child.goalId} onClick={() => onSelect(child.goalId)}>{child.title}<span aria-hidden="true">↗</span></button>)}</div></> : null}
  </nav>;
}

/** One read surface for the selected original Goal; opening it never starts research. */
export function GoalResearchWorkbench(props: {goalId: string; active: boolean; zh: boolean; refreshRevision: number; readSourceUrl?: string; goals?: ResearchGoal[]; onSelectGoal?: (goalId: string) => void; onOpenTasks?: () => void}) {
  const selected = props.goals?.find(goal => goal.goalId === props.goalId);
  const source = selected?.sourceAccess;
  const [evidenceOpen, setEvidenceOpen] = useState(false);
  return <div className="goal-research-workbench">
    {props.goals && props.onSelectGoal ? <ResearchGoalNavigation goalId={props.goalId} goals={props.goals} zh={props.zh} onSelect={props.onSelectGoal}/> : null}
    <GoalResearchHistory {...props} originalGoalId={source?.original_goal_id}
      preferHistory={Boolean(source)} sourceStrategy={selected?.sourceStrategy}
      onOpenSourceTasks={source ? props.onOpenTasks : undefined}/>
    <details className="research-saved-evidence" open={evidenceOpen} onToggle={event => setEvidenceOpen(event.currentTarget.open)}>
      <summary>{props.zh ? "阅读已保存的结论" : "Read saved findings"}</summary>
      {evidenceOpen ? <GoalExploreResults originalGoalId={source?.original_goal_id} goalId={props.goalId} zh={props.zh} active={props.active} readSourceUrl={props.readSourceUrl}/> : null}
    </details>
  </div>;
}
