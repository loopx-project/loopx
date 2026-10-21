import {useEffect, useRef, useState} from "react";
import {useWorkspaceI18n} from "./i18n";
import "./content-media-workbench.css";

type Provider = {status: string; enabled: boolean | null; next_action: string | null};
type Composition = {
  composition_id: string; revision: number; status: string; title: string; duration_seconds: number;
  shots: {title: string; seconds: number; kind: string; boundary: string; source_ref: string; framing?: "full_frame"}[];
  result: {files: Record<string, {sha256: string; bytes: number}>; qa: {frames: number; full_decode: string; narration: {mode: string}; blender_scenes?: {project_reopened: boolean}[]}; cost: {render_wall_seconds: number}} | null;
  provider: Provider;
};
type Library = {ok: boolean; compositions: Composition[]; provider: Provider};
const ENDPOINT = "/api/chat/content-media";

export function ContentMediaWorkbench({goalId}: {goalId: string}) {
  const {locale} = useWorkspaceI18n();
  const copy = (cn: string, en: string) => locale === "zh-CN" ? cn : en;
  const [library, setLibrary] = useState<Library | null>(null), [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const pending = useRef(false), generation = useRef(0), readSequence = useRef(0);
  const selected = library?.compositions.find(item => item.composition_id === selectedId) ?? library?.compositions[0];
  async function request(action: string, item?: Composition) {
    const response = await fetch(ENDPOINT, {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({goal_id: goalId, action, ...(item ? {composition_id: item.composition_id, expected_revision: item.revision} : {})})});
    if (!response.ok) throw new Error();
    return await response.json();
  }
  async function reload() {
    const current = generation.current;
    const sequence = ++readSequence.current;
    try {
      const value: Library = await request("list");
      if (current === generation.current && sequence === readSequence.current) {setLibrary(value); setError("");}
    } catch {if (current === generation.current && sequence === readSequence.current) setError(copy("作品暂不可读，请重新读取。", "Cannot read compositions. Recheck the workspace."));}
  }
  useEffect(() => {
    generation.current += 1; setLibrary(null); setSelectedId(""); pending.current = false; setBusy(false); void reload();
    return () => {generation.current += 1;};
  }, [goalId]);
  const running = library?.compositions.some(item => item.status === "rendering");
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => void reload(), 2000);
    return () => clearInterval(timer);
  }, [running, goalId]);
  async function operate(action: "render" | "cancel") {
    if (!selected || pending.current) return;
    pending.current = true; setBusy(true); setError("");
    const current = generation.current;
    try {
      const value = await request(action, selected);
      if (current !== generation.current) return;
      await reload();
      if (!value.ok) setError(copy("渲染器未就绪。请启用已验证的本机视频扩展后重试。", "Renderer unavailable. Enable the verified local video extension, then retry."));
    } catch {if (current === generation.current) setError(copy("操作未确认。先重新读取，避免重复启动。", "Operation unconfirmed. Recheck before retrying."));}
    finally {if (current === generation.current) {pending.current = false; setBusy(false);}}
  }
  function file(name: string) {return `${ENDPOINT}/file?${new URLSearchParams({goal_id: goalId, composition_id: selected!.composition_id, file: name})}`;}
  const status: Record<string, string> = {
    prepared: copy("待制作", "Ready to render"), rendering: copy("正在制作", "Rendering"), ready: copy("作品已生成", "Video ready"),
    failed: copy("制作失败，可修复后重试", "Render failed; repair and retry"), cancelled: copy("已取消，可重新制作", "Cancelled; ready to retry"),
    interrupted: copy("上次制作中断，可重新制作", "Previous render interrupted; retry available"), damaged: copy("结果校验失败，请重新制作", "Result verification failed; render again"),
  };
  const providerReady = library?.provider.status === "ready";
  const hasBlender = selected?.shots.some(shot => shot.kind === "blender");
  return <section className="content-media" aria-label={copy("视频制作", "Video production")}>
    <div className="content-media-heading"><div><h3>{copy("制作视频", "Produce a video")}</h3>
      <p>{copy("让 Agent 准备脚本、来源和时间线，在这里制作并查看作品。", "Have your Agent prepare the story, sources and timeline. Render and review the result here.")}</p></div>
      <button type="button" onClick={() => void reload()} disabled={busy}>{copy("重新读取", "Recheck")}</button></div>
    {error ? <p role="alert">{error}</p> : null}
    {!library && !error ? <p role="status">{copy("正在读取作品…", "Loading compositions…")}</p> : null}
    {library && !providerReady ? <p className="content-media-notice" role="status">{copy("本机视频扩展未就绪。作品与来源保留，安装并启用 loopx-media 后即可制作。", "The local video extension is unavailable. Prepared work is retained; install and enable loopx-media to render.")}</p> : null}
    {library && !selected ? <p className="content-media-empty">{copy("还没有准备好的作品。把想讲的内容交给 Agent，它会通过 Content-Ops 保存可制作的脚本。", "No prepared compositions yet. Tell your Agent what to explain; it can save a renderable brief through Content-Ops.")}</p> : null}
    {selected ? <div className="content-media-workspace">
      <nav aria-label={copy("选择作品", "Choose a composition")}>{library!.compositions.map(item => <button key={item.composition_id} type="button" aria-pressed={item.composition_id === selected.composition_id}
        onClick={() => setSelectedId(item.composition_id)}><span>{item.title}</span><small>{item.duration_seconds}s · {status[item.status] ?? item.status}</small></button>)}</nav>
      <div className="content-media-result"><div className="content-media-title"><div><h4>{selected.title}</h4><p role="status">{status[selected.status] ?? selected.status} · {selected.duration_seconds}s · {copy("修订", "Revision")} {selected.revision}</p></div>
        {selected.status === "rendering" ? <button type="button" disabled={busy} onClick={() => void operate("cancel")}>{copy("取消制作", "Cancel render")}</button>
          : <button type="button" disabled={busy || !providerReady || selected.status === "ready"} onClick={() => void operate("render")}>{copy("制作视频", "Render video")}</button>}</div>
        {hasBlender && selected.status !== "ready" ? <p>{copy("包含 Blender 三维场景。制作需要已安装的本机 Blender 扩展依赖；作品会保留可编辑工程与颜色、深度画面。", "Includes Blender scenes. Rendering needs the local Blender extra; the editable project, color and depth frames are retained.")}</p> : null}
        {selected.status === "ready" && selected.result ? <><video key={`${selected.composition_id}:${selected.result.files["video.mp4"].sha256}`} controls preload="metadata" poster={file("poster.png")} src={file("video.mp4")}>
          <track kind="captions" src={file("captions.vtt")} label={copy("字幕", "Captions")} default /></video>
          <div className="content-media-downloads"><a href={file("video.mp4")} download="video.mp4">{copy("下载视频", "Download video")}</a><a href={file("captions.srt")} download="captions.srt">{copy("字幕", "Subtitles")}</a><a href={file("transcript.txt")} download="transcript.txt">{copy("文字稿", "Transcript")}</a>
            {selected.result.files["blender-project.zip"] ? <a href={file("blender-project.zip")} download="blender-project.zip">{copy("Blender 工程与画面", "Blender project and passes")}</a> : null}</div>
          <p>{copy("完整解码通过", "Full decode passed")} · {selected.result.qa.frames} {copy("帧", "frames")} · {copy("渲染耗时", "Render time")} {selected.result.cost.render_wall_seconds}s</p>
          {selected.result.qa.blender_scenes?.length ? <p>{copy("三维工程已重新打开并检查，颜色与深度帧完整。", "The 3D project was reopened and checked; color and depth frames are complete.")}</p> : null}
        </> : <ol className="content-media-timeline">{selected.shots.map((shot, i) => <li key={i}><span>{shot.title}</span><small>{shot.seconds}s · {shot.framing === "full_frame" ? copy("大画面 · ", "Full frame · ") : ""}{shot.boundary}</small></li>)}</ol>}
        {["failed", "damaged"].includes(selected.status) ? <p>{hasBlender ? copy("请让 Agent 检查 Blender 依赖、场景范围与制作时限。修复本机依赖后可重试；修改场景需保存新修订。", "Ask your Agent to check the Blender extra, scene bounds and render time. Retry after dependency repair; save a new revision for scene changes.") : copy("请让 Agent 检查素材、字体和配音时长；修改脚本后保存新修订，或修复本机依赖后重试。", "Ask your Agent to check sources, fonts and narration timing. Save a new revision for story changes, or repair the local dependencies and retry.")}</p> : null}
        <details><summary>{copy("来源与检查", "Sources and checks")}</summary><ul>{selected.shots.map((shot, i) => <li key={i}>{shot.title} — {shot.framing === "full_frame" ? copy("大画面 · ", "Full frame · ") : ""}{shot.boundary}<br /><small>{shot.source_ref}</small></li>)}</ul>
          <p>{copy("本机私有作品。编码通过不代表创意质量、理解效果或发布许可；请观看并试听。", "Private local work. Encoding does not establish creative quality, audience understanding or publication approval. Watch and listen before sharing.")}</p></details>
      </div></div> : null}
  </section>;
}
