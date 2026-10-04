import { useEffect, useRef, useState } from "react";
import type { summarizePerformanceProfile } from "../../../../../../loopx/control_plane/capabilities/performance_profile";
import { useWorkspaceI18n } from "./i18n";
import "./performance-diagnosis.css";

type Result = ReturnType<typeof summarizePerformanceProfile>;

export function PerformanceDiagnosisPanel() {
  const { locale } = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const worker = useRef<Worker | null>(null);
  const input = useRef<HTMLInputElement | null>(null);
  const [ranking, setRanking] = useState<"self" | "inclusive">("self");
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => () => worker.current?.terminate(), []);

  function clear(resetInput = true) {
    worker.current?.terminate(); worker.current = null;
    setBusy(false); setError(null); setResult(null);
    if (resetInput && input.current) input.current.value = "";
  }
  function inspect(file: File | undefined) {
    clear(false);
    if (!file) return;
    if (file.size > 16 * 1024 * 1024) {
      setError(zh ? "文件超过 16 MiB，请缩短采样时间。" : "Profile exceeds 16 MiB; select a shorter capture."); return;
    }
    setBusy(true);
    try {
      const active = new Worker(new URL("./performance-diagnosis-worker.ts", import.meta.url), { type: "module" });
      worker.current = active;
      const finish = (message: { result?: Result; error?: string }) => {
        if (worker.current !== active) return;
        active.terminate(); worker.current = null; setBusy(false);
        setResult(message.result ?? null); setError(message.error ?? null);
      };
      active.onmessage = (event: MessageEvent<{ result?: Result; error?: string }>) => finish(event.data);
      active.onerror = () => finish({ error: zh ? "解析进程不可用，请重试或使用 CLI 检查。" : "Profile reader unavailable; retry or inspect through the CLI." });
      active.postMessage(file);
    } catch {
      worker.current?.terminate(); worker.current = null;
      setBusy(false); setError(zh ? "浏览器无法启动解析进程。" : "This browser could not start the profile reader.");
    }
  }
  return <details className="personal-capability-scope-note personal-performance-diagnosis" data-testid="performance-diagnosis">
    <summary>{zh ? "性能诊断 · 查看本地采样" : "Performance diagnosis · inspect a local capture"}</summary>
    <p>{zh ? "选择 Speedscope 或 Node CPU JSON，比较独立线程的热点。文件只在浏览器内解析，不发送、不保存，不启动采样工具。" : "Choose Speedscope or Node CPU JSON to inspect each thread's hotspots. Files are processed only in this browser, never sent or saved. No profiler is started."}</p>
    <label>{zh ? "选择本地 profile（最多 16 MiB）" : "Choose a local profile (up to 16 MiB)"}
      <input accept=".json,.cpuprofile,application/json" ref={input} type="file" onChange={event => inspect(event.target.files?.[0])} />
    </label>
    <button className="personal-secondary-action" onClick={() => clear()} type="button">{zh ? "清除 / 取消" : "Clear / cancel"}</button>
    {busy ? <p role="status">{zh ? "正在解析，可取消…" : "Inspecting; you can cancel…"}</p> : null}
    {error ? <p role="alert">{zh ? "未能读取采样：" : "Could not inspect capture: "}{error}</p> : null}
    {result ? <div aria-live="polite">
      <p role="status">{zh ? `已解析 ${result.profiles.length} 份独立采样` : `Inspected ${result.profiles.length} independent profiles`}</p>
      <p>{zh ? "采样权重不是执行耗时、CPU 利用率或已证明的根因；包含子调用的时间有重叠，不能相加。" : "Sample weights are not task latency, CPU utilization or proven root cause. Inclusive times overlap and must not be added."}</p>
      <label>{zh ? "热点排序" : "Rank hotspots by"}<select value={ranking} onChange={event => setRanking(event.target.value as "self" | "inclusive")}>
        <option value="self">{zh ? "函数自身时间" : "Self time"}</option><option value="inclusive">{zh ? "含子调用时间" : "Inclusive time"}</option>
      </select></label>
      {result.profiles.map((profile, index) => <details key={index} open={index === 0}>
        <summary>{profile.name} · {profile.observed_weight_ms.toFixed(2)} ms</summary>
        <div className="personal-performance-hotspots"><table>
          <caption>{zh ? "采样热点（前 15 项）" : "Recorded hotspots (up to 15)"}</caption>
          <thead><tr><th>{zh ? "函数" : "Function"}</th><th>{zh ? "自身 ms" : "Self ms"}</th><th>{zh ? "含子调用 ms" : "Inclusive ms"}</th></tr></thead>
          <tbody>{(ranking === "self" ? profile.self_hotspots : profile.inclusive_hotspots).map((row, i) => <tr key={i}><td>{row.name}<small>{row.file ? ` · ${row.file}${row.line ? `:${row.line}` : ""}` : ""}</small></td><td>{row.self_ms.toFixed(2)}</td><td>{row.inclusive_ms.toFixed(2)}</td></tr>)}</tbody>
        </table></div>
      </details>)}
    </div> : null}
    <details><summary>{zh ? "如何采样" : "How to capture"}</summary>
      <p>{zh ? "由 Host 对有权限的目标执行采样；使用 CLI 生成准确 argv，安装与执行由 Host 负责。采样后回到这里选择文件。" : "Ask your Host to profile an owned target. The CLI prepares exact argv; the Host owns installation and execution. Return here to select the capture."}</p>
      <code>loopx performance-diagnosis plan --help</code>
    </details>
  </details>;
}
