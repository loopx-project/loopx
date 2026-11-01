import { useCallback, useEffect, useRef, useState } from "react";
import { z } from "zod";
import { ChatApiError, requestJson } from "../../data/chat";
import { ContextHelp } from "./context-help";
import { useWorkspaceI18n } from "./i18n";

const statusSchema = z.object({
  ok: z.literal(true), available: z.literal(true), revision: z.number().int().nonnegative(),
  balance_available: z.literal(false),
  keys: z.array(z.object({
    id: z.string(), label: z.string(), masked_key: z.string(),
    check_status: z.enum(["not_checked", "models_readable", "authentication_failed", "permission_denied", "rate_limited", "unavailable"]),
    checked_at: z.number().nullable(),
    runtime: z.object({ state: z.enum(["accepted", "quota_exhausted", "authentication_failed", "permission_denied", "rate_limited", "failed"]),
      observed_at: z.number(), model: z.string(), remaining_requests: z.number().optional(), remaining_tokens: z.number().optional(),
    }).nullable(),
  })),
});
type KeyStatus = z.infer<typeof statusSchema>;

const copy = {
  "zh-CN": {
    title: "OpenAI API Key", configured: "已保存", boundary: "订阅账号优先，全部耗尽后使用 API",
    help: "API Key 与额度", detail: "密钥只保存在本机，不回显完整值。启用 API 接续后，只有订阅账号均被确认耗尽才使用 API；订阅恢复后在安全边界回到订阅。API 额度或权限错误会在输出前尝试下一把 Key，保留当前模型，不重放已开始输出的请求。检查连接只读取模型列表，不能证明可消费额度。项目 Key 无法查询账单余额，费用查询需另有 Admin Key。",
    auto: "订阅耗尽后使用 API（按用量计费）", autoOff: "需先启用下方的订阅自动切换", ownerMissing: "原生槽位管理器需要更新，暂不能启用 API 接续",
    lastRequest: "上次响应", accepted: "API 已接受请求", quota_exhausted: "API 额度不足", failed: "API 请求失败", tokens: "限流窗口 Token 余量", requests: "限流窗口请求余量",
    apiPermission: "API 模型权限不足", apiLimited: "API 请求限流",
    balance: "API 余额不可读取", billing: "查看 OpenAI 用量", add: "添加 Key", save: "保存 Key", cancel: "取消",
    name: "名称", key: "API Key", check: "检查连接", remove: "移除", reload: "刷新列表",
    loading: "正在读取 Key 配置", empty: "尚未添加 Key", failure: "配置暂时无法读取，请重试。",
    uncertain: "操作结果未确认，已重新读取列表。请先核对结果再操作。",
    last: "上次检查", not_checked: "未检查", models_readable: "模型列表读取成功",
    authentication_failed: "认证失败，请移除并更新 Key", permission_denied: "无模型列表读取权限",
    rate_limited: "检查被限流，余额仍未知", unavailable: "连接检查失败，可重试",
    configuration_changed: "配置已在别处更新，列表已刷新，请核对后重试。", duplicate_api_key: "这把 Key 已经保存。",
    invalid_api_key: "请提供有效的 OpenAI 项目 Key，不接受 Admin Key。", invalid_key_label: "名称应为 1–80 个字符，不能包含密钥或换行。",
    key_limit_reached: "最多保存 16 把 Key。", key_not_found: "这把 Key 已被移除，列表已刷新。",
  },
  en: {
    title: "OpenAI API keys", configured: "saved", boundary: "Subscriptions first; API only after all subscriptions are exhausted",
    help: "API keys and quota", detail: "Keys stay on this machine and are never returned in full. API fallback is admitted only after every subscription is confirmed exhausted. Recovered subscriptions take priority at a safe handoff boundary. API quota or permission errors can try the next key before output, preserving the model. Requests that started output are never replayed. Connection checks only read the model list and do not establish spendable quota. Project keys cannot query billing balance; the Costs API requires a separate Admin key.",
    auto: "Use API after subscription exhaustion (usage based billing)", autoOff: "Enable subscription automatic handoff below first", ownerMissing: "The native slot manager needs an update before API fallback can be enabled",
    lastRequest: "Last response", accepted: "API accepted the request", quota_exhausted: "API quota exhausted", failed: "API request failed", tokens: "Rate-window tokens left", requests: "Rate-window requests left",
    apiPermission: "API model permission denied", apiLimited: "API request rate limited",
    balance: "API balance unavailable", billing: "View OpenAI usage", add: "Add key", save: "Save key", cancel: "Cancel",
    name: "Name", key: "API key", check: "Check connection", remove: "Remove", reload: "Refresh list",
    loading: "Reading key configuration", empty: "No keys added", failure: "Key configuration is unavailable. Retry the read.",
    uncertain: "The operation result was not confirmed. The list was read again; inspect it before another operation.",
    last: "Last checked", not_checked: "Not checked", models_readable: "Model list read successfully",
    authentication_failed: "Authentication failed; remove and replace this key", permission_denied: "Model list read permission denied",
    rate_limited: "Check rate limited; balance is still unknown", unavailable: "Connection check failed; retry is available",
    configuration_changed: "Configuration changed elsewhere. The list was refreshed; inspect it before retrying.", duplicate_api_key: "This key is already saved.",
    invalid_api_key: "Provide an OpenAI project key. Admin keys are not accepted.", invalid_key_label: "Use a name of 1–80 characters without keys or line breaks.",
    key_limit_reached: "At most 16 keys can be saved.", key_not_found: "This key was removed. The list was refreshed.",
  },
};

export function OpenAIKeySettings({ automatic, supported, apiEnabled, onToggle, nativeBusy = false }: {
  automatic: boolean; supported: boolean | null; apiEnabled: boolean; onToggle: (enabled: boolean) => void; nativeBusy?: boolean;
}) {
  const { locale } = useWorkspaceI18n();
  const c = copy[locale];
  const [status, setStatus] = useState<KeyStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const generation = useRef(0);
  const endpoint = "/api/chat/codex-accounts/keys";
  const read = useCallback(async (clearError = false) => {
    const request = ++generation.current;
    try {
      const result = statusSchema.parse(await requestJson<unknown>(endpoint));
      if (request === generation.current) {
        setStatus(result);
        setError(previous => clearError || previous === "read" ? null : previous);
      }
    } catch { if (request === generation.current) {setStatus(null); setError("read");} }
  }, []);
  useEffect(() => { void read(); }, [read]);
  useEffect(() => {
    if (busy) return;
    const timer = window.setInterval(() => { if (!document.hidden) void read(); }, 5000);
    return () => window.clearInterval(timer);
  }, [busy, read]);

  async function act(operation: string, parameters: Record<string, unknown>) {
    if (!status) return;
    generation.current += 1;
    setBusy(true);
    setError(null);
    try {
      const next = statusSchema.parse(await requestJson<unknown>(endpoint, {
        method: "POST", body: JSON.stringify({ operation, parameters: { expected_revision: status.revision, ...parameters } }),
      }));
      setStatus(next);
      if (operation === "keys-add") { setAdding(false); setName(""); }
    } catch (failure) {
      // Read back a possibly completed write; never replay a lost response.
      await read();
      const code = failure instanceof ChatApiError ? failure.payload.error_code : null;
      setError(typeof code === "string" && ["configuration_changed", "duplicate_api_key", "invalid_api_key", "invalid_key_label", "key_limit_reached", "key_not_found"].includes(code) ? code : "write");
    } finally { setBusy(false); }
  }

  return <section className="personal-codex-accounts openai-key-settings" data-testid="openai-key-settings" aria-busy={busy}>
    <header><h2>{c.title}</h2><span>{status ? `${status.keys.length} ${c.configured}` : c.loading}</span>
      <ContextHelp label={c.help}><p>{c.detail}</p></ContextHelp></header>
    <p className="codex-account-effect">{c.boundary}</p>
    <label className="codex-account-enable"><input type="checkbox" checked={apiEnabled}
      disabled={busy || nativeBusy || !automatic || !supported || !status?.keys.length}
      onChange={event => onToggle(event.target.checked)} /><span>{c.auto}</span></label>
    {supported === false ? <p className="codex-account-effect">{c.ownerMissing}</p> : !automatic ? <p className="codex-account-effect">{c.autoOff}</p> : null}
    {status ? <>
      <div className="codex-account-list">
        {status.keys.map(row => <article key={row.id}>
          <div className="codex-account-name"><strong>{row.label}</strong><span>{row.masked_key}</span></div>
          <span>{c[row.check_status]}</span>
          {row.runtime ? <><span>{row.runtime.state === "permission_denied" ? c.apiPermission : row.runtime.state === "rate_limited" ? c.apiLimited : c[row.runtime.state]} · {row.runtime.model}</span>
            {row.runtime.remaining_requests != null || row.runtime.remaining_tokens != null ? <span>
              {row.runtime.remaining_requests != null ? `${c.requests} ${row.runtime.remaining_requests}` : ""}
              {row.runtime.remaining_tokens != null ? ` · ${c.tokens} ${row.runtime.remaining_tokens}` : ""}</span> : null}
            <span>{c.lastRequest} {new Date(row.runtime.observed_at * 1000).toLocaleString(locale)}</span></> : null}
          {row.checked_at != null ? <span>{c.last} {new Date(row.checked_at * 1000).toLocaleString(locale)}</span> : null}
          <div className="codex-account-actions">
            <button type="button" disabled={busy} onClick={() => void act("keys-check", { key_id: row.id })}>{c.check}</button>
            <button type="button" disabled={busy} onClick={() => void act("keys-remove", { key_id: row.id })}>{c.remove}</button>
          </div>
        </article>)}
      </div>
      {!status.keys.length ? <p>{c.empty}</p> : null}
      {adding ? <form onSubmit={event => {
        event.preventDefault();
        const submitted = key;
        setKey("");
        void act("keys-add", { label: name, api_key: submitted });
      }}>
        <label>{c.name}<input value={name} onChange={event => setName(event.target.value)} maxLength={80} required disabled={busy} /></label>
        <label>{c.key}<input type="password" value={key} onChange={event => setKey(event.target.value)} autoComplete="off" spellCheck={false}
          minLength={23} maxLength={512} required disabled={busy} /></label>
        <div className="codex-account-actions"><button type="submit" disabled={busy}>{c.save}</button>
          <button type="button" disabled={busy} onClick={() => {setAdding(false); setKey(""); setName("");}}>{c.cancel}</button></div>
      </form> : <button className="openai-add-key" type="button" disabled={busy || status.keys.length >= 16} onClick={() => setAdding(true)}>{c.add}</button>}
      <div className="codex-account-actions"><span>{c.balance}</span><a href="https://platform.openai.com/usage" target="_blank" rel="noreferrer">{c.billing}</a></div>
    </> : null}
    {error ? <><p role="alert">{error === "write" ? c.uncertain : error === "read" ? c.failure : c[error as keyof typeof c]}</p>
      <button type="button" disabled={busy} onClick={() => void read(true)}>{c.reload}</button></> : null}
  </section>;
}
