import {useEffect, useId, useState} from "react";

type Session = {session_id: string; goal_id: string; managed_agent_id?: string; session_mode: string;
  agent_id: string; status: string; title?: string; updated_at?: string};
type Report = {available: boolean; configured?: boolean; enabled?: boolean; scheduled?: boolean; next_due_at?: string | null;
  retained_window_due_at?: string | null; generation_complete?: boolean; execution_failed?: boolean;
  publication_verified?: boolean; report_message_id?: string | null};

async function read<T>(url: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(url, {signal});
  const value = await response.json();
  if (!response.ok || value.ok !== true) throw new Error("Original report conversation unavailable");
  return value as T;
}

/** Select an existing original Session; never create, resume or retarget one. */
export function PeriodicReportSessionField({goalId, route, zh, disabled, revision, onChange}: {
  goalId: string; route: string; zh: boolean; disabled: boolean; revision: string;
  onChange: (route: string) => void;
}) {
  const id = useId();
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [reportFailed, setReportFailed] = useState(false);
  const local = route.startsWith("managed-session:");
  const selectedId = local ? route.slice("managed-session:".length) : "";

  useEffect(() => {
    const controller = new AbortController();
    setSessions([]); setLoading(true); setFailed(false);
    void read<{sessions: Session[]}>(`/api/chat/sessions?${new URLSearchParams({goal_id: goalId})}`, controller.signal)
      .then(value => {
        if (controller.signal.aborted) return;
        if (!Array.isArray(value.sessions) || value.sessions.some(session => session.goal_id !== goalId)) throw new Error("Source changed");
        setSessions(value.sessions.filter(session => session.managed_agent_id && session.session_mode === "managed_runtime"
          && session.agent_id === "codex" && session.status !== "closed"));
      }).catch(() => {if (!controller.signal.aborted) setFailed(true);})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [goalId, retry]);

  useEffect(() => {
    const controller = new AbortController();
    setReport(null); setReportFailed(false);
    if (selectedId && !disabled) {
      void read<{report?: Report}>(`/api/chat/sessions/${encodeURIComponent(selectedId)}/native-turn`, controller.signal)
        .then(value => {if (!controller.signal.aborted) {
          if (!value.report) throw new Error("Report readback unavailable");
          setReport(value.report);
        }}).catch(() => {if (!controller.signal.aborted) setReportFailed(true);});
    }
    return () => controller.abort();
  }, [selectedId, goalId, revision, disabled, retry]);

  return <div className="personal-capability-fields" style={{gridTemplateColumns: "minmax(0, 1fr)"}}>
    <label htmlFor={id}><span>{zh ? "报告接收会话" : "Report conversation"}</span>
      <select id={id} disabled={disabled || loading || failed} value={local ? route : route ? "other" : ""}
        onChange={event => onChange(event.target.value === "other" ? (local ? "weekly-report" : route || "weekly-report") : event.target.value)}>
        <option value="" disabled>{loading ? (zh ? "正在读取原会话…" : "Reading original conversations…")
          : zh ? "选择现有 Agent 会话" : "Choose an existing Agent conversation"}</option>
        {sessions.map(session => <option key={session.session_id} value={`managed-session:${session.session_id}`}>
          {session.managed_agent_id} · {session.title || (zh ? "原会话" : "Original conversation")}
          {session.updated_at ? ` · ${new Date(session.updated_at).toLocaleString(zh ? "zh-CN" : "en-US")}` : ""}
        </option>)}
        {local && !sessions.some(session => session.session_id === selectedId)
          ? <option value={route} disabled>{zh ? "原接收会话不可用" : "Original destination unavailable"}</option> : null}
        <option value="other">{zh ? "其它已配置位置" : "Another configured destination"}</option>
      </select>
    </label>
    {!local && route ? <label><span>{zh ? "现有报告位置" : "Existing report destination"}</span>
      <input value={route} disabled={disabled} onChange={event => onChange(event.target.value)} />
    </label> : null}
    {failed ? <span role="alert">{zh ? "无法读取原会话，未更换接收位置。" : "Original conversations could not be read; the destination is retained."}</span>
      : !loading && !sessions.length ? <span role="status">{zh ? "此 Goal 尚无已绑定的 Agent 会话。" : "This Goal has no bound Agent conversation."}</span> : null}
    {failed || reportFailed ? <button type="button" disabled={disabled} onClick={() => setRetry(value => value + 1)}>
      {zh ? "重新读取" : "Read again"}</button> : null}
    {reportFailed ? <span role="status">{zh ? "报告执行与送达状态暂时未知。" : "Report execution and delivery are currently unknown."}</span> : null}
    {report ? <span role={report.available ? "status" : "alert"}>
      {!report.available ? (zh ? "原会话不可用，定时报告不能执行。" : "The original conversation is unavailable; scheduled reports cannot run.")
        : !report.configured ? (zh ? "保存后在所选原会话接收报告。" : "Save to receive reports in the selected original conversation.")
        : !report.enabled ? (zh ? "定时报告已暂停。" : "Scheduled reports are paused.")
        : report.scheduled === false ? (zh ? "已启用，请设置日历汇报时间。" : "Enabled; set a calendar report time.")
        : report.publication_verified ? (zh ? "本期报告已在原会话完整读回。" : "This period’s full report was read back in the original conversation.")
        : report.execution_failed ? (zh ? "本期执行失败；原结果与失败记录已保留。" : "This period’s execution failed; the original result and failure are retained.")
        : report.generation_complete ? (zh ? "本期已生成，完整送达尚未确认。" : "This period was generated; complete delivery is unverified.")
        : (zh ? "定时报告已启用。" : "Scheduled reports are enabled.")}
      {report.enabled && report.next_due_at && (!report.retained_window_due_at || report.publication_verified)
        ? ` ${zh ? "下次：" : "Next: "}${new Date(report.next_due_at).toLocaleString(zh ? "zh-CN" : "en-US")}` : ""}
    </span> : null}
    {report?.publication_verified && report.report_message_id ? <a href={`/chat/?${new URLSearchParams({
      reportSessionId: selectedId, reportMessageId: report.report_message_id,
    })}`}>{zh ? "查看本期完整报告" : "Read this period’s full report"}</a> : null}
  </div>;
}
