import {useEffect, useState} from "react";
import {connectPrivateConversation, disconnectPrivateConversation, fetchPrivateConversations,
  changePrivateAgentTarget, fetchChatProjects, fetchChatCapabilities, fetchLarkApps, fetchLarkGroupChats, type PrivateConversation,
  type ChatProject, type LarkApp} from "../../data/chat";
import {useWorkspaceI18n} from "./i18n";
import "./private-conversation.css";

export function PrivateConversationPanel() {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [apps, setApps] = useState<LarkApp[]>([]);
  const [projects, setProjects] = useState<ChatProject[]>([]);
  const [executors, setExecutors] = useState<string[]>([]);
  const [rows, setRows] = useState<PrivateConversation[]>([]);
  const [revision, setRevision] = useState(0);
  const [app, setApp] = useState("");
  const [project, setProject] = useState("");
  const [executor, setExecutor] = useState("");
  const [role, setRole] = useState<"project" | "steward">("project");
  const [audience, setAudience] = useState<"owner" | "group">("owner");
  const [groups, setGroups] = useState<Awaited<ReturnType<typeof fetchLarkGroupChats>>>([]);
  const [groupIds, setGroupIds] = useState<string[]>([]);
  const [groupError, setGroupError] = useState("");
  const [loadingGroups, setLoadingGroups] = useState(false);
  const [groupRefresh, setGroupRefresh] = useState(0);
  const [projectGrant, setProjectGrant] = useState<"workspace_read" | "workspace_write">("workspace_write");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function refresh() {
    const state = await fetchPrivateConversations();
    setRows(state.connections); setRevision(state.revision);
  }
  useEffect(() => {
    let active = true;
    Promise.all([fetchLarkApps(), fetchChatProjects(), fetchChatCapabilities(), fetchPrivateConversations()])
      .then(([apps, projects, capabilities, state]) => {
        if (!active) return;
        setApps(apps.filter(app => app.ready && app.app_ref !== "default"));
        setProjects(projects.projects);
        const choices = (capabilities.adapters ?? []).filter(row => row.available).map(row => row.agent_id);
        setExecutors(choices); setExecutor(choices.includes("codex") ? "codex" : choices[0] ?? "");
        if (projects.projects.length === 1) setProject(projects.projects[0].project_ref);
        setRows(state.connections); setRevision(state.revision);
      }).catch(error => {if (active) setError(String(error));});
    const timer = setInterval(() => {fetchPrivateConversations().then(state => {
      if (active) {setRows(state.connections); setRevision(state.revision);}
    }).catch(() => {});}, 5000);
    return () => {active = false; clearInterval(timer);};
  }, []);

  useEffect(() => {
    let active = true;
    setGroups([]); setGroupIds([]); setGroupError("");
    setLoadingGroups(false);
    if (!app || audience !== "group") return;
    setLoadingGroups(true);
    fetchLarkGroupChats(app).then(chats => {
      if (active) {setGroups(chats); setGroupIds(chats.filter(chat => chat.selected).map(chat => chat.chat_id));}
    }).catch(error => {if (active) setGroupError(String(error));})
      .finally(() => {if (active) setLoadingGroups(false);});
    return () => {active = false;};
  }, [app, audience, groupRefresh]);

  const effectiveProjectGrant = executor === "codex" && projects.find(item => item.project_ref === project)?.grant === "workspace_write"
    ? projectGrant : "workspace_read";

  function listenerLabel(state: string) {
    const labels: Record<string, [string, string]> = {starting: ["启动中", "Starting"], listening: ["实时连接就绪", "Live connection ready"],
      standby: ["等待已有监听服务", "Waiting for the existing listener"], retrying: ["重连中", "Reconnecting"],
      stopped: ["已停止", "Stopped"], inactive: ["配置未启用", "Inactive"]};
    return labels[state]?.[zh ? 0 : 1] ?? (zh ? "连接尚未确认" : "Connection unconfirmed");
  }

  async function act(operation: () => Promise<unknown>) {
    setBusy(true); setError("");
    try {await operation(); await refresh();} catch (error) {setError(String(error));}
    finally {setBusy(false);}
  }
  function selectApp(appRef: string) {
    setApp(appRef);
    const saved = rows.find(row => row.app_ref === appRef);
    if (saved) {
      setAudience(saved.audience);
      setProject(saved.project_ref); setExecutor(saved.executor_endpoint_id); setRole(saved.context_kind);
      setProjectGrant(saved.grant === "workspace_write" ? "workspace_write" : "workspace_read");
    } else {setProjectGrant("workspace_write"); setAudience("owner");}
  }
  return <section className="personal-detail-card personal-private-conversation" aria-label={zh ? "飞书项目助手与管家" : "Lark project assistants and steward"}>
    <h3>{zh ? "飞书 · 项目助手与管家" : "Lark · Project assistants and steward"}</h3>
    <p>{zh ? "每个 App 单独核验本人。项目助手默认支持工作区读写，按项目规则与 skills 执行编辑；可选只读。普通聊天不创建隐藏 Goal。本人管家默认读取本机已注册的 Goal 与 Agent；没有注册工作时显示空态。旧连接保留原范围，需明确升级授权。交办不提升执行或发布权限。" : "Verify the owner independently for each App. Project Chat defaults to workspace writes under project rules and skills; read-only remains available without hidden Goals. A personal steward defaults to the registered local Goals and Agents, including an honest empty state. Existing connections retain their scope until explicitly upgraded. Handoff does not elevate execution or publishing permissions."}</p>
    {rows.map(row => <article key={row.binding_id}>
      <strong>{row.app_ref} · {row.context_available ? row.project_title : (zh ? "工作区不可用" : "Workspace unavailable")}</strong>
      <p>{row.context_kind === "steward" ? (row.goal_scope === "all_registered"
        ? (zh ? `LoopX 管家 · 全部已注册工作 · ${row.goal_count} 个 Goal` : `LoopX steward · All registered work · ${row.goal_count} Goals`)
        : (zh ? `LoopX 管家 · 已选范围 · ${row.goal_count} 个 Goal` : `LoopX steward · Selected scope · ${row.goal_count} Goals`)) : (row.grant === "workspace_write" ? (zh ? "普通项目助手 · 已授权工作区读写" : "Project assistant · Workspace writes authorized") : (zh ? "普通项目助手 · 只读对话" : "Project assistant · Read-only Chat"))}</p>
      {row.audience === "group" ? <p>{zh ? `群话题助手 · 已授权 ${row.group_count} 个群 · 独立工作区隔离` : `Group topic assistant · ${row.group_count} authorized groups · Isolated workspace`}</p> : null}
      <p>{row.executor_endpoint_id} · {zh ? "监听状态" : "Listener"}: {listenerLabel(row.listener_status)}</p>
      <p>{zh ? `待处理或回复：${row.pending_count}` : `Pending execution or reply: ${row.pending_count}`}</p>
      {row.recovery_count > 0 ? <p role="status">{zh ? "存在尚未确认的发送回执。服务会读取原回执恢复；不要重新发送同一任务。检查 App 登录、权限和原会话后刷新状态。" : "A send receipt is unconfirmed. The service reads the original receipt to recover; avoid resending the same task. Check this App login, permissions and original Session, then refresh status."}</p> : null}
      {!row.context_available ? <p role="alert">{zh ? "工作区授权已失效；请恢复原工作区或重新选择。旧会话不会移到其它工作区。" : "The workspace grant is unavailable. Restore the original workspace or select a new one; the old Session will not move."}</p> : null}
      {row.context_kind === "project" && row.audience !== "group" ? <PrivateAgentTargets row={row} revision={revision} zh={zh} busy={busy} act={act}/> : null}
      {row.context_kind === "steward" && row.goal_scope !== "all_registered" ? <button disabled={busy || !row.context_available} onClick={() => void act(() => connectPrivateConversation(row.app_ref, row.project_ref, row.executor_endpoint_id, "steward"))} type="button">{zh ? "授权全部已注册工作" : "Authorize all registered work"}</button> : null}
      <button disabled={busy} onClick={() => void act(() => disconnectPrivateConversation(row.binding_id, revision))} type="button">{zh ? "解绑" : "Disconnect"}</button>
    </article>)}
    {rows.length === 0 ? <p>{zh ? "尚未连接助手。" : "No assistant connected."}</p> : null}
    <label>App<select aria-label={zh ? "助手 App" : "Assistant App"} value={app} disabled={busy} onChange={event => selectApp(event.target.value)}>
      <option value="">{zh ? "选择已验证 App" : "Select a verified App"}</option>
      {apps.map(app => <option key={app.app_ref} value={app.app_ref}>{app.label} · {app.app_ref}</option>)}
    </select></label>
    <label>{zh ? "使用位置" : "Conversation audience"}<select aria-label={zh ? "使用位置" : "Conversation audience"} value={audience} disabled={busy} onChange={event => {
      const value = event.target.value as "owner" | "group";
      setAudience(value); if (value === "group") {setRole("project"); setExecutor("codex");}
    }}>
      <option value="owner">{zh ? "本人私聊" : "Owner private Chat"}</option>
      <option value="group">{zh ? "指定群的话题" : "Topics in selected groups"}</option>
    </select></label>
    {audience === "group" ? <fieldset disabled={busy || loadingGroups} style={{display: "grid", gap: 12, border: "1px solid var(--pw-line-strong)", borderRadius: 10, padding: 16}}>
      <legend>{zh ? "允许使用的群（最多 16 个）" : "Authorized groups (up to 16)"}</legend>
      {loadingGroups ? <p role="status">{zh ? "正在读取此 App 的群" : "Reading this App's groups"}</p> : groups.map(chat => <label key={chat.chat_id} style={{display: "flex", alignItems: "center"}}>
        <input type="checkbox" checked={groupIds.includes(chat.chat_id)} onChange={event => setGroupIds(ids => event.target.checked ? [...ids, chat.chat_id] : ids.filter(id => id !== chat.chat_id))}/>{chat.chat_name}
      </label>)}
      {!loadingGroups && !groups.length && !groupError ? <p>{zh ? "此 App 尚无可见群。先将 Bot 加入调试群，再刷新。" : "This App has no visible groups. Add the Bot to a trial group, then refresh."}</p> : null}
      {groupError ? <p role="alert">{groupError}</p> : null}
      <button type="button" disabled={busy || loadingGroups || !app} onClick={() => setGroupRefresh(value => value + 1)}>{zh ? "刷新群列表" : "Refresh groups"}</button>
      <p>{zh ? "新话题需 @此 Bot；回复留在原话题。仅使用选定工作区，不能继承个人管家、全局工具或已有 Agent 会话。需要在独立执行环境登录；连接成功不代表已通过公开群准出。" : "Mention this Bot to start; replies stay in the original topic. Only the selected workspace is available, without personal steward, global tools or existing Agent Sessions. Sign in to the independent execution environment. Connection is not public release qualification."}</p>
    </fieldset> : null}
    <label>{zh ? "角色" : "Role"}<select aria-label={zh ? "私聊角色" : "Private Chat role"} value={role} disabled={busy || audience === "group"} onChange={event => setRole(event.target.value as "project" | "steward")}>
      <option value="project">{zh ? "普通项目助手" : "Project assistant"}</option><option value="steward">{zh ? "LoopX 管家（全部已注册工作）" : "LoopX steward (all registered work)"}</option>
    </select></label>
    <label>{zh ? "工作区" : "Workspace"}<select aria-label={zh ? "私聊工作区" : "Private Chat workspace"} value={project} disabled={busy} onChange={event => setProject(event.target.value)}>
      <option value="">{zh ? "选择授权工作区" : "Select an authorized workspace"}</option>
      {projects.map(project => <option key={project.project_ref} value={project.project_ref}>{project.title}</option>)}
    </select></label>
    <label>{zh ? "执行器" : "Executor"}<select aria-label={zh ? "私聊执行器" : "Private Chat executor"} value={executor} disabled={busy || audience === "group"} onChange={event => setExecutor(event.target.value)}>
      {executors.map(executor => <option key={executor} value={executor}>{executor}</option>)}
    </select></label>
    {role === "project" ? <label>{zh ? "工作区权限" : "Workspace access"}<select aria-label={zh ? "私聊工作区权限" : "Private Chat workspace access"} value={effectiveProjectGrant} disabled={busy || executor !== "codex"} onChange={event => setProjectGrant(event.target.value as "workspace_read" | "workspace_write")}>
      <option value="workspace_read">{zh ? "只读" : "Read-only"}</option>
      <option value="workspace_write" disabled={projects.find(item => item.project_ref === project)?.grant === "workspace_read"}>{zh ? "工作区读写（默认）" : "Workspace writes (default)"}</option>
    </select></label> : null}
    {role === "project" && effectiveProjectGrant === "workspace_write" ? <p role="status">{zh ? "此 App 可按你的指令编辑选定工作区；不提高已直连 Agent 的原宿主权限。更改权限会建立新绑定和新会话，旧会话不会自动提升权限，已有 Agent 直连需重新授权。" : "This App may edit the selected workspace on your instruction; attached Agents retain their original host permissions. Changing access creates a new binding and Session. Existing Chat does not gain access and Agent targets require new authorization."}</p> : null}
    <div className="personal-detail-actions"><button disabled={busy || !app || !project || !executor || (audience === "group" && (loadingGroups || groupIds.length === 0 || groupIds.length > 16))} onClick={() => void act(() => connectPrivateConversation(app, project, executor, role, role === "project" ? effectiveProjectGrant : "workspace_read", audience === "group" ? groupIds : undefined))} type="button">
      {busy ? (zh ? "正在核验" : "Verifying") : (audience === "group" ? (zh ? "连接指定群话题" : "Connect selected group topics") : (zh ? "连接本人私聊" : "Connect owner private Chat"))}</button>
      <button disabled={busy} onClick={() => void act(refresh)} type="button">{zh ? "刷新状态" : "Refresh status"}</button></div>
    <p>{zh ? "发送文字、图片或图文消息开始；后续消息进入原会话队列。/status 查看工作区、角色与持久排队状态，/help 查看用法与解绑入口，/stop 停止当前聊天执行，/new 开启新会话。文件、音视频、附在控制命令或已选择 Agent 上的图片会明确提示暂不支持。" : "Send text, images or image/text posts to begin; follow-ups queue in the same Session. /status shows the workspace, role and durable queue, /help explains commands and where to unbind, /stop stops the current Chat Turn, /new starts a new conversation. Files, audio/video, and images sent with control commands or to a selected attached Agent receive an explicit unsupported response."}</p>
    {audience === "owner" ? <p>{zh ? "管家新委托：/delegate --tokens N 具体目标。先读预览，再用原私聊的完整 /confirm 命令确认；15 分钟过期。原生执行保持只读，总 token 上限可能被运行中的请求超过；没有默认定时调度。回执提供 /stop-commission 停止和 /resume-commission 恢复命令；恢复保留原线程及累计用量。" : "Steward commission: /delegate --tokens N objective. Read the preview, then use its full /confirm command in the original private Chat within 15 minutes. Native execution remains read-only; in-flight requests can exceed the total token allowance. No default schedule. Receipts provide /stop-commission and /resume-commission commands; recovery retains the original thread and cumulative usage."}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
  </section>;
}

function PrivateAgentTargets({row, revision, zh, busy, act}: {row: PrivateConversation; revision: number; zh: boolean;
  busy: boolean; act: (operation: () => Promise<unknown>) => Promise<void>}) {
  const [session, setSession] = useState("");
  const candidates = row.agent_candidates.filter(item => !row.agent_targets.some(target => target.session_id === item.session_id));
  return <div>
    <p>{zh ? "直连注册 Agent：只授权确切的已有 attached Session。消息进入原宿主队列；此处不创建 Agent、不继承其它目标或提高宿主权限。一个 Session 的 App 受众固定，撤销后也不能换给另一 App。" : "Direct registered Agent: grant an exact existing attached Session. Messages enter its original host queue; this creates no Agent, inherits no other Goals and raises no host permission. The Session audience remains fixed even after revocation."}</p>
    {row.agent_targets.map(target => <p key={target.target_ref}>{target.agent_id} · {target.goal_id}<br/>
      <code>/agent {target.target_ref}</code> <button type="button" disabled={busy} onClick={() => void act(() => changePrivateAgentTarget(row.binding_id, revision, {target_ref: target.target_ref}))}>{zh ? "撤销直连授权" : "Revoke direct access"}</button></p>)}
    <label>{zh ? "已有 Agent 会话" : "Existing Agent Session"}<select value={session} disabled={busy} onChange={event => setSession(event.target.value)}>
      <option value="">{zh ? "选择确切会话" : "Select exact Session"}</option>
      {candidates.map(item => <option key={item.session_id} value={item.session_id}>{item.agent_id} · {item.goal_id} · {item.session_id.slice(-6)}</option>)}
    </select></label>
    <button type="button" disabled={busy || !candidates.some(item => item.session_id === session)} onClick={() => void act(() => changePrivateAgentTarget(row.binding_id, revision, {session_id: session}))}>{zh ? "授权此 App 直连" : "Grant this App direct access"}</button>
    {row.agent_candidates.length === 0 ? <p>{zh ? "暂无此工作区的可用 attached Agent 会话。请先在原宿主完成注册和 attached-session-bind；普通聊天仍可直接使用，不需要创建 Goal。" : "No eligible attached Agent Session in this workspace. Register and bind it in its original host first; ordinary Chat remains available without a Goal."}</p> : null}
    <p>{zh ? "私聊 /agents 查看当前可用授权，复制完整 /agent 命令选择；/project 返回原项目对话。实时停止或新建 Agent 会话请在原宿主处理。" : "Use /agents to list usable grants, select with the full /agent command, and /project to return to project Chat. Stop or create Agent Sessions in their original host."}</p>
  </div>;
}
