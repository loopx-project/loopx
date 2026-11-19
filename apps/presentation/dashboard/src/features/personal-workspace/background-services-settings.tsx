import {useCallback, useEffect, useRef, useState} from "react";
import {AlertCircle, CheckCircle2, Circle, RefreshCw} from "lucide-react";
import {z} from "zod";
import {BACKGROUND_STATES} from "../../../../../../loopx/control_plane/runtime/background_services.ts";
import type {BackgroundState, BackgroundServicesSnapshot} from "../../../../../../loopx/control_plane/runtime/background_services.ts";
import {requestJson} from "../../data/chat";
import {ContextHelp} from "./context-help";
import {useWorkspaceI18n} from "./i18n";
import "./background-services-settings.css";

const path = "/api/chat/background-services";
const healthSchema = z.object({state: z.enum(BACKGROUND_STATES),
  observed_at: z.number().finite().nullable(), last_success_at: z.number().finite().nullable(),
  last_failure_at: z.number().finite().nullable(), next_check_at: z.number().finite().nullable(),
  failure_count: z.number().int().nonnegative().nullable(), reconcile_failed: z.boolean()});
const snapshotSchema = z.object({ok: z.literal(true), schema_version: z.literal("background_services_v1"),
  checked_at: z.number().finite(), supervisor: healthSchema,
  tasks: z.array(healthSchema.extend({id: z.string().min(1), service_id: z.string().min(1)}))});
const labels: Record<BackgroundState, [string, string]> = {
  healthy: ["正常", "Healthy"], checking: ["正在检查", "Checking"], starting: ["正在启动", "Starting"],
  retrying: ["退避重试中", "Backing off"], overdue: ["检查逾期", "Check overdue"],
  stopped: ["已停止", "Stopped"], stopping: ["正在停止", "Stopping"], completed: ["已完成", "Completed"],
  disabled: ["未启用", "Disabled"], unavailable: ["不可用", "Unavailable"], unverified: ["尚未核实", "Unverified"],
};
const names: Record<string, [string, string]> = {
  "codex-observation": ["Codex 账号与额度", "Codex accounts and quota"],
  "manager-returns": ["结果回传", "Result delivery"],
  "manager-executions": ["排队执行恢复", "Queued execution recovery"],
  "lark-delivery": ["Lark 消息投递", "Lark message delivery"],
};
const faults: BackgroundState[] = ["overdue", "retrying", "stopped", "unavailable", "unverified"];

export function BackgroundServicesSettings({onOpenSettings}: {onOpenSettings?: (tab: "provider" | "lark") => void}) {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN", language = zh ? 0 : 1;
  const [snapshot, setSnapshot] = useState<BackgroundServicesSnapshot | null>(null);
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rechecked, setRechecked] = useState(false);
  const generation = useRef(0);
  const mounted = useRef(true);
  const load = useCallback(async (recheck = false) => {
    const current = ++generation.current;
    try {
      const result = snapshotSchema.parse(await requestJson<unknown>(path, recheck ? {
        method: "POST", body: JSON.stringify({operation: "recheck"}), headers: {"Content-Type": "application/json"},
      } : undefined));
      if (mounted.current && current === generation.current) {
        setSnapshot(result); setError(false); if (recheck) setRechecked(true);
      }
    } catch {
      if (mounted.current && current === generation.current) setError(true);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = window.setInterval(() => {if (!document.hidden) void load();}, 10_000);
    return () => {mounted.current = false; generation.current++; window.clearInterval(timer);};
  }, [load]);
  const time = (value: number | null) => value === null ? "—" : new Date(value * 1000).toLocaleString(locale);
  const status = (state: BackgroundState) => {
    const Icon = faults.includes(state) ? AlertCircle : state === "healthy" ? CheckCircle2 : Circle;
    return <span className="background-status" data-state={error ? "unverified" : state}><Icon size={16} aria-hidden="true" />{error ? (zh ? "上次记录：" : "Last observed: ") : ""}{labels[state][language]}</span>;
  };
  return <section className="background-settings" aria-label={zh ? "后台服务" : "Background services"}>
    <div className="background-toolbar">
      <div>{snapshot ? <>{zh ? "后台宿主" : "Background host"} {status(snapshot.supervisor.state)}</> : null}</div>
      <div className="background-actions">
        <ContextHelp label={zh ? "后台检查与恢复" : "Background checks and recovery"}>
          <p>{zh ? "重新检查会恢复已启用但未运行的后台任务。授权仍由原设置管理；在途任务不会重放。检查逾期表示近期进度不足，需要查看对应模块。宿主随 LoopX 服务运行，服务进程由 App 和系统管理。" : "Recheck restores enabled background tasks that are not running. Existing settings retain authority; in-flight work is not replayed. Overdue checks need attention in the owning feature. The App and OS manage the LoopX service process."}</p>
        </ContextHelp>
        <button className="personal-secondary-action" disabled={busy} type="button" onClick={() => {
          setBusy(true); setRechecked(false);
          void load(true).finally(() => {if (mounted.current) setBusy(false);});
        }}><RefreshCw size={15} aria-hidden="true" />{busy ? (zh ? "正在检查" : "Checking") : (zh ? "重新检查" : "Recheck")}</button>
      </div>
    </div>
    {error ? <p role="alert">{zh ? "暂时无法读取后台状态。显示的记录可能已过期，请重新检查。" : "Background status is unavailable. Displayed observations may be stale; recheck to recover."}</p> : null}
    {!snapshot && !error ? <p role="status">{zh ? "正在读取后台状态…" : "Loading background status…"}</p> : null}
    {rechecked && !error ? <p role="status">{zh ? "已重新核对启用状态，下面显示最新观测。" : "Enabled state rechecked. Latest observations are shown below."}</p> : null}
    {snapshot?.tasks.length === 0 ? <p>{zh ? "当前服务未登记后台任务。" : "No background tasks are registered in this service."}</p> : null}
    <div className="background-task-list">
      {snapshot?.tasks.map(task => <article className="background-task" key={task.id}>
        <div className="background-task-title"><h2>{names[task.id]?.[language] ?? task.id}</h2>{status(task.state)}</div>
        <dl className="background-task-times">
          <div><dt>{zh ? "最近进度" : "Last progress"}</dt><dd>{time(task.observed_at)}</dd></div>
          <div><dt>{zh ? "下次检查" : "Next check"}</dt><dd>{time(task.next_check_at)}</dd></div>
        </dl>
        <details><summary>{zh ? "检查记录" : "Check details"}</summary>
          <dl className="background-task-times">
            <div><dt>{zh ? "最近成功" : "Last success"}</dt><dd>{time(task.last_success_at)}</dd></div>
            <div><dt>{zh ? "最近失败" : "Last failure"}</dt><dd>{time(task.last_failure_at)}</dd></div>
            <div><dt>{zh ? "连续失败" : "Consecutive failures"}</dt><dd>{task.failure_count ?? "—"}</dd></div>
          </dl>
          {task.reconcile_failed ? <p>{zh ? "启用状态核对失败，将自动重试。" : "Enabled state reconciliation failed; it will retry automatically."}</p> : null}
          {onOpenSettings && (task.service_id === "codex-accounts" || task.service_id === "lark") ? <button
            className="background-feature-link" type="button" onClick={() => onOpenSettings(task.service_id === "lark" ? "lark" : "provider")}>
            {task.service_id === "lark" ? (zh ? "连接设置" : "Connection settings") : (zh ? "账号设置" : "Account settings")}
          </button> : null}
        </details>
      </article>)}
    </div>
  </section>;
}
