import {useLayoutEffect, useRef, useState} from "react";
import {MarkdownText} from "./markdown";

/** Provider-local read model; these records grant no execution authority. */
export type ConversationRecord = {
  id: string; end: number; role: "user" | "assistant" | "tool" | "activity";
  kind: string; label: string; text: string; truncated: boolean;
  timestamp?: string | null; content_ref?: string; redacted?: boolean; unavailable?: string;
  /** Saved shell observations only; neither inferred failures nor task acceptance. */
  tool_exit_codes?: number[];
};
export type ContentPage = {text: string; next_offset: number; has_more: boolean};

/** Keep source order, but give dialogue priority over consecutive tool events. */
export function AttachedConversationTimeline({records, ...props}: {
  records: ConversationRecord[]; agent: string; zh: boolean;
  load: (record: ConversationRecord, offset: number) => Promise<ContentPage>;
  live?: boolean; active?: boolean;
}) {
  const groupKeys = useRef(new Map<string, string>());
  const nextKeys = new Map<string, string>();
  useLayoutEffect(() => {groupKeys.current = nextKeys;});
  const items: (ConversationRecord | ConversationRecord[])[] = [];
  for (const record of records) {
    if (record.role === "user" || record.role === "assistant") items.push(record);
    else {
      const previous = items.at(-1);
      if (Array.isArray(previous)) previous.push(record);
      else items.push([record]);
    }
  }
  return items.map(item => {
    if (!Array.isArray(item)) return <AttachedConversationRecord key={`${item.id}:${item.content_ref ?? ""}`} record={item} {...props}/>;
    // Reuse the group identity through overlapping source pages in either
    // direction. First/last-record keys would close it as a window is trimmed.
    const tokens = item.map(record => JSON.stringify([record.id, record.content_ref]));
    const key = tokens.map(token => groupKeys.current.get(token)).find(Boolean) ?? tokens[0];
    for (const token of tokens) nextKeys.set(token, key);
    const interrupted = item.some(record => record.kind === "error" || record.kind === "turn_aborted");
    const gap = item.some(record => record.unavailable);
    const nonzeroResults = item.filter(record => record.tool_exit_codes?.some(code => code !== 0)).length;
    return <details className="attached-record-group" key={key}>
      <summary data-record-anchor={item.map(record => record.id).join(",")}>
        {props.live && item.some(record => record.role === "tool") ? <ActivityMark active={Boolean(props.active)} zh={props.zh}/> : null}
        <span>{props.zh ? "工作过程" : "Work activity"} <span className="attached-record-count">· {item.length} {props.zh ? "条记录" : "records"}</span></span>
        {interrupted ? <span className="attached-record-warning">{props.zh ? "含中断或报错" : "Includes an interruption or error"}</span> : null}
        {nonzeroResults ? <span className="attached-record-warning">{props.zh ? `${nonzeroResults} 条结果含非零退出码` : `${nonzeroResults} results with nonzero exit codes`}</span> : null}
        {gap ? <span className="attached-record-warning">{props.zh ? "部分记录不可读" : "Some records unavailable"}</span> : null}
      </summary>
      <div className="attached-record-group-body">{item.map(record => <AttachedConversationRecord key={`${record.id}:${record.content_ref ?? ""}`} record={record} {...props}/>)}</div>
    </details>;
  });
}

function ActivityMark({active, zh}: {active: boolean; zh: boolean}) {
  const label = active ? (zh ? "原宿主执行中" : "Work observed in the original host") : (zh ? "回看的原宿主记录" : "Previously observed host record");
  return <span className="attached-activity-mark" data-active={active || undefined} role="img" aria-label={label} title={label}/>;
}

function ToolContent({text, complete, zh}: {text: string; complete: boolean; zh: boolean}) {
  // Presentation only: render the saved shell envelope's output as readable text.
  // Keep every source field accessible; an exit code is not task acceptance.
  if (complete) {
    try {
      const value: unknown = JSON.parse(text);
      if (value && typeof value === "object" && !Array.isArray(value) &&
        "output" in value && typeof value.output === "string" &&
        "wall_time_seconds" in value && typeof value.wall_time_seconds === "number" &&
        "exit_code" in value && (value.exit_code === null || typeof value.exit_code === "number")) {
        return <>
          <div className="attached-tool-metadata">{value.exit_code === null ? (zh ? "尚无退出码" : "No exit code yet") : `${zh ? "退出码" : "Exit code"} ${value.exit_code}`} · {value.wall_time_seconds} s</div>
          <pre>{value.output}</pre>
          <details className="attached-tool-source"><summary>{zh ? "原始记录字段" : "Original record fields"}</summary><pre>{text}</pre></details>
        </>;
      }
    } catch { /* Plain text and partial/unknown formats remain lossless. */ }
  }
  return <pre>{text}</pre>;
}

export function AttachedConversationRecord({record, agent, zh, load, live = false, active = false}: {
  record: ConversationRecord; agent: string; zh: boolean;
  load: (record: ConversationRecord, offset: number) => Promise<ContentPage>;
  live?: boolean;
  active?: boolean;
}) {
  const [storedText, setText] = useState(record.text);
  const text = live ? record.text : storedText;
  const [more, setMore] = useState(record.truncated);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [offset, setOffset] = useState(Array.from(record.text).length);
  const labels: Record<string, string> = zh ? {
    task_started: "开始执行", task_complete: "本轮结束", turn_aborted: "执行中断", error: "执行报错",
    function_call: "工具调用", custom_tool_call: "工具调用",
    function_call_output: "工具结果", custom_tool_call_output: "工具结果",
  } : {
    task_started: "Turn started", task_complete: "Turn ended", turn_aborted: "Turn interrupted", error: "Execution error",
    function_call: "Tool call", custom_tool_call: "Tool call",
    function_call_output: "Tool result", custom_tool_call_output: "Tool result",
  };
  const title = live && record.role === "tool" ? record.label :
    record.role === "user" ? (zh ? "你" : "You") : record.role === "assistant" ? agent :
    `${labels[record.kind] ?? (zh ? "记录暂不可读" : "Record unavailable")}${record.label && record.label !== record.kind ? ` · ${record.label}` : ""}`;
  const nonzeroCodes = [...new Set(record.tool_exit_codes?.filter(code => code !== 0))];
  async function continueReading() {
    if (loading) return;
    setLoading(true); setError("");
    try {
      const page = await load(record, offset);
      setText(previous => previous + page.text); setOffset(page.next_offset); setMore(page.has_more);
    } catch (failure) {setError(String(failure));}
    finally {setLoading(false);}
  }
  const content = <>
    {record.redacted ? <p>{zh ? "此段包含疑似凭据，内容已隐藏。" : "This content contains credential-shaped data and has been withheld."}</p> : record.role === "tool" ? <ToolContent text={text} complete={!more} zh={zh}/> : <MarkdownText text={text}/>}
    {more ? <button type="button" disabled={loading} onClick={() => void continueReading()}>{loading ? (zh ? "正在读取…" : "Loading…") : (zh ? "继续读取完整内容" : "Read more of this record")}</button> : null}
    {error ? <p role="alert">{error}</p> : null}
  </>;
  const heading = <><strong>{live && record.role !== "activity" ? <ActivityMark active={active} zh={zh}/> : null}{title}</strong>{nonzeroCodes.length ? <span className="attached-record-warning">{zh ? "退出码" : "Exit code"} {nonzeroCodes.join(", ")}</span> : null}{record.timestamp ? <time dateTime={record.timestamp}>{new Date(record.timestamp).toLocaleString()}</time> : null}</>;
  return <article data-role={record.role} data-record-id={record.id} data-record-anchor={record.id}>
    {record.role !== "tool" ? <header>{heading}</header> : null}
    {record.unavailable ? <p>{zh ? "源记录无法安全读取；此处保留缺口，未将其当作完成。" : "This source record could not be read safely. The gap is retained, not treated as completion."}</p> :
      record.role === "tool" ? <details><summary>{heading}{more ? <span>{zh ? " · 可继续加载" : " · more available"}</span> : null}</summary>{content}</details> : content}
  </article>;
}
