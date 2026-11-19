import {useEffect, useRef, useState} from "react";
import {useWorkspaceI18n} from "./i18n";

type Footage = {
  generation_id: string; revision: number; title: string; status: string; seconds: number; resolution: string;
  prompt: string; model: string; task_id: string | null; error_code: string | null;
  estimate: {cny: number}; reported_usage_estimate_cny: number | null;
  quote: {source_url: string; checked_at: string}; watermark_requested: boolean;
  references: {role: string; sha256: string; source_ref: string}[];
  result: {files: Record<string, {sha256: string; bytes: number}>; measured_seconds: number; frames: number} | null;
};
type Library = {ok: boolean; generations: Footage[]; provider: {status: string}};
const ENDPOINT = "/api/chat/content-media";

/** Same content-ops workspace, same Goal. Reading drafts has no cloud effect. */
export function ContentGeneratedFootage({goalId}: {goalId: string}) {
  const {locale} = useWorkspaceI18n();
  const copy = (cn: string, en: string) => locale === "zh-CN" ? cn : en;
  const [library, setLibrary] = useState<Library | null>(null), [selectedId, select] = useState("");
  const [busyAction, setBusyAction] = useState<"submit" | "check" | "collect" | null>(null), [error, setError] = useState(""), [watch, setWatch] = useState(false);
  const busy = busyAction !== null;
  const epoch = useRef(0), readSequence = useRef(0), pending = useRef(false), watchDeadline = useRef<number | null>(null);
  const current = library?.generations.find(item => item.generation_id === selectedId) ?? library?.generations[0];
  const active = current && ["submitted", "queued", "running"].includes(current.status);
  function stopWatching() {watchDeadline.current = null; setWatch(false);}
  function startWatching() {watchDeadline.current = performance.now() + 5 * 60 * 1000; setWatch(true);}
  async function request(action: string, item?: Footage) {
    const response = await fetch(ENDPOINT, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({goal_id: goalId, action: `generation_${action}`,
        ...(item ? {generation_id: item.generation_id, expected_revision: item.revision} : {})})});
    if (!response.ok) throw new Error();
    return response.json();
  }
  async function reload() {
    const scope = epoch.current;
    const sequence = ++readSequence.current;
    try {
      const result: Library = await request("list");
      if (scope === epoch.current && sequence === readSequence.current) {setLibrary(result); setError("");}
    } catch {if (scope === epoch.current && sequence === readSequence.current) {stopWatching(); setError(copy("镜头暂不可读，请重新读取。", "Cannot read footage. Recheck this workspace."));}}
  }
  useEffect(() => {
    epoch.current += 1; pending.current = false; watchDeadline.current = null;
    setLibrary(null); select(""); setBusyAction(null); setWatch(false); void reload();
    return () => {epoch.current += 1;};
  }, [goalId]);
  async function operate(action: "submit" | "check" | "collect") {
    if (!current || pending.current) return;
    const scope = epoch.current;
    pending.current = true; setBusyAction(action); setError("");
    try {
      const result = await request(action, current);
      if (scope !== epoch.current) return;
      await reload();
      if (scope !== epoch.current) return;
      if (!result.ok) {stopWatching(); setError(result.error_code === "generation_provider_unavailable"
        ? copy("当前 Host 未加载云端生成凭据或扩展未启用。请让 Agent 修复后重试；镜头草稿已保留。", "The current host has no available cloud-generation credential or enabled extension. Ask your Agent to repair it; the draft is retained.")
        : copy("本次操作未完成。请检查原任务或保存状态；不会重新提交付费生成。", "The operation is incomplete. Check the existing task or collection state; no new paid task will be submitted."));}
      if (action === "submit" && result.ok) startWatching();
    } catch {if (scope === epoch.current) {stopWatching(); setError(copy("操作未确认。请先读取现有任务，勿重新提交付费生成。", "Operation unconfirmed. Read the existing task before submitting any new paid generation."));}}
    finally {if (scope === epoch.current) {pending.current = false; setBusyAction(null);}}
  }
  useEffect(() => {
    if (!watch) return;
    if (!active || watchDeadline.current === null) {stopWatching(); return;}
    const timer = setInterval(() => {
      if (watchDeadline.current === null || performance.now() >= watchDeadline.current) {stopWatching(); return;}
      if (pending.current) return;
      void operate("check");
    }, 10000);
    const expiry = setTimeout(stopWatching, Math.max(0, watchDeadline.current - performance.now()));
    return () => {clearInterval(timer); clearTimeout(expiry);};
  }, [watch, current?.generation_id, current?.status, goalId]);
  const names: Record<string, string> = {
    prepared: copy("待生成", "Ready to generate"), submitting: copy("提交结果待确认", "Submission awaiting confirmation"),
    submitted: copy("已提交，待查询状态", "Submitted; status not yet checked"),
    submission_unknown: copy("提交结果未知，请核对原任务", "Submission unknown; reconcile the original task"),
    queued: copy("云端排队中", "Queued in the cloud"), running: copy("云端生成中", "Generating in the cloud"),
    succeeded: copy("云端已完成，待保存", "Cloud result ready to save"), ready: copy("镜头已保存", "Footage saved"),
    failed: copy("云端生成失败", "Cloud generation failed"), rejected: copy("请求被拒绝", "Request rejected"),
    expired: copy("云端任务已超时", "Cloud task expired"), cancelled: copy("云端已取消", "Cancelled in the cloud"),
    damaged: copy("本机文件校验失败", "Local file verification failed"),
  };
  function file(name: string) {return `${ENDPOINT}/file?${new URLSearchParams({goal_id: goalId, generation_id: current!.generation_id, file: name,
    revision: String(current!.revision), sha256: current!.result!.files[name].sha256})}`;}
  return <section className="content-media" aria-label={copy("生成镜头", "Generate footage")}>
    <div className="content-media-heading"><div><h3>{copy("生成镜头", "Generate footage")}</h3>
      <p>{copy("用已准备的提示词和参考帧生成素材，再在本机添加准确文字、图表和配音。", "Generate source footage from a prepared prompt and reference frames. Add exact text, diagrams and narration locally.")}</p></div>
      <button type="button" disabled={busy} onClick={() => void reload()}>{copy("重新读取", "Recheck")}</button></div>
    {error ? <p role="alert">{error}</p> : null}
    {busyAction ? <p role="status">{busyAction === "collect" ? copy("正在下载并校验镜头…", "Downloading and verifying footage…") : busyAction === "submit" ? copy("正在提交一次生成请求…", "Submitting one generation request…") : copy("正在查询原任务…", "Checking the existing task…")}</p> : null}
    {!library && !error ? <p role="status">{copy("正在读取镜头…", "Loading footage…")}</p> : null}
    {library && !current ? <p>{copy("让 Agent 准备需要生成的镜头。普通本机视频制作仍可使用。", "Ask your Agent to prepare a footage intent. Ordinary local composition remains available.")}</p> : null}
    {current ? <div className="content-media-workspace">
      <nav aria-label={copy("选择镜头", "Choose footage")}>{library!.generations.map(item => <button type="button" key={item.generation_id} aria-pressed={item.generation_id === current.generation_id}
        onClick={() => {select(item.generation_id); stopWatching();}}><span>{item.title}</span><small>{names[item.status] ?? item.status}</small></button>)}</nav>
      <div className="content-media-result"><h4>{current.title}</h4>
        <p role="status">{names[current.status] ?? current.status} · {current.seconds}s · {current.resolution}</p>
        {current.status === "prepared" ? <><p>{copy("将提示词和参考帧发送至 Ark，生成一次镜头。预计", "Send the prompt and reference frames to Ark for one generation. Estimated")} ¥{current.estimate.cny.toFixed(2)}{copy("；实际用量与账单可能不同。", "; actual usage and billing may differ.")}</p>
          <button type="button" disabled={busy || library!.provider.status !== "ready"} onClick={() => void operate("submit")}>{copy("生成镜头", "Generate footage")}</button></> : null}
        {library!.provider.status !== "ready" ? <p role="status">{copy("云端生成扩展未启用，草稿已保留。", "Cloud generation extension unavailable; the draft is retained.")}</p> : null}
        {active || ["submitting", "submission_unknown", "succeeded", "damaged"].includes(current.status) ? <div className="content-media-downloads">
          <button type="button" disabled={busy} onClick={() => void operate("check")}>{copy("查询原任务", "Check existing task")}</button>
          {["succeeded", "damaged"].includes(current.status) ? <button type="button" disabled={busy} onClick={() => void operate("collect")}>{current.status === "damaged" ? copy("重新保存镜头", "Save footage again") : copy("保存镜头", "Save footage")}</button> : null}
          {active ? <button type="button" onClick={() => watch ? stopWatching() : startWatching()}>{watch ? copy("停止自动查询", "Stop checking") : copy("自动查询最多 5 分钟", "Check for up to 5 minutes")}</button> : null}</div> : null}
        {active ? <p>{copy("停止查询不会取消云端任务或费用。此服务不提供可安全自动重试的取消操作。", "Stopping checks does not cancel the cloud task or its charges. This service has no safe automatic cancellation operation.")}</p> : null}
        {current.error_code ? <p role="alert">{copy("本次操作尚未完成。原任务和草稿保留；让 Agent 检查任务读回或配置。", "The operation is incomplete. The existing task and draft are retained; ask your Agent to check task readback or configuration.")}</p> : null}
        {current.result && current.status === "ready" ? <><video controls preload="metadata" src={file("generated.mp4")} poster={file("poster.png")} />
          <div className="content-media-downloads"><a href={file("generated.mp4")} download="generated.mp4">{copy("下载镜头", "Download footage")}</a>
            {current.result.files["last-frame.jpg"] ? <a href={file("last-frame.jpg")} download="last-frame.jpg">{copy("尾帧 · 用于接续", "Last frame for continuation")}</a> : null}</div>
          <p>{copy("完整解码通过", "Full decode passed")} · {current.result.frames} {copy("帧", "frames")} · {current.result.measured_seconds.toFixed(2)}s</p></> : null}
        <details><summary>{copy("提示词、来源与用量", "Prompt, sources and usage")}</summary>
          <p>{current.prompt}</p><ul>{current.references.map(ref => <li key={ref.role}>{ref.role === "first_frame" ? copy("首帧", "First frame") : copy("尾帧", "Last frame")} · {ref.source_ref}<br /><small>SHA256 {ref.sha256}</small></li>)}</ul>
          <p>{current.model} · {copy("请求水印", "Watermark requested")}: {current.watermark_requested ? copy("是", "Yes") : copy("否", "No")}</p>
          <p><a href={current.quote.source_url} target="_blank" rel="noreferrer">{copy("估价来源", "Price source")}</a> · {current.quote.checked_at}</p>
          <p>{copy("按服务返回用量估算", "Estimate from reported usage")}: {current.reported_usage_estimate_cny == null ? copy("未取得", "Unavailable") : `¥${current.reported_usage_estimate_cny.toFixed(4)}`} · {copy("实际账单未知", "Actual invoice unknown")}</p>
          <p>{copy("云端生成的示意素材；文字、水印、连续性和创意效果仍需观看检查。不会自动发布。", "AI-generated illustrative footage. Inspect text, watermark, continuity and creative quality by watching. Nothing is published automatically.")}</p>
        </details>
      </div></div> : null}
  </section>;
}
