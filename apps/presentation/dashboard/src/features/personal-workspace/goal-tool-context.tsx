import type {TrajectoryToolContext} from "../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js";
import {ContextHelp} from "./context-help";

/** Formats the toolkit observation; never classifies commands or infers savings. */
export function ToolContextInventory({value, zh}: {value: TrajectoryToolContext | undefined; zh: boolean}) {
  if (!value) return null;
  const number = (n: number) => n.toLocaleString(zh ? "zh-CN" : "en");
  const fraction = value.status === "observed" ? value.loopx_tool_output_fraction : null;
  const percent = fraction === null ? "—" : fraction > 0 && fraction < 0.001 ? "<0.1%"
    : fraction < 1 && fraction > 0.999 ? ">99.9%"
    : new Intl.NumberFormat(zh ? "zh-CN" : "en", {style: "percent", maximumFractionDigits: 1}).format(fraction);
  return <section className="research-request-coverage" aria-label={zh ? "LoopX 工具上下文" : "LoopX tool context"}>
    <div className="research-usage-heading"><h4>{zh ? "LoopX 工具返回 · 字节" : "LoopX tool returns · bytes"}</h4>
      <ContextHelp label={zh ? "工具上下文统计口径" : "Tool context measurement basis"}>
        <p>{zh ? "分母是捕获中追加的用户、系统、开发者文本及工具返回；分子仅识别简单 LoopX CLI 调用的返回。复合脚本、间接调用、文件读取和其他内容保持未归因，图片等非文本内容不计字节。" : "The denominator is appended user, system, developer text and tool returns. Only returns from simple LoopX CLI calls are assigned. Compound scripts, indirect calls, file reads and other content remain unassigned; non-text content such as images has no byte count."}</p>
        <p>{zh ? "这是已记录内容的组成，不是逐请求重放的上下文或计费 token 占比。重复内容不等于无益重复，LoopX 返回也可能携带有用研究证据；不能据此推断净成本或提分。" : "This describes recorded content, not per-request replay or billable token share. Repeated content need not be waste; LoopX returns can carry useful research evidence. It does not establish net cost or score benefit."}</p>
      </ContextHelp></div>
    {value.status === "unavailable" ? <p role="status">{zh ? "捕获未通过核对 · 占比未知" : "Capture could not be reconciled · share unknown"}</p> : <>
      <p className="research-input-share-value"><strong data-loopx-tool-context-share>{percent}</strong><span>{zh ? "占已记录输入文本" : "of recorded input text"}</span></p>
      <details className="research-usage-detail"><summary>{zh ? "返回明细" : "Return details"}</summary><dl className="research-usage-basis">
        <dt>{zh ? "LoopX 工具返回" : "LoopX tool returns"}</dt><dd>{number(value.loopx_tool_output_bytes)} B</dd>
        <dt>{zh ? "已记录输入文本" : "Recorded input text"}</dt><dd>{number(value.observed_input_text_bytes)} B</dd>
        <dt>{zh ? "未归因文本" : "Unassigned text"}</dt><dd>{number(value.unassigned_input_text_bytes)} B</dd>
        <dt>{zh ? "未计量内容块" : "Unmeasured content parts"}</dt><dd>{number(value.unmeasured_parts)}</dd>
        <dt>{zh ? "重复相同返回" : "Repeated identical returns"}</dt><dd>{number(value.repeated_loopx_output_bytes)} B</dd>
        <dt>{zh ? "已去重返回记录" : "Duplicate return records excluded"}</dt><dd>{number(value.duplicate_output_records)}</dd>
        {value.operations.map(row => <div key={row.operation}><dt>{row.operation}</dt><dd>{number(row.calls)} {zh ? "次" : "calls"} · {number(row.bytes)} B</dd></div>)}
      </dl></details>
    </>}
  </section>;
}
