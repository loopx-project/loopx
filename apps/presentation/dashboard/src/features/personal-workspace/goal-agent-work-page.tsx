import {CircleHelp} from "lucide-react";
import {ContextHelp} from "./context-help";
import {useEffect, useRef, useState, type ReactNode} from "react";
import {AttachedConversation, request, retryableRead, readFailureMessage, type Binding} from "./attached-agent-conversations";
import {fetchManagedAgentSessions, type ChatSessionSummary} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import type {WorkspaceGoal} from "./personal-workspace-model";

/** Navigation uses the Goal directory; only the native provider can authorize a conversation. */
export function GoalAgentWorkPage({goal, agentId, readOnly, sessionId, onSession, children, onSelect, onClose}: {
  goal: WorkspaceGoal; agentId: string; readOnly: boolean;
  sessionId?: string; onSession: (sessionId: string, replace?: boolean) => void; children?: ReactNode;
  onSelect: (agentId: string) => void; onClose: () => void;
}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {heading.current?.focus({preventScroll: true});}, [agentId]);
  const ids = [...new Set(goal.registeredAgentIds ?? [])];
  const lane = goal.agentLanes?.find(item => item.agentId === agentId);
  const registered = ids.includes(agentId);
  const hosts = lane?.hostSurfaces ?? [];
  const title = zh ? "工作记录" : "Work conversation";
  return <section className="goal-agent-work-page" data-managed={sessionId?.startsWith("managed:") || undefined} aria-label={zh ? "Agent 工作会话" : "Agent working conversation"}>
    <header className="attached-conversation-heading">
      <h2 className="sr-only" ref={heading} tabIndex={-1}>{title}</h2>
      <div className="agent-work-navigation">
        {ids.length && agentId ? <select aria-label={zh ? "查看 Agent" : "View Agent"} value={registered ? agentId : ""} onChange={event => onSelect(event.target.value)}>
          <option value="" disabled>{zh ? "选择 Agent" : "Choose Agent"}</option>
          {ids.map(id => <option value={id} key={id}>{goal.agentLanes?.find(item => item.agentId === id)?.label ?? id}</option>)}
        </select> : null}
        <button type="button" onClick={onClose}>{zh ? "返回概览" : "Return to overview"}</button>
      </div>
    </header>
    {!agentId ? <div className="agent-work-directory">
      {ids.map(id => <button type="button" key={id} onClick={() => onSelect(id)}>{goal.agentLanes?.find(item => item.agentId === id)?.label ?? id}</button>)}
      {!ids.length ? <p role="status">{zh ? "此 Goal 尚无已注册的 Agent。" : "This Goal has no registered Agents."}</p> : null}
    </div> : readOnly ? <ContextHelp label={zh ? "来源仅有状态快照" : "Source contains a status snapshot only"} icon={CircleHelp}><p className="agent-work-unavailable" role="status">{zh ? "此来源仅提供状态快照，无法在这里读取或回复原会话。" : "This source provides a status snapshot; original conversations cannot be read or replied to here."}</p></ContextHelp>
      : !registered ? <p className="agent-work-unavailable" role="status">{zh ? "此 Agent 不在当前 Goal 的已注册列表中，请选择列表中的 Agent。" : "This Agent is not registered in the current Goal. Choose an Agent from its directory."}</p>
      : <AgentSessions key={`${goal.goalId}:${agentId}`} goalId={goal.goalId} agentId={agentId} hosts={hosts} attached={hosts.includes("codex-app")}
        zh={zh} sessionId={sessionId} onSession={onSession}>{children}</AgentSessions>}
  </section>;
}

/** Both providers stay under the registered Agent; the reader retains its original owner. */
function AgentSessions({goalId, agentId, hosts, attached, zh, sessionId, onSession, children}: {
  goalId: string; agentId: string; hosts: string[]; attached: boolean; zh: boolean; sessionId?: string;
  onSession: (sessionId: string, replace?: boolean) => void; children?: ReactNode;
}) {
  const selectionKey = `loopx-attached-selection:${goalId}:${agentId}`;
  const [selected, setSelected] = useState(() => {
    if (sessionId !== undefined) return sessionId;
    try {return sessionStorage.getItem(selectionKey) ?? "";} catch {return "";}
  });
  const [directory, setDirectory] = useState<{bindings: Binding[]; managed: ChatSessionSummary[]; errors: string[]} | null>(null);
  const [revision, setRevision] = useState(0);
  const reconnect = () => setRevision(value => value + 1);
  const previousRoute = useRef(sessionId);
  const selectedValue = sessionId !== undefined ? sessionId : previousRoute.current !== sessionId ? "" : selected;
  useEffect(() => {
    if (previousRoute.current !== sessionId) {previousRoute.current = sessionId; setSelected(sessionId ?? "");}
  }, [sessionId]);
  useEffect(() => {
    const controller = new AbortController();
    let retry: ReturnType<typeof setTimeout>;
    setDirectory(null);
    void Promise.allSettled([
      attached ? request<{agents: Binding[]}>(`/api/chat/codex-attached?${new URLSearchParams({goal_id: goalId})}`,
        {signal: AbortSignal.any([controller.signal, AbortSignal.timeout(6000)])}) : Promise.resolve({agents: []}),
      fetchManagedAgentSessions(goalId, agentId, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)])),
    ]).then(([host, native]) => {
      if (controller.signal.aborted) return;
      const errors = [host, native].flatMap(result => result.status === "rejected" ? [readFailureMessage(result.reason, zh)] : []);
      if (host.status === "rejected" && retryableRead(host.reason)) retry = setTimeout(reconnect, 1000);
      const bindings = host.status === "fulfilled" && Array.isArray(host.value.agents) ? host.value.agents.filter(row => row.agent_id === agentId) : [];
      const managed = native.status === "fulfilled" ? native.value.sessions : [];
      setDirectory({bindings, managed, errors});
      // A revoked saved choice never silently substitutes another Session.
      if (!selected && bindings.length + managed.length === 1) {
        const value = bindings[0]?.binding_ref ?? `managed:${managed[0].session_id}`;
        setSelected(value); onSession(value, true);
      } else if (selected.startsWith("managed:") && sessionId === undefined) onSession(selected, true);
    });
    return () => {controller.abort(); clearTimeout(retry);};
    // The route is a read selection, not a reason to reload the directory.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goalId, agentId, attached, revision]);
  const binding = directory?.bindings.find(row => row.binding_ref === selectedValue);
  const managed = directory?.managed.find(row => `managed:${row.session_id}` === selectedValue);
  const count = (directory?.bindings.length ?? 0) + (directory?.managed.length ?? 0);
  const selectedUnavailable = Boolean(directory && selectedValue && !binding && !managed);
  return <>
    {directory && count > 0 && (count > 1 || selectedValue && !binding && !managed) ? <label className="attached-session-selection">
      <span>{zh ? "工作会话" : "Conversation"}</span>
      <select aria-label={zh ? "工作会话" : "Conversation"} value={binding || managed ? selectedValue : ""} onChange={event => {
        const value = event.target.value; setSelected(value); onSession(value);
        try {sessionStorage.setItem(selectionKey, value);} catch {/* Reads need no storage grant. */}
      }}>
        <option value="" disabled>{selectedValue ? (zh ? "原会话绑定已变更" : "Original conversation binding changed") : (zh ? "选择工作会话" : "Choose a conversation")}</option>
        {directory.bindings.map(row => <option key={row.binding_ref} value={row.binding_ref}>Codex App · {row.thread_id}</option>)}
        {directory.managed.map(row => <option key={row.session_id} value={`managed:${row.session_id}`}>LoopX · {new Date(row.created_at).toLocaleString(zh ? "zh-CN" : "en-US")} · {row.session_id.slice(-8)}</option>)}
      </select>
    </label> : null}
    {!directory ? <p role="status">{zh ? "正在连接原会话…" : "Connecting to the original conversation…"}</p> : null}
    {directory && (selectedUnavailable || directory.errors.length && !count) ? <p role="alert">
      {selectedUnavailable ? (zh ? "原会话绑定暂不可用。" : "The original conversation binding is unavailable.") : null}
      {directory.errors.join(" ")} <button type="button" onClick={reconnect}>{zh ? "重新连接" : "Reconnect"}</button>
    </p> : null}
    {directory?.errors.length && count > 0 && !selectedUnavailable ? <ContextHelp label={zh ? "部分会话来源不可用" : "Some conversation sources unavailable"} icon={CircleHelp}>
      {directory.errors.map((error, index) => <p key={index}>{error}</p>)}
      <button type="button" onClick={reconnect}>{zh ? "重新连接" : "Reconnect"}</button>
    </ContextHelp> : null}
    {directory && !count && !directory.errors.length && !selectedUnavailable ? <ContextHelp label={hosts.length ? (zh ? "会话来源未接入" : "Conversation source not connected") : (zh ? "尚无工作会话" : "No work conversations")} icon={CircleHelp}>
      <p>{hosts.length ? (zh ? `来源：${hosts.join("、")}。此宿主的工作记录与回复尚未接入。` : `Source: ${hosts.join(", ")}. Work records and replies from this host are not connected yet.`)
        : (zh ? "此 Agent 尚无已绑定的工作会话。原 Goal 对话记录仍独立保留。" : "This Agent has no bound work Sessions. Existing Goal conversations remain separate.")}</p>
    </ContextHelp> : null}
    {managed ? <><div className="agent-work-session-source"><span>LoopX · {managed.agent_id}</span>
      <ContextHelp label={zh ? "原会话身份" : "Original Session identity"}><p>{managed.session_id}</p><p>{managed.agent_model_id ?? (zh ? "模型未读取" : "Model not observed")} · {managed.agent_reasoning_effort ?? "—"}</p></ContextHelp>
    </div>{children}</> : binding ? <AttachedConversation key={binding.binding_ref} goalId={goalId} binding={binding} zh={zh} reconnect={reconnect}/> : null}
  </>;
}
