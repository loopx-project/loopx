import { useCallback, useEffect, useState } from "react";
import { RefreshCw, CheckCircle2, Circle, AlertCircle } from "lucide-react";
import { requestJson } from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";
import { ContextHelp } from "./context-help";
import "./codex-account-settings.css";

type AccountStatus = {
  ok: boolean; available: boolean; code?: string | null; revision?: number;
  active_slot?: string | null; selection_revision?: string; action?: string;
  target?: string | null; recovery_required?: boolean; launch_failed?: boolean;
  policy?: { enabled: boolean; slots: string[]; cooldown_seconds: number; restart_clients?: boolean;
    force_quit_clients?: boolean };
  slots?: string[]; last_checked_at?: number | null; last_switch_at?: number | null;
  accounts?: Array<{ slot: string; availability: string; observed_at: number;
    primary_used: number | null; secondary_used: number | null; reset_at: number | null;
    credits_available: boolean | null }>;
};

const copy = {
  "zh-CN": {
    title: "Codex 原生账号", enabled: "自动接续已启用", disabled: "自动接续已关闭",
    enable: "额度耗尽后自动切换", refresh: "检查额度", retry: "重新连接",
    selectionHelp: "自动选择顺序", selectionDetail: "优先选择剩余额度比例最多的可用账号；短、长周期取剩余较少的一项。平手从当前账号之后按已登记顺序选择。这里比较的是额度百分比，无法获知绝对剩余 token。当前账号可用时不会主动切换。",
    effect: "仅使用下方已登记账号。额度耗尽后正常退出 GPT 专用客户端，保存登录态并切换，再重新打开原来的界面。",
    effectWait: "仅使用下方已登记账号。额度耗尽后等待客户端关闭，再保存登录态、切换账号并重新打开。",
    restart: "允许自动退出并重开 GPT 客户端", restarting: "正在退出 GPT、切换账号并重新打开",
    forceQuit: "正常退出失败时自动强制关闭 GPT，无需再次确认",
    effectForce: "仅使用下方已登记账号。额度耗尽后先正常退出 GPT；退出失败时自动强制结束该实例，再切换账号并重新打开。强制退出会中断该实例的当前工作。",
    reopen: "重新打开 GPT", other_clients_busy: "还有其他进程占用 GPT 登录；关闭占用后检查额度重试",
    client_quit_refused: "GPT 未接受正常退出；本次未换号，自动尝试已暂停", client_quit_timeout: "GPT 尚未退出；本次未换号，自动尝试已暂停",
    client_changed: "GPT 进程已变化；已暂停本次切换", handoff_pending: "独立切换进程正在完成接续，请稍候",
    handoff_interrupted: "接续进程中断；请检查并恢复本次切换", quota_stale: "额度记录已过期，正在重新检查",
    current: "当前", next: "下一账号", slot: "账号", primary: "短周期剩余", secondary: "长周期剩余", unread: "未读取",
    select: "切换到此账号", checking: "正在检查额度", ready: "当前账号可用", selected: "账号已切换",
    waiting_for_idle: "已选好候选账号，等待 Codex 客户端关闭", selection_changed: "账号选择已变化，已暂停本次切换",
    auth_required: "登录需要更新，请在原生 Codex 完成登录", quota_unknown: "暂时无法确认额度，将稍后重试",
    cooldown: "切换后冷却中", all_unavailable: "暂时没有可用账号，等待额度恢复", target_unavailable: "所选账号当前不可用",
    recovery_required: "上次切换未完成，需要恢复", unavailable: "原生账号暂不可用", adapter_changed: "槽位管理器已更新，请重新连接",
    recover: "恢复未完成的切换", rollback: "撤销最近一次切换", available: "可用", exhausted: "额度已用完",
    unknown: "额度未知", credits: "另有可用额度", help: "运行与恢复说明", last: "上次检查",
    detail: "检测不会调用模型、购买额度或使用重置券。401 登录失效先由原生客户端刷新；其他账号可通过官方 CLI 单独刷新。查询失败不会被判为额度耗尽。自动退出仅针对 GPT 专用实例；启用强制关闭后，正常退出失败会自动结束同一个已核验实例。其他 CLI/文件占用仍会暂停切换。独立进程完成切换和重开，即使发起切换的 Codex 已退出。切换保留模型设置和会话历史，不重放已提交工作。自动观察随 LoopX 服务运行。",
    extension_uninstalled: "尚未安装 Codex 账号扩展。安装 loopx-codex-provider-routing 后重新启动 LoopX 服务。",
    adapter_unavailable: "未发现已安装的 Personal Apps 槽位管理器和 Codex CLI。",
    native_unavailable: "无法连接原生槽位管理器，请检查本机安装后重试。",
    launchFailed: "账号已切换，但界面未能重新打开。可以从原来的 GPT 入口打开；请勿重复切换。",
    failure: "操作未完成。刷新状态后可以重试。", recoveryHint: "恢复会还原未完成事务；撤销会还原最近一次切换。两者都需要客户端关闭，且不会覆盖后续登录修改。",
  },
  en: {
    title: "Native Codex accounts", enabled: "Automatic handoff enabled", disabled: "Automatic handoff off",
    enable: "Switch automatically when quota runs out", refresh: "Check quota", retry: "Reconnect",
    selectionHelp: "Automatic selection order", selectionDetail: "Prefer the available account with the most remaining allowance, using the lower remaining percentage across its reported windows. Ties follow the registered order after the current account. These percentages do not establish absolute tokens left. An available current account is not preempted.",
    effect: "Use only the registered accounts below. After exhaustion, quit the dedicated GPT client normally, save the login, switch accounts, then reopen the same interface.",
    effectWait: "Use only the registered accounts below. After exhaustion, wait for clients to close, save the login, switch accounts, then reopen the same interface.",
    restart: "Allow automatic GPT client quit and restart", restarting: "Quitting GPT, switching accounts and reopening",
    forceQuit: "Force quit GPT if normal quit fails, without another confirmation",
    effectForce: "Use only the registered accounts below. After exhaustion, try normal quit first; if it fails, force quit that GPT instance, switch accounts and reopen. Force quit interrupts work in that instance.",
    reopen: "Reopen GPT", other_clients_busy: "Another process still owns the GPT login; close it and check quota to retry",
    client_quit_refused: "GPT refused normal quit; no account changed and automatic retries are paused", client_quit_timeout: "GPT has not exited; no account changed and automatic retries are paused",
    client_changed: "The GPT process changed; this switch was paused", handoff_pending: "The independent handoff is still finishing; please wait",
    handoff_interrupted: "The handoff process stopped; inspect and recover this switch", quota_stale: "Quota observations expired; checking again",
    current: "Current", next: "Next account", slot: "Account", primary: "Short window left", secondary: "Long window left", unread: "Not read",
    select: "Switch to this account", checking: "Checking quota", ready: "Current account is available", selected: "Account switched",
    waiting_for_idle: "Candidate selected; waiting for Codex clients to close", selection_changed: "Account selection changed; this switch was paused",
    auth_required: "Sign in again in native Codex", quota_unknown: "Quota could not be confirmed; retrying later",
    cooldown: "Cooling down after switching", all_unavailable: "No account is available yet; waiting for quota to recover", target_unavailable: "Selected account is unavailable",
    recovery_required: "An interrupted switch needs recovery", unavailable: "Native accounts unavailable", adapter_changed: "The slot manager changed; reconnect to it",
    recover: "Recover interrupted switch", rollback: "Undo last switch", available: "Available", exhausted: "Quota exhausted",
    unknown: "Quota unknown", credits: "Additional allowance available", help: "Operation and recovery", last: "Last checked",
    detail: "Checks do not call a model, buy credits or use reset coupons. Native Codex refreshes an expired active login; other accounts can refresh separately through the official CLI. A failed check never means exhausted quota. Automatic quit targets only the dedicated GPT instance. With force quit enabled, failed normal quit ends that same verified instance automatically. Another CLI/file owner still pauses switching. An independent process completes switching and reopening even when the calling Codex exits. Model settings and history stay intact; submitted work is never replayed. Observation runs with the LoopX service.",
    extension_uninstalled: "Install the optional loopx-codex-provider-routing package, then restart the LoopX service.",
    adapter_unavailable: "The installed Personal Apps slot manager and Codex CLI were not found.",
    native_unavailable: "Cannot connect to the native slot manager. Check the local installation and retry.",
    launchFailed: "The account switched, but the interface could not reopen. Open the original GPT entry; do not repeat the switch.",
    failure: "The operation did not finish. Refresh its status before retrying.", recoveryHint: "Recovery restores an interrupted transaction; undo restores the last switch. Both require closed clients and refuse to overwrite later login changes.",
  },
};

export function CodexAccountSettings() {
  const { locale } = useWorkspaceI18n();
  const c = copy[locale];
  const [status, setStatus] = useState<AccountStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const endpoint = "/api/chat/codex-accounts";
  const label = (key: string | null | undefined) => c[key as keyof typeof c] ?? c.unavailable;
  const read = useCallback(async () => {
    try {
      const result = await requestJson<AccountStatus>(endpoint);
      setStatus(result);
      setError(false);
    } catch { setError(true); }
  }, []);
  useEffect(() => {
    void read();
    const timer = window.setInterval(() => { if (!document.hidden) void read(); }, 5000);
    return () => window.clearInterval(timer);
  }, [read]);
  async function act(operation: string, parameters = {}) {
    setBusy(true);
    setError(false);
    try {
      const result = await requestJson<AccountStatus>(endpoint, {
        method: "POST", body: JSON.stringify({ operation, parameters }),
      });
      setStatus(result);
    } catch { await read(); setError(true); }
    finally { setBusy(false); }
  }
  const enabled = status?.policy?.enabled === true;
  const restartClients = status?.policy?.restart_clients === true;
  const forceQuitClients = status?.policy?.force_quit_clients === true;
  const problem = status?.code || (status?.ok === false ? "native_unavailable" : null);
  return <section className="personal-codex-accounts" data-testid="codex-account-settings" aria-busy={busy}>
    <header><h2>{c.title}</h2><span>{status?.active_slot ? `${c.current} ${status.active_slot.toUpperCase()} · ` : ""}{enabled ? c.enabled : c.disabled}</span></header>
    {!status ? <p role="status">{c.checking}</p> : null}
    {(problem && problem !== status?.action) || status?.available === false ? <p role="alert">{label(problem)}</p> : null}
    {status?.available && status.slots?.length ? <>
      <p className="codex-account-effect">{restartClients ? forceQuitClients ? c.effectForce : c.effect : c.effectWait}</p>
      <div className="codex-account-enable"><label><input type="checkbox" checked={enabled} disabled={busy}
        onChange={event => void act("configure", { expected_revision: status.revision, enabled: event.target.checked,
          slots: status.slots })} /><span>{c.enable}</span></label>
        <ContextHelp label={c.selectionHelp}><p>{c.selectionDetail}</p></ContextHelp>
      </div>
      <label className="codex-account-enable"><input type="checkbox" checked={restartClients} disabled={busy}
        onChange={event => void act("configure", { expected_revision: status.revision, enabled,
          restart_clients: event.target.checked, force_quit_clients: event.target.checked && forceQuitClients,
          slots: status.slots })} /><span>{c.restart}</span></label>
      {restartClients ? <label className="codex-account-enable"><input type="checkbox" checked={forceQuitClients} disabled={busy}
        onChange={event => void act("configure", { expected_revision: status.revision, enabled,
          restart_clients: true, force_quit_clients: event.target.checked, slots: status.slots })} /><span>{c.forceQuit}</span></label> : null}
      <div className="codex-account-state" role="status" aria-live="polite">
        {status.action === "ready" || status.action === "selected" ? <CheckCircle2 size={17} aria-hidden />
          : <Circle size={17} aria-hidden />}
        <span>{label(status.recovery_required ? "recovery_required" : status.action)}</span>
      </div>
      <div className="codex-account-list">
        {status.slots.map(slot => {
          const row = status.accounts?.find(a => a.slot === slot);
          // Expired observations remain unknown in the UI, just as in policy.
          const age = row ? Date.now() / 1000 - row.observed_at : null;
          const fresh = age != null && age >= 0 && age <= 120;
          const current = status.active_slot === slot;
          const remaining = (used: number | null | undefined) => fresh && used != null ? `${100 - used}%` : "—";
          return <article className={current ? "is-current" : ""} key={slot}>
            <div className="codex-account-name"><strong>{c.slot} {slot.toUpperCase()}</strong>
              <span>{current ? c.current : status.target === slot ? c.next : label(row?.availability === "auth_required" ? "auth_required" : fresh ? row?.availability : "unknown")}</span></div>
            <dl><div><dt>{c.primary}</dt><dd>{remaining(row?.primary_used)}</dd></div>
              <div><dt>{c.secondary}</dt><dd>{remaining(row?.secondary_used)}</dd></div></dl>
            {fresh && row?.credits_available ? <span>{c.credits}</span> : null}
            {!fresh ? <span>{c.unread}</span> : null}
            {!current ? <button type="button" disabled={busy || Boolean(status.recovery_required) || (!status.policy?.slots.includes(slot) && Boolean(status.policy?.slots.length))}
              onClick={() => void act("switch", { manual_slot: slot, expected_selection: status.selection_revision,
                operation_id: crypto.randomUUID() })}>{c.select}</button> : null}
          </article>;
        })}
      </div>
      {status.launch_failed ? <><p role="alert">{c.launchFailed}</p><button type="button" disabled={busy}
        onClick={() => void act("reopen", { expected_selection: status.selection_revision })}>{c.reopen}</button></> : null}
      <div className="codex-account-actions"><button type="button" disabled={busy}
        onClick={() => void act("refresh")}><RefreshCw aria-hidden size={16} />{c.refresh}</button>
        {status.last_checked_at ? <span>{c.last} {new Date(status.last_checked_at * 1000).toLocaleTimeString(locale)}</span> : null}
      </div>
      <details><summary>{c.help}</summary><p>{c.detail}</p><p>{c.recoveryHint}</p>
        <div className="codex-account-actions"><button type="button" disabled={busy || !status.recovery_required}
          onClick={() => void act("recover", { expected_revision: status.revision, expected_selection: status.selection_revision })}>{c.recover}</button>
          <button type="button" disabled={busy || !status.last_switch_at}
            onClick={() => void act("rollback", { expected_revision: status.revision, expected_selection: status.selection_revision })}>{c.rollback}</button></div>
      </details>
    </> : null}
    {error ? <p role="alert"><AlertCircle aria-hidden size={16} />{c.failure}</p> : null}
    {status?.code === "adapter_changed" || error ? <button type="button" disabled={busy} onClick={() => void act("configure", {
      expected_revision: status?.revision, enabled, slots: status?.slots ?? status?.policy?.slots ?? [], reconnect: true,
      restart_clients: restartClients,
      force_quit_clients: forceQuitClients,
    })}>{c.retry}</button> : null}
  </section>;
}
