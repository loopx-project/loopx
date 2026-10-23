import {useEffect, useId, useState} from "react";
import type {PrivateConversation} from "../../data/chat";
import {PeriodicReportScheduleField, withReportScheduleTimezone} from "./periodic-report-schedule-field";
import "./steward-report.css";

type Managed = Extract<NonNullable<PrivateConversation["managed_steward"]>, {available: true}>;

export function StewardReport({state, zh, busy, available, goalActive, onChange}: {
  state: Managed["report"]; zh: boolean; busy: boolean; available: boolean; goalActive: boolean;
  onChange: (operation: "enable" | "pause" | "cancel", schedule?: Record<string, unknown>) => void;
}) {
  const original = state?.available ? state.schedule : null;
  const scheduleId = useId();
  const defaultZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const [zone, setZone] = useState(String(original?.timezone ?? defaultZone));
  const [schedule, setSchedule] = useState<Record<string, unknown> | null>(original ?? {
    schema_version: "periodic_report_schedule_v0", schedule_id: "steward-report",
    timezone: defaultZone, rrule: "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
    start_date: new Intl.DateTimeFormat("sv-SE", {timeZone: defaultZone}).format(new Date()),
  });
  const signature = JSON.stringify(original);
  useEffect(() => {if (original) {setSchedule(original); setZone(String(original.timezone));}}, [signature]);
  if (!state) return null;
  const disabled = busy || !available;
  let validZone = true;
  try {new Intl.DateTimeFormat("en", {timeZone: zone}).format();} catch {validZone = false;}
  const stamp = (value?: string | null) => value ? new Date(value).toLocaleString(zh ? "zh-CN" : "en-US") : (zh ? "暂无" : "None");
  return <section className="personal-steward-report">
    <h4>{zh ? "定期详细汇报" : "Detailed periodic reports"}</h4>
    {!state.available ? <p role="status">{zh ? "汇报状态暂不可读；原计划和回执已保留。刷新连接后重试。" : "Report status is unavailable. The original schedule and receipts are retained; refresh the connection."}</p> : <>
      <p role="status">{state.enabled ? (zh ? "已启用 · 完整中文正文回此私聊" : "Enabled · Full Chinese report returns to this private Chat")
        : state.configured ? (zh ? "已暂停 · 原窗口和回执保留" : "Paused · Original windows and receipts retained")
        : (zh ? "尚未启用" : "Not enabled")}</p>
      <p>{zh ? "汇总全部获准本机 Goal，包括停止项和读取缺口。每天汇报前24小时，生成与实际送达分别核验；状态监控照常运行。" : "Covers every authorized local Goal, including stopped Goals and read gaps. Daily reports cover the previous 24 hours; generation and delivery are verified separately. Monitoring continues."}</p>
      {state.configured ? <p>{zh ? "下次计划：" : "Next deadline: "}{stamp(state.next_due_at)}<br/>
        {zh ? "最近窗口：" : "Latest window: "}{stamp(state.retained_window_due_at)} · {state.delivery_status === "delivered"
          ? (zh ? "原私聊全文已核验送达" : "Full private reply verified")
          : state.delivery_status === "unconfirmed" ? (zh ? "发送结果待确认，不重复发送" : "Send unconfirmed; no blind resend")
          : state.generation_id ? (zh ? "已生成，投递待核验" : "Generated; delivery pending") : (zh ? "尚未生成" : "Not generated")}</p> : null}
      <div className="personal-capability-fields"><label>{zh ? "汇报时区" : "Report timezone"}<input value={zone} aria-invalid={!validZone} disabled={disabled} onChange={event => {
        const value = event.target.value; setZone(value);
        if (schedule) setSchedule(withReportScheduleTimezone({schedule}, "timezone", value).schedule as Record<string, unknown>);
      }}/></label>
      {!validZone ? <p role="alert">{zh ? "请输入有效时区，例如 Asia/Shanghai。" : "Enter a valid timezone, such as Asia/Shanghai."}</p> : null}
      <PeriodicReportScheduleField id={scheduleId} value={schedule} timezone={zone} calendarRequired
        onChange={disabled ? undefined : setSchedule}/></div>
      <p>{zh ? "暂停只停止后续生成；取消还会请求停止正在生成的本期汇报。两者均保留已生成报告和投递回执，停用期间不再发送。" : "Pause stops future generation. Cancel also requests a stop for an active report. Both retain generated reports and receipts, and stop further delivery while disabled."}</p>
      <div className="personal-cadence-actions"><button className="is-primary" type="button" disabled={disabled || !goalActive || !schedule || !zone.trim() || !validZone}
        onClick={() => schedule && onChange("enable", schedule)}>{state.enabled ? (zh ? "更新汇报计划" : "Update report schedule") : (zh ? "启用详细汇报" : "Enable detailed reports")}</button>
        {state.enabled ? <button type="button" disabled={disabled} onClick={() => onChange("pause")}>{zh ? "暂停汇报" : "Pause reports"}</button> : null}
        {state.configured ? <button type="button" disabled={disabled} onClick={() => onChange("cancel")}>{zh ? "取消汇报" : "Cancel reports"}</button> : null}
      </div>
    </>}
  </section>;
}
