import {useEffect, useRef, useState} from "react";
import {useWorkspaceI18n} from "./i18n";

type ImageIntent = {
  image_id: string; revision: number; title: string; prompt: string; status: string; model: string;
  width: number; height: number; estimate: {cny: number}; error_code: string | null;
  reported_usage_estimate_cny: number | null; watermark_requested: boolean;
  quote: {source_url: string; checked_at: string};
  references: {source_ref: string; sha256: string}[];
  result: {files: Record<string, {sha256: string; bytes: number}>; usage: {generated_images: number; output_tokens: number | null}} | null;
};
type Library = {images: ImageIntent[]; provider: {status: string}};
const ENDPOINT = "/api/chat/content-media";

export function ContentGeneratedImages({goalId}: {goalId: string}) {
  const {locale} = useWorkspaceI18n();
  const copy = (zh: string, en: string) => locale === "zh-CN" ? zh : en;
  const [library,setLibrary] = useState<Library | null>(null), [selectedId,select] = useState("");
  const [busy,setBusy] = useState(false), [error,setError] = useState("");
  const epoch = useRef(0), sequence = useRef(0), pending = useRef(false);
  const current = library?.images.find(i=>i.image_id === selectedId) ?? library?.images[0];
  async function request(action: string, item?: ImageIntent) {
    const response = await fetch(ENDPOINT,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({goal_id:goalId,action:`image_${action}`,
      ...(item ? {image_id:item.image_id,expected_revision:item.revision} : {})})});
    if (!response.ok) throw new Error();
    return response.json();
  }
  async function reload() {
    const scope = epoch.current, read = ++sequence.current;
    try {const value: Library = await request("list"); if(scope === epoch.current && read === sequence.current) {setLibrary(value);setError("");}}
    catch {if(scope === epoch.current && read === sequence.current) setError(copy("图像暂不可读，请重新读取。","Cannot read images. Recheck this workspace."));}
  }
  useEffect(()=>{
    epoch.current += 1;pending.current = false;setBusy(false);setLibrary(null);setError("");select("");void reload();
    return ()=>{epoch.current += 1;};
  },[goalId]);
  async function operate(action: "submit" | "collect") {
    if (!current || pending.current) return;
    const scope = epoch.current;pending.current = true;setBusy(true);setError("");
    try {
      const result = await request(action,current);
      if(scope !== epoch.current) return;
      await reload();
      if(!result.ok) setError(result.error_code === "image_provider_unavailable"
        ? copy("图像生成暂不可用。请让 Agent 检查扩展与图像专用凭据；草稿保留，未提交生成。","Image generation is unavailable. Ask your Agent to check the extension and image credential; the draft is retained without a generation submission.")
        : copy("生成或保存未确认。仅检查本机已保存的响应，不会重新提交付费请求。","Generation or collection is unconfirmed. Only inspect the saved local response; no paid request will be repeated."));
    } catch {if(scope === epoch.current) setError(copy("请求结果未确认，请重新读取并检查已保存响应。","Request unconfirmed. Recheck and inspect the saved response."));}
    finally {if(scope === epoch.current) {pending.current = false;setBusy(false);}}
  }
  const names: Record<string,string> = {
    prepared:copy("待生成","Ready to generate"),submitting:copy("请求结果待确认","Request awaiting confirmation"),
    submission_unknown:copy("请求结果未知","Request outcome unknown"),rejected:copy("请求被拒绝","Request rejected"),
    ready:copy("图像已保存","Image saved"),damaged:copy("本机图像校验失败","Local image verification failed"),
  };
  function file(name: string) {return `${ENDPOINT}/file?${new URLSearchParams({goal_id:goalId,image_id:current!.image_id,file:name,
    revision:String(current!.revision),sha256:current!.result!.files[name].sha256})}`;}
  return <section className="content-media" aria-label={copy("生成图像","Generate images")}>
    <div className="content-media-heading"><div><h3>{copy("生成图像","Generate an illustration")}</h3>
      <p>{copy("让 Agent 准备提示词与参考图，在本机保留生成素材，再添加准确文字与图表。","Have your Agent prepare the prompt and reference. Save the generated source locally, then add exact text and diagrams.")}</p></div>
      <button type="button" disabled={busy} onClick={()=>void reload()}>{copy("重新读取","Recheck")}</button></div>
    {error ? <p role="alert">{error}</p> : null}
    {busy ? <p role="status">{copy("正在发送并读取一次生图响应…","Sending and reading one image-generation response…")}</p> : null}
    {!library && !error ? <p role="status">{copy("正在读取图像…","Loading images…")}</p> : null}
    {library && !current ? <p>{copy("把需要解释的内容交给 Agent，它会准备可检查的生图草稿。","Tell your Agent what to explain; it can prepare an image intent for review.")}</p> : null}
    {current ? <div className="content-media-workspace"><nav aria-label={copy("选择图像","Choose an image")}>{library!.images.map(item=><button type="button" key={item.image_id} disabled={busy} aria-pressed={item.image_id === current.image_id} onClick={()=>{select(item.image_id);setError("");}}><span>{item.title}</span><small>{names[item.status] ?? item.status}</small></button>)}</nav>
      <div className="content-media-result"><h4>{current.title}</h4><p role="status">{names[current.status] ?? current.status} · {current.width} × {current.height}px</p>
        {current.status === "prepared" ? <><p>{copy("将提示词与参考图发送至 Ark，生成一张图。预计","Send the prompt and reference to Ark for one image. Estimated")} ¥{current.estimate.cny.toFixed(2)}{copy("；实际账单未知。","; actual invoice unknown.")}</p>
          <button type="button" disabled={busy || library!.provider.status !== "ready"} onClick={()=>void operate("submit")}>{copy("生成一张图","Generate one image")}</button></> : null}
        {library!.provider.status !== "ready" ? <p role="status">{copy("云端媒体扩展未启用，草稿已保留。","Cloud media extension unavailable; the draft is retained.")}</p> : null}
        {["submitting","submission_unknown","damaged"].includes(current.status) ? <><p>{copy("该请求不能按云端任务查询或自动重发。可读取本机已保存的响应；若没有响应，须先核实服务端用量。","This request cannot be polled as a cloud task or automatically resubmitted. Read its saved local response; if absent, reconcile provider usage first.")}</p>
          <button type="button" disabled={busy} onClick={()=>void operate("collect")}>{copy("读取已保存响应","Read saved response")}</button></> : null}
        {current.error_code && !error ? <p role="alert">{copy("本次操作尚未完成，草稿和原请求状态已保留。","The operation is incomplete; the draft and original request state are retained.")}</p> : null}
        {current.status === "ready" && current.result ? <><img src={file("generated.png")} alt={current.title} onError={() => setError(copy("无法读取这个版本的图像，请重新读取。","Cannot read this image version. Recheck the saved result."))} />
          <div className="content-media-downloads"><a href={file("generated.png")} download="generated.png">{copy("下载图像","Download image")}</a><a href={file("original.png")} download="original.png">{copy("服务原始图像","Original provider image")}</a></div>
          <p>{copy("AI 生成的示意素材；完整解码通过，内容含义仍需检查。","AI-generated illustration. Full decode passed; inspect its meaning.")}</p></> : null}
        <details><summary>{copy("提示词、来源与用量","Prompt, sources and usage")}</summary><p>{current.prompt}</p>
          <ul>{current.references.map(ref=><li key={ref.sha256}>{ref.source_ref}<br/><small>SHA256 {ref.sha256}</small></li>)}</ul>
          <p>{current.model} · {copy("请求水印","Watermark requested")}: {current.watermark_requested ? copy("是","Yes") : copy("否","No")}</p>
          <p><a href={current.quote.source_url} target="_blank" rel="noreferrer">{copy("估价来源","Price source")}</a> · {current.quote.checked_at}</p>
          <p>{copy("服务报告生成数量","Reported output count")}: {current.result?.usage.generated_images ?? copy("未取得","Unavailable")} · {copy("按用量估价","Usage estimate")}: {current.reported_usage_estimate_cny == null ? "—" : `¥${current.reported_usage_estimate_cny.toFixed(2)}`} · {copy("实际账单未知","Actual invoice unknown")}</p>
          <p>{copy("图像是机制示意，不是执行轨迹。创意、文字、水印需人工检查，不会自动发布。","This is a mechanism illustration, not an execution trace. Inspect creative quality, text and watermark. Nothing is published automatically.")}</p>
        </details></div></div> : null}
  </section>;
}
