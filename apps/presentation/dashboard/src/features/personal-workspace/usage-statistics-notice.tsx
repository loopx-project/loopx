import { useEffect, useRef, useState } from "react";
import { acknowledgeUsageNotice, usageStatistics, type UsageStatistics } from "../../data/chat";
import { useWorkspaceI18n } from "./i18n";

/** Show the disclosure on the live App before acknowledging the shared policy. */
export function UsageStatisticsNotice({ onDetails }: { onDetails: () => void }) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const [state, setState] = useState<UsageStatistics | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [error, setError] = useState(false);
  const [disabling, setDisabling] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const attempted = useRef(false);
  const choice = useRef(0);
  useEffect(() => {
    let active = true;
    usageStatistics().then(value => {
      if (active && value.automatic_notice_required) setState(value);
    }).catch(() => {}); // An unavailable settings service cannot authorize collection.
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!state?.automatic_notice_required || dismissed || !panel.current) return;
    let active = true;
    let frame = 0;
    const element = panel.current;
    const acknowledge = () => {
      cancelAnimationFrame(frame);
      if (document.visibilityState !== "visible" || !element.getClientRects().length) return;
      // Two frames let the visible disclosure paint before changing local state.
      frame = requestAnimationFrame(() => {
        frame = requestAnimationFrame(() => {
          if (!active || attempted.current || document.visibilityState !== "visible"
            || !element.getClientRects().length) return;
          const rect = element.getBoundingClientRect();
          if (rect.bottom <= 0 || rect.top >= window.innerHeight) return;
          attempted.current = true;
          const revision = choice.current;
          acknowledgeUsageNotice(state.notice).then(value => {
            if (active && revision === choice.current) setState(value);
          }).catch(() => { if (active && revision === choice.current) setError(true); });
        });
      });
    };
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) acknowledge();
    });
    observer.observe(element);
    document.addEventListener("visibilitychange", acknowledge);
    return () => {
      active = false;
      cancelAnimationFrame(frame);
      observer.disconnect();
      document.removeEventListener("visibilitychange", acknowledge);
    };
  }, [state, dismissed]);

  async function disable() {
    choice.current++;
    attempted.current = true;
    setDisabling(true);
    setError(false);
    try { setState(await usageStatistics(false)); }
    catch { setError(true); }
    finally { setDisabling(false); }
  }
  if (!state || dismissed) return null;
  return <section ref={panel} className="personal-usage-notice" aria-label={zh ? "基础使用统计" : "Basic usage statistics"} data-testid="usage-statistics-notice">
    <div>
      <strong role="status">{state.consent === "disabled" ? (zh ? "基础使用统计已关闭" : "Basic usage statistics disabled")
        : state.sending ? (zh ? "基础使用统计已开启" : "Basic usage statistics enabled")
          : state.automatic_notice_required ? (zh ? "基础使用统计 · 告知后自动开启" : "Basic usage statistics · enabled after this notice")
            : (zh ? "基础使用统计当前不发送，请查看详情" : "Basic usage statistics are not sending; see details")}</strong>
      <p>{zh
        ? "用于改进平台支持与使用体验。随机安装标识会关联每日 CLI 功能计数、版本、日期、自愿环境标签与已观测运行分钟。区间按安装去重；不同计时口径不能相加，不代表机器在线或任务完成。不采集对话、代码、路径或参数值。可随时关闭。"
        : "Helps improve platform support and usage. A random installation ID links to daily CLI counts, version, date, voluntary context and observed runtime minutes. Overlapping intervals are deduplicated per installation; different clocks cannot be added, and are not uptime or task completion. No conversations, code, paths or argument values. You can turn it off at any time."}</p>
      <p>{zh
        ? "独立无 ID 的 CLI 汇总保留；新增安装级概要由活动触发，至少间隔 15 分钟发送。关闭会清除本机标识和测量记录，已发送的记录不能撤回。"
        : "ID-free CLI summaries remain supported. New installation profiles are activity-triggered, at least 15 minutes apart. Disabling clears local ID and measurement history; it cannot recall already-sent records."}</p>
      <p className="personal-usage-recipient">{zh ? "接收方：" : "Recipient: "}{state.endpoint}</p>
      {error ? <p role="alert">{zh ? "设置未能保存，请打开详情重试。" : "Could not save this setting. Open details to retry."}</p> : null}
    </div>
    <div className="personal-usage-notice-actions">
      {state.consent !== "disabled" ? <button type="button" disabled={disabling} onClick={() => void disable()}>{zh ? "关闭统计" : "Turn off"}</button> : null}
      <button type="button" onClick={() => { setDismissed(true); onDetails(); }}>{zh ? "了解详情" : "Details"}</button>
      <button type="button" onClick={() => setDismissed(true)} aria-label={zh ? "收起统计告知" : "Dismiss statistics notice"}>{zh ? "收起" : "Dismiss"}</button>
    </div>
  </section>;
}
