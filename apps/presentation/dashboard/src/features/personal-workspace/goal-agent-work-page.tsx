import {useEffect, useRef} from "react";
import {AttachedAgentConversations} from "./attached-agent-conversations";
import {useWorkspaceI18n} from "./i18n";
import type {WorkspaceGoal} from "./personal-workspace-model";

/** Navigation uses the Goal directory; only the native provider can authorize a conversation. */
export function GoalAgentWorkPage({goal, agentId, readOnly, onSelect, onClose}: {
  goal: WorkspaceGoal; agentId: string; readOnly: boolean;
  onSelect: (agentId: string) => void; onClose: () => void;
}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {heading.current?.focus({preventScroll: true});}, [agentId]);
  const ids = [...new Set(goal.registeredAgentIds ?? [])];
  const lane = goal.agentLanes?.find(item => item.agentId === agentId);
  const registered = ids.includes(agentId);
  const hosts = lane?.hostSurfaces ?? [];
  const supported = hosts.includes("codex-app");
  const title = zh ? "工作记录" : "Work conversation";
  return <section className="goal-agent-work-page" aria-label={zh ? "Agent 工作会话" : "Agent working conversation"}>
    <header className="attached-conversation-heading">
      <h2 ref={heading} tabIndex={-1}>{title}</h2>
      <div className="agent-work-navigation">
        {ids.length ? <select aria-label={zh ? "查看 Agent" : "View Agent"} value={registered ? agentId : ""} onChange={event => onSelect(event.target.value)}>
          <option value="" disabled>{zh ? "选择 Agent" : "Choose Agent"}</option>
          {ids.map(id => <option value={id} key={id}>{goal.agentLanes?.find(item => item.agentId === id)?.label ?? id}</option>)}
        </select> : null}
        <button type="button" onClick={onClose}>{zh ? "返回概览" : "Return to overview"}</button>
      </div>
    </header>
    {!agentId ? <div className="agent-work-directory">
      {ids.map(id => <button type="button" key={id} onClick={() => onSelect(id)}>{goal.agentLanes?.find(item => item.agentId === id)?.label ?? id}</button>)}
      {!ids.length ? <p role="status">{zh ? "此 Goal 尚无已注册的 Agent。" : "This Goal has no registered Agents."}</p> : null}
    </div> : readOnly ? <p className="agent-work-unavailable" role="status">{zh ? "此来源仅提供状态快照，无法在这里读取或回复原会话。" : "This source provides a status snapshot; original conversations cannot be read or replied to here."}</p>
      : !registered ? <p className="agent-work-unavailable" role="status">{zh ? "此 Agent 不在当前 Goal 的已注册列表中，请选择列表中的 Agent。" : "This Agent is not registered in the current Goal. Choose an Agent from its directory."}</p>
      : !supported ? <p className="agent-work-unavailable" role="status">{hosts.length
        ? (zh ? `来源：${hosts.join("、")}。此宿主的工作记录与回复尚未接入。` : `Source: ${hosts.join(", ")}. Work records and replies from this host are not connected yet.`)
        : (zh ? "此 Agent 尚无已绑定的会话来源。" : "This Agent has no bound conversation source yet.")}</p>
      : <AttachedAgentConversations key={`${goal.goalId}:${agentId}`} goalId={goal.goalId} targetAgentId={agentId} zh={zh}/>}
  </section>;
}
