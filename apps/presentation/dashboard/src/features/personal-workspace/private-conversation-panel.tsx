import {useEffect, useState} from "react";
import {connectPrivateConversation, disconnectPrivateConversation, fetchPrivateConversations,
  changePrivateAgentTarget, fetchChatProjects, fetchChatCapabilities, fetchLarkApps, type PrivateConversation,
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
      setProject(saved.project_ref); setExecutor(saved.executor_endpoint_id); setRole(saved.context_kind);
      setProjectGrant(saved.grant === "workspace_write" ? "workspace_write" : "workspace_read");
    } else setProjectGrant("workspace_write");
  }
  return <section className="personal-detail-card personal-private-conversation" aria-label={zh ? "本人飞书私聊" : "Owner private Chat"}>
    <h3>{zh ? "本人私聊 · 项目助手与管家" : "Owner private Chat · Project assistant and steward"}</h3>
    <p>{zh ? "每个 App 单独核验本人。项目助手默认支持工作区读写，按项目规则与 skills 执行编辑；可选只读。普通聊天不创建隐藏 Goal。管家从空 portfolio 开始，只管理在此入口明确确认的新委托。" : "Verify the owner independently for each App. Project Chat defaults to workspace writes under project rules and skills; read-only remains available without hidden Goals. A steward starts with an empty portfolio and manages only new commissions explicitly confirmed here."}</p>
    {rows.map(row => <article key={row.binding_id}>
      <strong>{row.app_ref} · {row.context_available ? row.project_title : (zh ? "工作区不可用" : "Workspace unavailable")}</strong>
      <p>{row.context_kind === "steward" ? (zh ? `LoopX 管家 · ${row.goal_count === 0 ? "暂无已授权的新委托；没有继承旧目标。" : `${row.goal_count} 个已确认的新委托`}` : `LoopX steward · ${row.goal_count} new confirmed commissions; no inherited Goals.`) : (row.grant === "workspace_write" ? (zh ? "普通项目助手 · 已授权工作区读写" : "Project assistant · Workspace writes authorized") : (zh ? "普通项目助手 · 只读对话" : "Project assistant · Read-only Chat"))}</p>
      <p>{row.executor_endpoint_id} · {zh ? "监听状态" : "Listener"}: {listenerLabel(row.listener_status)}</p>
      <p>{zh ? `待处理或回复：${row.pending_count}` : `Pending execution or reply: ${row.pending_count}`}</p>
      {row.recovery_count > 0 ? <p role="status">{zh ? "存在尚未确认的发送回执。服务会读取原回执恢复；不要重新发送同一任务。检查 App 登录、权限和原会话后刷新状态。" : "A send receipt is unconfirmed. The service reads the original receipt to recover; avoid resending the same task. Check this App login, permissions and original Session, then refresh status."}</p> : null}
      {!row.context_available ? <p role="alert">{zh ? "工作区授权已失效；请恢复原工作区或重新选择。旧会话不会移到其它工作区。" : "The workspace grant is unavailable. Restore the original workspace or select a new one; the old Session will not move."}</p> : null}
      {row.context_kind === "project" ? <PrivateAgentTargets row={row} revision={revision} zh={zh} busy={busy} act={act}/> : null}
      <button disabled={busy} onClick={() => void act(() => disconnectPrivateConversation(row.binding_id, revision))} type="button">{zh ? "解绑" : "Disconnect"}</button>
    </article>)}
    {rows.length === 0 ? <p>{zh ? "尚未连接本人私聊。" : "No owner private Chat connected."}</p> : null}
    <label>App<select aria-label={zh ? "私聊 App" : "Private Chat App"} value={app} disabled={busy} onChange={event => selectApp(event.target.value)}>
      <option value="">{zh ? "选择已验证 App" : "Select a verified App"}</option>
      {apps.map(app => <option key={app.app_ref} value={app.app_ref}>{app.label} · {app.app_ref}</option>)}
    </select></label>
    <label>{zh ? "角色" : "Role"}<select aria-label={zh ? "私聊角色" : "Private Chat role"} value={role} disabled={busy} onChange={event => setRole(event.target.value as "project" | "steward")}>
      <option value="project">{zh ? "普通项目助手" : "Project assistant"}</option><option value="steward">{zh ? "LoopX 管家（新委托）" : "LoopX steward (new commissions)"}</option>
    </select></label>
    <label>{zh ? "工作区" : "Workspace"}<select aria-label={zh ? "私聊工作区" : "Private Chat workspace"} value={project} disabled={busy} onChange={event => setProject(event.target.value)}>
      <option value="">{zh ? "选择授权工作区" : "Select an authorized workspace"}</option>
      {projects.map(project => <option key={project.project_ref} value={project.project_ref}>{project.title}</option>)}
    </select></label>
    <label>{zh ? "执行器" : "Executor"}<select aria-label={zh ? "私聊执行器" : "Private Chat executor"} value={executor} disabled={busy} onChange={event => setExecutor(event.target.value)}>
      {executors.map(executor => <option key={executor} value={executor}>{executor}</option>)}
    </select></label>
    {role === "project" ? <label>{zh ? "工作区权限" : "Workspace access"}<select aria-label={zh ? "私聊工作区权限" : "Private Chat workspace access"} value={executor === "codex" ? projectGrant : "workspace_read"} disabled={busy || executor !== "codex"} onChange={event => setProjectGrant(event.target.value as "workspace_read" | "workspace_write")}>
      <option value="workspace_read">{zh ? "只读" : "Read-only"}</option>
      <option value="workspace_write" disabled={projects.find(item => item.project_ref === project)?.grant === "workspace_read"}>{zh ? "工作区读写（默认）" : "Workspace writes (default)"}</option>
    </select></label> : null}
    {role === "project" && executor === "codex" && projectGrant === "workspace_write" ? <p role="status">{zh ? "此 App 可按你的指令编辑选定工作区；不提高已直连 Agent 的原宿主权限。更改权限会建立新绑定和新会话，旧会话不会自动提升权限，已有 Agent 直连需重新授权。" : "This App may edit the selected workspace on your instruction; attached Agents retain their original host permissions. Changing access creates a new binding and Session. Existing Chat does not gain access and Agent targets require new authorization."}</p> : null}
    <div className="personal-detail-actions"><button disabled={busy || !app || !project || !executor || (role === "project" && executor === "codex" && projectGrant === "workspace_write" && projects.find(item => item.project_ref === project)?.grant === "workspace_read")} onClick={() => void act(() => connectPrivateConversation(app, project, executor, role, role === "project" && executor === "codex" ? projectGrant : "workspace_read"))} type="button">
      {busy ? (zh ? "正在核验" : "Verifying") : (zh ? "连接本人私聊" : "Connect owner private Chat")}</button>
      <button disabled={busy} onClick={() => void act(refresh)} type="button">{zh ? "刷新状态" : "Refresh status"}</button></div>
    <p>{zh ? "从手机发送文字开始；后续消息进入原会话队列。/status 查看工作区、角色与持久排队状态，/help 查看用法与解绑入口，/stop 停止当前聊天执行，/new 开启新会话。图片、文件会明确提示暂不支持。" : "Send text from your phone to begin; follow-ups queue in the same Session. /status shows the workspace, role and durable queue, /help explains commands and where to unbind, /stop stops the current Chat Turn, /new starts a new conversation. Images and files receive an explicit unsupported response."}</p>
    <p>{zh ? "管家新委托：/delegate --tokens N 具体目标。先读预览，再用原私聊的完整 /confirm 命令确认；15 分钟过期。原生执行保持只读，总 token 上限可能被运行中的请求超过；没有默认定时调度。回执提供 /stop-commission 停止和 /resume-commission 恢复命令；恢复保留原线程及累计用量。" : "Steward commission: /delegate --tokens N objective. Read the preview, then use its full /confirm command in the original private Chat within 15 minutes. Native execution remains read-only; in-flight requests can exceed the total token allowance. No default schedule. Receipts provide /stop-commission and /resume-commission commands; recovery retains the original thread and cumulative usage."}</p>
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
