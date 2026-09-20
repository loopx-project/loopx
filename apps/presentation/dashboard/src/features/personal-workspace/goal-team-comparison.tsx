import {useEffect, useRef, useState} from "react";
import {readLoopXTeamWork, type DelegationDependency, type DelegationReadback} from "../../data/chat";
import {changedRange, comparisonSource} from "./team-artifact-comparison";

export function GoalTeamComparison({sessionId, result, zh}: {
  sessionId: string; result: DelegationReadback; zh: boolean;
}) {
  const [selection, setSelection] = useState<{link: DelegationDependency; source: DelegationReadback} | null>(null);
  const [outputIndex, setOutputIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++; setSelection(null); setError(""); setBusy(false); setOutputIndex(0);
    return () => {generation.current++;};
  }, [result, sessionId]);
  const artifacts = result.status === "accepted" && !result.error && !result.recovery_required ? result.artifacts ?? [] : [];
  const links = result.dependencies ?? [];
  async function compare(link: DelegationDependency) {
    const current = ++generation.current;
    setSelection(null); setError(""); setBusy(true); setOutputIndex(0);
    try {
      const source = await readLoopXTeamWork(sessionId, link.operation_id);
      if (current !== generation.current) return;
      if (!comparisonSource(link, source)) {
        setError(zh ? "指定来源版本无法核验，未展示对照。" : "The referenced source version cannot be verified. Comparison withheld.");
      } else setSelection({link, source});
    } catch {
      if (current === generation.current) setError(zh ? "来源读取失败，已清除上次对照。可重试或查看执行详情。" : "Source read failed; previous comparison cleared. Retry or inspect the execution.");
    } finally {if (current === generation.current) setBusy(false);}
  }
  if (!links.length || !artifacts.length) return null;
  const before = selection ? comparisonSource(selection.link, selection.source) : null;
  const after = artifacts[outputIndex];
  const range = before && after ? changedRange(before.text, after.text) : null;
  const labels = zh ? {revises: "修订依据", responds_to: "回应依据", uses: "使用依据"}
    : {revises: "Revision source", responds_to: "Response source", uses: "Input source"};
  return <section className="goal-team-comparison" aria-label={zh ? "依据与结果对照" : "Source and result comparison"} aria-busy={busy}>
    <div className="goal-team-comparison-heading"><h4>{zh ? "看清这次变化" : "See what changed"}</h4>
      <span>{zh ? "按需核验" : "On-demand verification"}</span></div>
    <div className="goal-team-comparison-sources">{links.map((link, index) => <button type="button"
      key={`${link.operation_id}:${link.input_ref}:${index}`} disabled={busy || link.state !== "current"}
      aria-pressed={selection?.link === link} onClick={() => void compare(link)}>
      <span>{labels[link.relation]}</span><strong>{link.ref}</strong>
      {link.state !== "current" ? <span>{zh ? "无法核验" : "Unavailable"}</span> : null}
    </button>)}</div>
    {busy ? <p role="status">{zh ? "正在读取指定来源版本…" : "Reading the referenced source version…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {before && after && range ? <>
      {artifacts.length > 1 ? <label>{zh ? "对照产物" : "Compare output"}<select value={outputIndex} onChange={e => setOutputIndex(Number(e.target.value))}>
        {artifacts.map((row, index) => <option key={`${row.ref}:${index}`} value={index}>{row.ref}</option>)}
      </select></label> : null}
      <div className="goal-team-comparison-grid">
        {([{kind: "source", title: zh ? "指定来源" : "Referenced source", artifact: before, lines: range.left, end: range.leftEnd},
          {kind: "result", title: zh ? "本次产物" : "This output", artifact: after, lines: range.right, end: range.rightEnd}]).map(pane => <article key={pane.kind}>
          <header><span>{pane.title}</span><strong>{pane.artifact.ref}</strong></header>
          <pre tabIndex={0} aria-label={`${pane.title}: ${pane.artifact.ref}`}>{pane.lines.map((line, i) => <span key={i}
            data-changed={i >= range.start && i < pane.end}>{line}{i < pane.lines.length - 1 ? "\n" : ""}</span>)}</pre>
        </article>)}
      </div>
      <p>{before.text === after.text ? (zh ? "两个版本的文本相同。" : "Both versions contain the same text.")
        : (zh ? "标记文本变化所在范围；不代表结论正确或已被采用。" : "Marks the range containing text changes; does not establish correctness or adoption.")}</p>
      <details><summary>{zh ? "核验的版本" : "Verified versions"}</summary>
        <code>{before.ref} · sha256:{before.sha256}</code><code>{after.ref} · sha256:{after.sha256}</code>
      </details>
    </> : <p>{zh ? "选择一份依据，与本次产物并排阅读。" : "Choose a source to read beside this output."}</p>}
  </section>;
}
