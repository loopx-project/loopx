import {useState} from "react";
import {MarkdownText} from "./markdown";

/** Provider-local read model; these records grant no execution authority. */
export type ConversationRecord = {
  id: string; end: number; role: "user" | "assistant" | "tool" | "activity";
  kind: string; label: string; text: string; truncated: boolean;
  timestamp?: string | null; content_ref?: string; redacted?: boolean; unavailable?: string;
};
export type ContentPage = {text: string; next_offset: number; has_more: boolean};

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

export function AttachedConversationRecord({record, agent, zh, load}: {
  record: ConversationRecord; agent: string; zh: boolean;
  load: (record: ConversationRecord, offset: number) => Promise<ContentPage>;
}) {
  const [text, setText] = useState(record.text);
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
  const title = record.role === "user" ? (zh ? "你" : "You") : record.role === "assistant" ? agent :
    `${labels[record.kind] ?? (zh ? "记录暂不可读" : "Record unavailable")}${record.label && record.label !== record.kind ? ` · ${record.label}` : ""}`;
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
  const heading = <><strong>{title}</strong>{record.timestamp ? <time>{new Date(record.timestamp).toLocaleString()}</time> : null}</>;
  return <article data-role={record.role} data-record-id={record.id}>
    {record.role !== "tool" ? <header>{heading}</header> : null}
    {record.unavailable ? <p>{zh ? "源记录无法安全读取；此处保留缺口，未将其当作完成。" : "This source record could not be read safely. The gap is retained, not treated as completion."}</p> :
      record.role === "tool" || text.length > 1800 ? <details><summary>{record.role === "tool" ? heading : (zh ? "查看内容" : "Show content")}{more ? <span>{zh ? " · 可继续加载" : " · more available"}</span> : null}</summary>{content}</details> : content}
  </article>;
}
