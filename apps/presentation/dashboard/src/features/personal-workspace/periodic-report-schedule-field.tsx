import { useWorkspaceI18n } from "./i18n";

type Schedule = Record<string, unknown>;

export function withReportScheduleTimezone(
  configuration: Record<string, unknown>, key: string, value: unknown,
): Record<string, unknown> {
  const result = { ...configuration, [key]: value };
  const schedule = configuration.schedule;
  if (key === "timezone" && schedule && typeof schedule === "object"
    && "schema_version" in schedule && schedule.schema_version === "periodic_report_schedule_v0") {
    result.schedule = { ...schedule, timezone: value };
  }
  return result;
}

export function PeriodicReportScheduleField({ id, value, timezone, onChange }: Readonly<{
  id: string;
  value: unknown;
  timezone: string;
  onChange?: (value: Schedule | null) => void;
}>) {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const schedule = value && typeof value === "object" && !Array.isArray(value) ? value as Schedule : null;
  const fields = String(schedule?.rrule ?? "").split(";").map((part) => part.split("="));
  const rule = Object.fromEntries(fields.filter((part) => part.length === 2));
  const days = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
  const inRange = (value: string | undefined, maximum: number) => value !== undefined
    && /^\d+$/.test(value) && Number(value) <= maximum;
  const supported = !schedule || (schedule.schema_version === "periodic_report_schedule_v0"
    && Object.keys(schedule).every((key) => ["schema_version", "schedule_id", "rrule", "timezone", "start_date"].includes(key))
    && (schedule.start_date === undefined || (typeof schedule.start_date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(schedule.start_date)))
    && ["DAILY", "WEEKLY"].includes(rule.FREQ)
    && schedule.timezone === timezone
    && fields.every((part) => part.length === 2 && ["FREQ", "BYDAY", "BYHOUR", "BYMINUTE", "INTERVAL"].includes(part[0]))
    && new Set(fields.map(([key]) => key)).size === fields.length
    && inRange(rule.BYHOUR, 23) && inRange(rule.BYMINUTE ?? "0", 59)
    && (rule.FREQ === "WEEKLY" ? days.includes(rule.BYDAY) : !rule.BYDAY)
    && (!rule.INTERVAL || rule.INTERVAL === "1"));
  const labels = zh ? ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    : ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  function change(patch: Record<string, string>, startDate = typeof schedule?.start_date === "string" ? schedule.start_date : "") {
    const next = { FREQ: "WEEKLY", BYDAY: "MO", BYHOUR: "9", BYMINUTE: "0", ...rule, ...patch };
    onChange?.({
      schema_version: "periodic_report_schedule_v0",
      schedule_id: schedule?.schedule_id ?? "report-schedule",
      timezone,
      rrule: [`FREQ=${next.FREQ}`, ...(next.FREQ === "WEEKLY" ? [`BYDAY=${next.BYDAY}`] : []),
        `BYHOUR=${next.BYHOUR}`, `BYMINUTE=${next.BYMINUTE}`].join(";"),
      ...(startDate ? { start_date: startDate } : {}),
    });
  }
  return <div className="personal-report-schedule">
    <label className="is-boolean" htmlFor={id}>
      <span>{zh ? "按日历汇报" : "Calendar reports"}</span>
      <input id={id} type="checkbox" role="switch" checked={Boolean(schedule)} disabled={!onChange}
        onChange={(event) => event.target.checked ? change({}) : onChange?.(null)} />
    </label>
    {!schedule ? <p>{zh ? "未设置日历计划；保持阶段结束时汇报。" : "No calendar schedule; report at validated stage boundaries."}</p> : <>
      {!supported ? <p role="alert">{zh ? "此计划需在 JSON 模式中编辑；当前内容已保留。" : "Edit this schedule in JSON mode; its current value is preserved."}</p> : <>
        <label htmlFor={`${id}-frequency`}><span>{zh ? "频率" : "Frequency"}</span>
          <select id={`${id}-frequency`} value={rule.FREQ} disabled={!onChange} onChange={(event) => change({ FREQ: event.target.value })}>
            <option value="DAILY">{zh ? "每天" : "Daily"}</option><option value="WEEKLY">{zh ? "每周" : "Weekly"}</option>
          </select>
        </label>
        {rule.FREQ === "WEEKLY" && <label htmlFor={`${id}-day`}><span>{zh ? "星期" : "Weekday"}</span>
          <select id={`${id}-day`} value={rule.BYDAY} disabled={!onChange} onChange={(event) => change({ BYDAY: event.target.value })}>
            {days.map((day, index) => <option key={day} value={day}>{labels[index]}</option>)}
          </select>
        </label>}
        <label htmlFor={`${id}-time`}><span>{zh ? "当地时间" : "Local time"} ({timezone})</span>
          <input id={`${id}-time`} type="time" required disabled={!onChange}
            value={`${(rule.BYHOUR ?? "9").padStart(2, "0")}:${(rule.BYMINUTE ?? "0").padStart(2, "0")}`}
            onChange={(event) => {
              if (!/^\d{2}:\d{2}$/.test(event.target.value)) return;
              const [hour, minute] = event.target.value.split(":");
              change({ BYHOUR: hour, BYMINUTE: minute });
            }} />
        </label>
        <label htmlFor={`${id}-start-date`}><span>{zh ? "首次汇报日期（可选）" : "First report date (optional)"}</span>
          <input id={`${id}-start-date`} type="date" disabled={!onChange}
            value={typeof schedule.start_date === "string" ? schedule.start_date : ""}
            onChange={(event) => change({}, event.target.value)} />
        </label>
      </>}
      <p>{zh ? "由现有唤醒检查到期计划；实际送达以回执为准。" : "Existing wakes check the schedule; delivery is confirmed by its receipt."}</p>
    </>}
  </div>;
}
