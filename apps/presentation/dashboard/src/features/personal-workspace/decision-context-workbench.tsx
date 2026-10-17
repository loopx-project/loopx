import {useEffect, useRef, useState} from "react";
import {inspectDecisionContext, recallDecisionContext, type DecisionContextActivation, type DecisionContextRecall} from "../../data/decision-context";
import {useWorkspaceI18n} from "./i18n";
import {memoryRetrievalLabel} from "./memory-settings";
import "./content-reference-workbench.css";

/** Explicit per-page profile copy; no browser storage or durable binding. */
export function DecisionContextWorkbench({goalId, active = true}: Readonly<{goalId: string; active?: boolean}>) {
  const {locale} = useWorkspaceI18n();
  const copy = (cn: string, en: string) => locale === "zh-CN" ? cn : en;
  const [profile, setProfile] = useState<Record<string, unknown> | null>(null);
  const [agent, setAgent] = useState("");
  const [scope, setScope] = useState("");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(4);
  const [activation, setActivation] = useState<DecisionContextActivation | null>(null);
  const [result, setResult] = useState<DecisionContextRecall | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  const pending = useRef<AbortController | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const importGeneration = useRef(0);
  const recallReady = activation?.available && activation.context_provider_configured;

  function invalidate() {
    generation.current += 1;
    pending.current?.abort(); pending.current = null;
    setResult(null); setError(""); setBusy(false);
  }
  useEffect(() => () => {generation.current += 1; importGeneration.current += 1; pending.current?.abort();}, []);
  useEffect(() => {
    if (!active && pending.current) {
      generation.current += 1; pending.current.abort(); pending.current = null; setBusy(false);
    }
  }, [active]);

  useEffect(() => {
    let current = true;
    const controller = new AbortController();
    setActivation(null); setResult(null); setError(""); setBusy(false);
    if (!profile || !agent.trim()) return () => controller.abort();
    void inspectDecisionContext({goal_id: goalId, agent_id: agent, profile}, controller.signal)
      .then(value => {if (current) setActivation(value);})
      .catch(() => {if (current) setError(copy("配置检查失败，请重试。", "Profile inspection failed. Retry."));});
    return () => {current = false; controller.abort();};
  }, [profile, agent, goalId]);

  function clear({keepFile = false, keepQuery = false} = {}) {
    importGeneration.current += 1; invalidate(); setProfile(null); setAgent("");
    setActivation(null); if (!keepQuery) setQuery(""); setScope(""); setLimit(4);
    if (!keepFile && input.current) input.current.value = "";
  }
  async function importProfile(file?: File) {
    clear({keepFile: true, keepQuery: true}); const revision = importGeneration.current;
    if (!file) return;
    try {
      if (file.size > 240 * 1024) throw new Error();
      const value: unknown = JSON.parse(await file.text());
      if (revision !== importGeneration.current) return;
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error();
      const next = value as Record<string, unknown>;
      const agents = Array.isArray(next.enabled_agents) ? next.enabled_agents.filter((item): item is string => typeof item === "string") : [];
      const context = next.context_provider && typeof next.context_provider === "object" ? next.context_provider as Record<string, unknown> : {};
      setProfile(next); setAgent(agents[0] ?? "");
      setScope(typeof context.scope_ref === "string" ? context.scope_ref : "goal");
      setLimit(typeof context.max_results === "number" ? context.max_results : 4);
    } catch {
      if (revision === importGeneration.current) setError(copy("无法导入 profile。请使用不超过 240 KiB 的 JSON 对象。", "Cannot import profile. Use a JSON object up to 240 KiB."));
    }
  }
  async function recall() {
    if (!active || pending.current || busy || !profile || !recallReady || !query.trim()) return;
    invalidate(); const revision = generation.current;
    const controller = new AbortController(); pending.current = controller; setBusy(true);
    try {
      const value = await recallDecisionContext({goal_id: goalId, agent_id: agent, profile, query, context_scope_ref: scope, max_results: limit}, controller.signal);
      if (revision === generation.current) setResult(value);
    } catch {
      if (revision === generation.current) setError(copy("召回失败。配置或服务恢复后可重试。", "Recall failed. Restore the profile or service and retry."));
    } finally {if (revision === generation.current) {setBusy(false); pending.current = null;}}
  }
  const status = result?.status ?? activation?.status;
  const reason = result?.reason_code ?? activation?.reason_code;
  const statusCopy: Record<string, string> = {
    available: copy("配置检查通过，可以尝试召回", "Profile checks passed; recall can be attempted"),
    disabled: copy("本页召回已停用", "Recall disabled in this page"),
    agent_not_enabled: copy("此 Agent 未在 profile 中启用", "Agent is not enabled by the profile"),
    profile_invalid: copy("Profile 无效或与当前 Goal 不符", "Profile invalid or outside the selected Goal"),
    provider_unavailable: copy("Provider 暂不可用", "Provider unavailable"),
    unavailable: copy("Provider 暂不可用", "Provider unavailable"),
    failed: copy("Provider 召回失败，可恢复后重试", "Provider recall failed; restore and retry"),
    completed: copy("召回完成", "Recall completed"),
  };
  const reasonCopy: Record<string, string> = {
    scope_mismatch: copy("本次 Scope 与语料不匹配", "This scope does not match the corpus"),
    corpus_unavailable: copy("语料缺失或不可读，恢复后可重试", "Corpus missing or unreadable; restore and retry"),
    context_provider_not_configured: copy("Profile 尚未配置召回 provider", "No recall provider configured in this profile"),
  };
  const context = profile?.context_provider && typeof profile.context_provider === "object"
    ? profile.context_provider as Record<string, unknown> : null;
  return <section className="personal-capability-field-summary personal-content-reference personal-decision-context" aria-label={copy("资料召回", "Find evidence")}>
    <label>{copy("选择私有检索配置（JSON）", "Choose a private retrieval profile (JSON)")}<input ref={input} type="file" accept=".json,application/json" onChange={event => void importProfile(event.target.files?.[0])} /></label>
    <p>{copy("使用配置副本；仅发给本机服务。查询在你点击召回后才发给检索器。离开“记忆与资料”、切换 Goal 或清除时移除副本与结果。", "A profile copy is sent only to this local service. Your query reaches the provider only when you recall. Leaving Memory & sources, changing Goal or clearing removes the copy and results.")}</p>
    {!profile ? <p>{copy("尚未选择配置，不会调用检索器。", "No profile selected; no provider calls.")}</p> : <>
      <p className="decision-context-connection">{copy("检索方式", "Retrieval")}: <strong>{memoryRetrievalLabel(context?.provider, locale)}</strong>{" · "}{copy("Agent", "Agent")}: {agent || "—"}</p>
      <label>{copy("要找什么证据？", "What evidence do you need?")}<textarea rows={3} maxLength={1000} value={query} onChange={event => {invalidate(); setQuery(event.target.value);}} /></label>
      <details><summary>{copy("本次配置与检索范围", "Profile and retrieval scope")}</summary>
        <label>{copy("Agent", "Agent")}<input value={agent} maxLength={200} onChange={event => {invalidate(); setActivation(null); setAgent(event.target.value);}} /></label>
        <label><input type="checkbox" checked={profile.enabled === true} onChange={event => {invalidate(); setActivation(null); setProfile({...profile, enabled: event.target.checked});}} />{copy("允许本页查询", "Allow queries in this page")}</label>
        <label>{copy("资料范围", "Source scope")}<input value={scope} maxLength={2048} onChange={event => {invalidate(); setScope(event.target.value);}} /></label>
        <label>{copy("最多结果数", "Maximum results")}<input type="number" min={1} max={8} value={limit} onChange={event => {invalidate(); setLimit(Number(event.target.value));}} /></label>
      </details>
      <div className="personal-capability-actions">
        <button className="is-primary" type="button" disabled={busy || !recallReady || !query.trim() || !scope.trim()} onClick={() => void recall()}>{busy ? copy("召回中…", "Recalling…") : copy("召回证据", "Recall evidence")}</button>
        <button type="button" onClick={() => {invalidate(); setProfile({...profile});}}>{copy("重新检查配置", "Recheck profile")}</button>
      </div>
    </>}
    {status ? <p role="status">{!result && activation?.available && !activation.context_provider_configured
      ? reasonCopy.context_provider_not_configured
      : reason && reasonCopy[reason] ? reasonCopy[reason] : statusCopy[status] ?? copy("状态未知，请重新检查配置", "State unknown; recheck the profile")}</p> : null}
    {error ? <p role="alert" className="personal-machine-error">{error}</p> : null}
    {result?.ok ? <section aria-label={copy("召回结果", "Recall results")} aria-live="polite">
      <p>{result.results.length} {copy("条结果", "results")} · {memoryRetrievalLabel(result.provider, locale)} · {result.observed_at}</p>
      <small>{copy("分数只用于该 provider 内的排序，不代表置信度。", "Scores rank hits within this provider; they are not confidence.")}</small>
      {!result.results.length ? <p>{copy("没有匹配证据。可修改问题或本次检索范围。", "No matching evidence. Refine your question or retrieval scope.")}</p> : null}
      {result.results.map(hit => <article key={hit.provider_ref} className="personal-capability-linked-setting"><div>
        <strong>{hit.summary}</strong><p className="decision-context-hit">{hit.content}</p>
        {hit.score !== null ? <small>{copy("相关度", "Relevance")}: {hit.score.toPrecision(3)}</small> : null}
      </div></article>)}
    </section> : null}
    <p><small>{copy("召回内容是不可信的参考材料。当前操作不写入原 profile、记忆、游标或 Goal，不启用自动 hook，也不授予执行权限。", "Retrieved content is untrusted advisory material. This operation does not write the original profile, memory, cursors or Goal, enable automatic hooks, or grant execution authority.")}</small></p>
    {reason ? <details className="personal-capability-help"><summary>{copy("诊断详情", "Diagnostics")}</summary><code>{status} · {reason}</code></details> : null}
    <div className="personal-capability-actions"><button type="button" onClick={() => clear()}>{copy("清除临时配置与结果", "Clear temporary profile and results")}</button></div>
  </section>;
}
