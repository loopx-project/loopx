import type { ReactNode } from "react";
import { Brain, FilePen, FileText, FolderOpen, Globe, Search, Terminal, Wrench } from "lucide-react";
import type { TurnStep } from "../../data/turn-steps";

const VERBS = {
  zh: { reasoning: "思考", read: "读取", search: "搜索", list: "列出", run: "运行", tool: "调用", web: "检索", file_change: "修改" },
  en: { reasoning: "Thought", read: "Read", search: "Search", list: "List", run: "Run", tool: "Call", web: "Search web", file_change: "Edit" },
} as const;
const ONGOING = {
  zh: { reasoning: "正在思考", read: "正在读取", search: "正在搜索", list: "正在列出", run: "正在运行", tool: "正在调用", web: "正在检索", file_change: "正在修改" },
  en: { reasoning: "Thinking", read: "Reading", search: "Searching", list: "Listing", run: "Running", tool: "Calling", web: "Searching the web for", file_change: "Editing" },
} as const;

function verbKey(step: TurnStep) {
  if (step.kind === "command") return step.verb ?? "run";
  if (step.kind === "search") return "web";
  return step.kind;
}

function icon(step: TurnStep): ReactNode {
  const props = { size: 13, "aria-hidden": true } as const;
  switch (verbKey(step)) {
    case "reasoning": return <Brain {...props}/>;
    case "read": return <FileText {...props}/>;
    case "search": return <Search {...props}/>;
    case "list": return <FolderOpen {...props}/>;
    case "tool": return <Wrench {...props}/>;
    case "web": return <Globe {...props}/>;
    case "file_change": return <FilePen {...props}/>;
    default: return <Terminal {...props}/>;
  }
}

function seconds(ms: number, zh: boolean) {
  const value = ms < 10000 ? (Math.round(ms / 100) / 10).toString() : String(Math.round(ms / 1000));
  return zh ? `${value} 秒` : `${value}s`;
}

/** Status-line text for the step the executor is on now, if any. */
export function currentTurnStepText(steps: readonly TurnStep[] | undefined, zh: boolean): string | null {
  const step = [...(steps ?? [])].reverse().find(item => item.state === "running");
  if (!step) return null;
  const ongoing = ONGOING[zh ? "zh" : "en"][verbKey(step)];
  if (step.kind === "reasoning") return step.title ? `${ongoing}${zh ? "：" : ": "}${step.title}` : ongoing;
  return `${ongoing} ${step.title}`.trim();
}

// Reasoning text often opens with the heading already used as the row title.
function stepDetail(step: TurnStep) {
  if (!step.detail || step.kind !== "reasoning" || !step.title) return step.detail;
  const [first, ...rest] = step.detail.split("\n");
  return first.replace(/^[\s*#]+|[\s*]+$/g, "") === step.title ? rest.join("\n").trim() : step.detail;
}

function StepRow({ step, zh, live }: { step: TurnStep; zh: boolean; live: boolean }) {
  const detail = stepDetail(step);
  const verbs = VERBS[zh ? "zh" : "en"];
  const meta = [
    step.state === "running" ? (live ? (zh ? "进行中" : "Running") : (zh ? "未结束" : "Not finished")) : null,
    step.state === "failed" ? (step.exit_code !== undefined ? (zh ? `失败 · 退出码 ${step.exit_code}` : `Failed · exit ${step.exit_code}`) : (zh ? "失败" : "Failed")) : null,
    step.count && step.count > 1 ? (zh ? `${step.count} 个文件` : `${step.count} files`) : null,
    step.duration_ms !== undefined && (step.kind === "reasoning" || step.duration_ms >= 1000) ? seconds(step.duration_ms, zh) : null,
  ].filter(Boolean).join(" · ");
  const head = <>
    <span className="personal-turn-step-icon">{icon(step)}</span>
    <span className="personal-turn-step-verb">{verbs[verbKey(step)]}</span>
    {step.title ? <span className={step.kind === "reasoning" ? "personal-turn-step-title" : "personal-turn-step-title is-code"}>{step.title}</span> : null}
    {meta ? <span className="personal-turn-step-meta">{meta}</span> : null}
  </>;
  return <li className="personal-turn-step" data-kind={step.kind} data-state={step.state}>
    {detail
      ? <details><summary>{head}</summary><pre className={step.kind === "reasoning" ? "is-prose" : undefined}>{detail}</pre></details>
      : <div className="personal-turn-step-head">{head}</div>}
  </li>;
}

export function TurnStepsView({ steps, zh, live }: { steps: readonly TurnStep[]; zh: boolean; live: boolean }) {
  const failed = steps.filter(step => step.state === "failed").length;
  return <details className="personal-message-activity personal-turn-steps">
    <summary>{zh ? "执行过程" : "Work steps"}<span>{steps.length}</span>
      {failed ? <span className="personal-turn-steps-failed">{zh ? `${failed} 步失败` : `${failed} failed`}</span> : null}
    </summary>
    <ol>{steps.map(step => <StepRow key={step.id} step={step} zh={zh} live={live}/>)}</ol>
  </details>;
}
