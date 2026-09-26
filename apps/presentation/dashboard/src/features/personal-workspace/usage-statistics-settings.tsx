import { useEffect, useState } from "react";
import { usageStatistics, type UsageStatistics } from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";

export function UsageStatisticsSettings() {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [state, setState] = useState<UsageStatistics | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    usageStatistics().then(value => { if (active) setState(value); })
      .catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, []);
  async function update(enabled: boolean) {
    setBusy(true); setError(false);
    try { setState(await usageStatistics(enabled)); }
    catch { setError(true); }
    finally { setBusy(false); }
  }
  return <details className="personal-capability-scope-note personal-usage-statistics" data-testid="usage-statistics-settings">
    <summary>{zh ? "基础使用统计 · 默认开启，可关闭" : "Basic usage statistics · on by default, optional"}</summary>
    <p>{zh
      ? "用于决定平台支持和改进命令体验。每天向 LoopX 的 Cloudflare 收集服务发送随机安装标识、版本、系统、CPU 架构、Python 版本和安装渠道；固定的 CLI 功能、结果、耗时区间和错误类别在本机按天汇总后另行发送，不带安装标识。"
      : "Helps prioritize platform support and CLI improvements. A daily heartbeat sends a random installation ID, version, OS, CPU architecture, Python version and install channel to the LoopX Cloudflare collector. Fixed CLI feature, result, duration and error counts are aggregated locally by day and sent separately without the ID."}</p>
    <p>{zh ? "不采集提示词、代码、路径、命令参数、Goal 内容或原始错误。当前功能计数仅覆盖 CLI；命令成功不等于 Goal 完成。" : "No prompts, code, paths, arguments, Goal contents or raw errors. Feature counts currently cover CLI only; command success is not Goal completion."}</p>
    <p>{zh ? "按天分别汇总所有 Host 的 quota→spend 推进周期、已绑定 Codex 任务的本地轮次时间、受管 Turn 与普通 Goal 对话的 Host 调用时间。上传固定 Host 类别及跨度/时长区间，不上传会话内容、Goal 或安装标识。三种口径重叠，不能相加；可能漏计，不代表完成、CPU 用时或计费。" : "Daily, separate span/duration buckets for all Hosts using quota→spend, local timing events from bound Codex tasks, and direct Host calls in managed Turns and regular owner Goal chat. Sends fixed Host categories, never session contents, Goal or installation IDs. The three overlapping populations cannot be added; partial observations are not completion, CPU time or billing."}</p>
    {state ? <>
      <label><input type="checkbox" checked={state.consent !== "disabled"} disabled={busy}
        onChange={event => void update(event.target.checked)} /> {zh ? "允许基础使用统计（整台机器）" : "Allow basic usage statistics (this machine)"}</label>
      <p role="status">{state.sending ? (zh ? "已允许发送" : "Sending allowed")
        : state.notice_required && state.consent !== "disabled" ? (zh ? "等待首次告知确认；尚未发送" : "Awaiting first-use acknowledgment; not sending")
          : (zh ? `当前不发送：${({disabled:"已关闭",CI:"CI 环境",DO_NOT_TRACK:"请勿追踪开关",LOOPX_USAGE_PING:"环境变量已关闭",consent_required:"需要明确同意",invalid_policy:"策略配置无效",invalid_endpoint:"接收地址无效",notice_required:"需要重新告知"} as Record<string,string>)[state.blocked_by ?? ""] ?? "请检查配置"}` : `Not sending: ${state.blocked_by}`)}</p>
      {state.notice_required && state.consent !== "disabled" ? <button className="personal-secondary-action" type="button" disabled={busy} onClick={() => void update(true)}>{zh ? "已了解，启用统计" : "Understood, enable statistics"}</button> : null}
      <p>{zh ? "接收地址：" : "Recipient: "}{state.endpoint ?? (zh ? "未配置" : "Not configured")}</p>
      <details><summary>{zh ? "查看待发送数据" : "Preview outgoing data"}</summary><pre>{JSON.stringify({ heartbeat: state.next_payload, aggregate: state.aggregate_preview, goals: state.goal_preview }, null, 2)}</pre></details>
    </> : null}
    {error ? <p role="alert">{zh ? "无法读取或保存；请用终端检查：" : "Could not read or save; inspect in terminal: "}<code>loopx usage-ping status</code></p> : null}
    <p><code>loopx usage-ping disable</code> · <code>LOOPX_USAGE_PING=0</code></p>
    <p>{zh ? "关闭会删除本机标识和待发送计数；无法撤回已发送记录。" : "Disabling deletes the local ID and pending counts; it cannot recall records already sent."}</p>
  </details>;
}
