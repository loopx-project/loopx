import {useEffect, useRef, useState} from "react";
import {configureAmendment, fetchAmendment, fetchAmendmentReceipt, savedRootRequestSchema, type AmendmentView, type RootRequest} from "../../data/goal-amendment";
import {useWorkspaceI18n} from "./i18n";

/** Operator setup and readback only. Peer adoption is recorded by the peer's
 * own operation; this page never sends an Agent id to impersonate it. */
export function GoalAmendmentSettings({goalId}: {goalId: string}) {
  const {locale} = useWorkspaceI18n();const zh = locale === "zh-CN";
  const [open, setOpen] = useState(false), [view, setView] = useState<AmendmentView | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null);
  const [text, setText] = useState(""), [action, setAction] = useState(""), [target, setTarget] = useState("");
  const [writes, setWrites] = useState(""), [capabilities, setCapabilities] = useState(""), [terminal, setTerminal] = useState("");
  const [agents, setAgents] = useState<string[]>([]), [limit, setLimit] = useState(1);
  const [preview, setPreview] = useState<RootRequest | null>(null);
  const generation = useRef(0), inFlight = useRef<number | null>(null);
  const [attempted, setAttempted] = useState(false);
  const [invalidSaved, setInvalidSaved] = useState(false);
  const key = `loopx-root-intent:${goalId}`;
  const lines = (value: string) => value.split("\n").map(v => v.trim()).filter(Boolean);
  const wording = (cn: string, en: string) => zh ? cn : en;

  async function read() {
    if (inFlight.current === generation.current) return;
    const current = generation.current;inFlight.current = current;setBusy(true);setError(null);
    try {
      const value = await fetchAmendment(goalId);
      if (current !== generation.current) return;
      setView(value);setAgents(value.peer_adoption.map(row => row.agent_id));
      setInvalidSaved(true);
      const raw = sessionStorage.getItem(key);
      if (raw) {
        const parsed = savedRootRequestSchema.safeParse(JSON.parse(raw));
        if (!parsed.success || parsed.data.request.goal_id !== goalId) {setInvalidSaved(true);throw new Error(wording("保存的操作信息不完整，无法安全恢复或创建新操作。", "Saved operation details are incomplete; recovery or a new operation cannot safely proceed."));}
        const saved = parsed.data;
        if (saved.request?.goal_id === goalId && typeof saved.request.preview_digest === "string") {
          setPreview(saved.request);setAttempted(saved.attempted === true);
          const parameters = saved.request.parameters, work = parameters.policy.templates[0]?.todo;
          setText(work?.text ?? "");setAction(work?.action_kind ?? "");setTarget(work?.target_key ?? "");
          setWrites(parameters.permission_scope.write_scopes.join("\n"));setCapabilities(parameters.permission_scope.capabilities.join("\n"));
          setTerminal(parameters.terminal_conditions.join("\n"));setAgents(parameters.policy.eligible_agents);setLimit(parameters.policy.max_created_todos);
        }
      }
      setInvalidSaved(false);
    } catch (cause) {
      if (current === generation.current) setError(cause instanceof Error ? cause.message : wording("无法读取目标变更状态。", "Goal amendment state is unavailable."));
    } finally {if (inFlight.current === current) inFlight.current = null;if (current === generation.current) setBusy(false);}
  }
  useEffect(() => {
    generation.current++;setView(null);setPreview(null);setAttempted(false);setInvalidSaved(false);setError(null);
    if (open) void read();
    return () => {generation.current++;};
    // The target, not a language toggle, owns this observation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goalId, open]);

  function edit(change: () => void) {
    if (attempted || invalidSaved) return;
    change();setPreview(null);sessionStorage.removeItem(key);setError(null);
  }
  async function submit(apply: boolean) {
    if (invalidSaved || inFlight.current === generation.current || !view || apply && !preview) return;
    const token = generation.current;inFlight.current = token;setBusy(true);setError(null);
    const request: RootRequest = apply ? preview! : {goal_id: goalId, goal_instance_id: view.goal_ref.goal_instance_id,
      operation_id: `root:${crypto.randomUUID()}`, parameters: {expected_provider_revision: view.provider_revision,
        permission_scope: {write_scopes: lines(writes), capabilities: lines(capabilities)}, terminal_conditions: lines(terminal),
        policy: {out_of_policy: "reject", eligible_agents: agents, max_created_todos: limit,
          templates: [{id: "owner-work", todo: {text: text.trim(), action_kind: action.trim(), target_key: target.trim(),
            required_write_scopes: lines(writes), required_capabilities: lines(capabilities)}}]}}};
    try {
      if (apply) {
        sessionStorage.setItem(key, JSON.stringify({request, attempted: true}));setAttempted(true);
      }
      const result = await configureAmendment(request, apply);
      if (token !== generation.current) return;
      if (!result.ok || !result.intent_digest) {
        if (result.reason_code === "goal_amendment_needs_rebase") {
          // The pinned old provider basis cannot become current again. This
          // definitive refusal permits a fresh preview; unknown outcomes do not.
          sessionStorage.removeItem(key);setPreview(null);setAttempted(false);
          const fresh = await fetchAmendment(goalId);
          if (token === generation.current) setView(fresh);
        }
        throw new Error(result.reason ?? wording("配置未被接纳。", "Configuration was not admitted."));
      }
      if (!apply) {
        const saved = {...request, preview_digest: result.intent_digest};
        sessionStorage.setItem(key, JSON.stringify({request: saved, attempted: false}));setPreview(saved);setAttempted(false);
      } else {
        sessionStorage.removeItem(key);setPreview(null);setAttempted(false);
        // Read the current owner state even when the write was historical replay.
        const fresh = await fetchAmendment(goalId);
        if (token === generation.current) setView(fresh);
      }
    } catch (cause) {
      if (token === generation.current) setError(cause instanceof Error ? cause.message : wording("响应未知，请读取当前结果或重试原操作。", "Response unknown; read back or retry the original operation."));
    } finally {if (inFlight.current === token) inFlight.current = null;if (token === generation.current) setBusy(false);}
  }

  async function recover() {
    if (!preview || inFlight.current === generation.current) return;
    const token = generation.current;inFlight.current = token;setBusy(true);setError(null);
    try {
      const found = await fetchAmendmentReceipt(preview);
      if (token !== generation.current) return;
      const receipt = found.receipts?.[0];
      if (found.status !== "found" || receipt?.operation_id !== preview.operation_id || receipt.result.intent_digest !== preview.preview_digest || receipt.result.goal_ref.goal_id !== preview.goal_id || receipt.result.goal_ref.goal_instance_id !== preview.goal_instance_id) {
        throw new Error(wording("暂未确认原操作回执；不能据此断言没有提交。请重试原操作。", "The original receipt is not confirmed; absence does not prove no commit. Retry the original operation."));
      }
      sessionStorage.removeItem(key);setPreview(null);setAttempted(false);
      const fresh = await fetchAmendment(goalId);
      if (token === generation.current) setView(fresh);
    } catch (cause) {if (token === generation.current) setError(cause instanceof Error ? cause.message : "Readback failed");}
    finally {if (inFlight.current === token) inFlight.current = null;if (token === generation.current) setBusy(false);}
  }

  return <details className="personal-cadence-settings" open={open} onToggle={e => setOpen(e.currentTarget.open)}>
    <summary>{wording("目标约束与受治理变更", "Goal commitments and governed changes")}</summary>
    {open ? <div className="personal-cadence-form">
      <p>{wording("先固定原始目标和允许的工作模板。Agent 可在授权范围内提出变更；变更记录不代表工作已执行或目标已完成。", "Freeze the original Goal and permitted work template. Agents may propose changes within this policy; a change record does not prove execution or Goal completion.")}</p>
      {view?.source_commitments ? <div className="personal-cadence-readback">
        <strong>{view.source_commitments.objective}</strong>
        <p>{wording("不包含：", "Excluded: ")}{view.source_commitments.non_goals.join("; ")}</p>
        <ul>{view.source_commitments.acceptance.map(value => <li key={value}>{value}</li>)}</ul>
        <p>{view.source_commitments.stop_condition}</p>
      </div> : null}
      {view && (!view.current_lifetime || !view.source_commitments || view.mutation_readiness.kind !== "admit") ? <p role="status">
        {wording("当前目标实例或原始声明不可用于初始化；请由目标生命周期负责人处理。历史结果不会授予当前执行权限。", "The current Goal lifetime or original declaration cannot be used for initialization. Its lifecycle owner must resolve this; historical results grant no current execution authority.")}
      </p> : null}
      {view?.intent ? <>
        <p>{wording(`已固定目标约束 · 版本 ${view.intent.goal_revision}`, `Goal commitments frozen · revision ${view.intent.goal_revision}`)}</p>
        <ul>{view.intent.policy.templates.map(row => <li key={row.id}>{row.todo.text}</li>)}</ul>
        <p>{wording("写入范围：", "Write scopes: ")}{view.intent.root_intent.permission_scope.write_scopes.join(", ") || wording("无", "None")}</p>
        <p>{wording("所需能力：", "Required capabilities: ")}{view.intent.root_intent.permission_scope.capabilities.join(", ") || wording("无", "None")}</p>
        <p>{wording("终止条件：", "Terminal conditions: ")}{view.intent.root_intent.terminal_conditions.join("; ")}</p>
        <h4>{wording("Agent 基线确认", "Agent basis acknowledgements")}</h4>
        <ul>{view.peer_adoption.map(row => <li key={row.agent_id}>{row.agent_id} · {row.current ? wording("当前版本确认已记录", "Current basis acknowledged") : wording("尚未确认当前版本", "Current basis not acknowledged")}</li>)}</ul>
        <p>{wording(`已创建工作 ${view.intent.created_todo_ids.length} 项；提案 ${view.intent.proposals.length} 项。`, `${view.intent.created_todo_ids.length} work items created; ${view.intent.proposals.length} proposals.`)}</p>
        <ul>{view.replan_observations.map(row => <li key={`${row.agent_id}:${row.obligation_id}`}>{row.agent_id} · {row.state === "settled" ? wording("对应 replan 已由原有规则验证", "Replan validated by its existing owner") : wording("仍待后续工作采纳与验证", "Work adoption and validation remain pending")}</li>)}</ul>
      </> : view?.current_lifetime && view.source_commitments && view.mutation_readiness.kind === "admit" ? <>
        <div className="personal-cadence-fields personal-capability-fields">
          <label>{wording("允许新增的工作", "Permitted work")}<textarea aria-label={wording("允许新增的工作", "Permitted work")} value={text} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setText(e.target.value))} /></label>
          <label>{wording("动作类型", "Action kind")}<input value={action} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setAction(e.target.value))} /></label>
          <label>{wording("稳定目标标识", "Stable target key")}<input value={target} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setTarget(e.target.value))} /></label>
          <label>{wording("写入范围，每行一项", "Write scopes, one per line")}<textarea aria-label={wording("写入范围，每行一项", "Write scopes, one per line")} value={writes} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setWrites(e.target.value))} /></label>
          <label>{wording("所需能力，每行一项", "Required capabilities, one per line")}<textarea aria-label={wording("所需能力，每行一项", "Required capabilities, one per line")} value={capabilities} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setCapabilities(e.target.value))} /></label>
          <label>{wording("终止条件，每行一项", "Terminal conditions, one per line")}<textarea aria-label={wording("终止条件，每行一项", "Terminal conditions, one per line")} value={terminal} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setTerminal(e.target.value))} /></label>
          <label>{wording("新增工作上限", "Created-work limit")}<input type="number" min={1} max={256} value={limit} disabled={busy || invalidSaved || Boolean(preview)} onChange={e => edit(() => setLimit(Number(e.target.value)))} /></label>
        </div>
        <fieldset className="personal-cadence-form" disabled={busy || invalidSaved || Boolean(preview)}><legend>{wording("允许提出变更的 Agent", "Agents allowed to propose changes")}</legend>
          {view.peer_adoption.map(row => <label className="personal-cadence-automation" key={row.agent_id}><input type="checkbox" checked={agents.includes(row.agent_id)} onChange={e => edit(() => setAgents(e.target.checked ? [...agents, row.agent_id] : agents.filter(a => a !== row.agent_id)))} />{row.agent_id}</label>)}
        </fieldset>
        {preview ? <p>{wording("将固定以上目标约束和预授权。完成后不能通过普通编辑替换。", "This freezes the Goal commitments and policy. Ordinary edits cannot replace them afterward.")}</p> : null}
        <div className="personal-cadence-actions">
          <button type="button" className="is-primary" disabled={busy || invalidSaved || attempted || !agents.length} onClick={() => void submit(Boolean(preview))}>
            {preview ? wording("确认固定目标与授权", "Freeze Goal and policy") : wording("检查本次授权", "Review this policy")}
          </button>
          {preview && !attempted ? <button type="button" disabled={busy} onClick={() => edit(() => {})}>{wording("返回修改", "Edit draft")}</button> : null}
        </div>
      </> : null}
      {attempted && preview ? <div className="personal-cadence-actions">
        <p>{wording("原操作结果尚待确认，保留原编号与内容进行恢复。", "The original outcome is pending; recovery retains its identity and content.")}</p>
        <button type="button" disabled={busy} onClick={() => void recover()}>{wording("查阅原操作回执", "Read original receipt")}</button>
        {view?.goal_ref.goal_instance_id === preview.goal_instance_id ? <button type="button" disabled={busy} onClick={() => void submit(true)}>{wording("重试原操作", "Retry original operation")}</button> : null}
      </div> : null}
      {error ? <p role="alert" className="personal-machine-error">{error}</p> : null}
      <button type="button" disabled={busy} onClick={() => void read()}>{wording("读取当前结果", "Read current result")}</button>
      {busy ? <p role="status">{wording("正在读取或提交…", "Reading or submitting…")}</p> : null}
    </div> : null}
  </details>;
}
