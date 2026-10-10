import {useMemo, useRef, useState} from "react";
import {captureContentReference, planReferenceDraft, searchContentReferences, type ContentReference} from "../../../../../../loopx/control_plane/capabilities/content_reference";
import {useWorkspaceI18n} from "./i18n";
import "./content-reference-workbench.css";

/** Browser-local artifacts only. Imports are never sent to the Chat server. */
export function ContentReferenceWorkbench() {
  const {locale} = useWorkspaceI18n();
  const zh = locale === "zh-CN";
  const copy = (cn: string, en: string) => zh ? cn : en;
  const [library, setLibrary] = useState<Record<string, unknown> | null>(null);
  const [query, setQuery] = useState("");
  const [structure, setStructure] = useState("");
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<ContentReference | null>(null);
  const [subject, setSubject] = useState("");
  const [facts, setFacts] = useState("");
  const [draft, setDraft] = useState<ReturnType<typeof planReferenceDraft> | null>(null);
  const [captureText, setCaptureText] = useState("");
  const [captureResult, setCaptureResult] = useState<ReturnType<typeof captureContentReference> | null>(null);
  const importGeneration = useRef(0);
  const fileInput = useRef<HTMLInputElement>(null);
  const retrieval = useMemo(() => {
    if (!library) return {found: null, error: ""};
    try {return {found: searchContentReferences({library, query, structure}), error: ""};}
    catch (reason) {return {found: null, error: reason instanceof Error ? reason.message : String(reason)};}
  }, [library, query, structure]);
  const found = retrieval.found;

  function download(value: unknown, name: string) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2) + "\n"], {type: "application/json"}));
    const link = document.createElement("a"); link.href = url; link.download = name; link.click(); URL.revokeObjectURL(url);
  }
  function resetImportState() {importGeneration.current += 1; setLibrary(null); setSelected(null); setDraft(null); setCaptureResult(null); setError(""); setCaptureText(""); setFacts(""); setSubject("");}
  function clear() {
    resetImportState(); setQuery(""); setStructure("");
    if (fileInput.current) fileInput.current.value = "";
  }
  async function importCatalog(file?: File) {
    resetImportState();
    const generation = importGeneration.current;
    if (!file) return;
    try {
      if (file.size > 2 * 1024 * 1024) throw new Error(copy("目录超过 2 MiB，请先在原存储中缩小读取范围。", "Catalog exceeds 2 MiB; narrow it in the original store first."));
      const value = JSON.parse(await file.text()) as Record<string, unknown>;
      if (generation !== importGeneration.current) return;
      searchContentReferences({library: value});
      setLibrary(value);
    } catch (reason) {if (generation === importGeneration.current) setError(reason instanceof Error ? reason.message : String(reason));}
  }
  function capture() {
    setError(""); setCaptureResult(null); setDraft(null);
    try {setCaptureResult(captureContentReference({...JSON.parse(captureText), library}));}
    catch (reason) {setError(reason instanceof Error ? reason.message : String(reason));}
  }
  function prepareDraft() {
    setDraft(null); setError("");
    try {setDraft(planReferenceDraft({library, reference_id: selected?.id, expected_source_revision: selected?.source_revision, subject, facts: facts.split("\n").filter(value => value.trim())}));}
    catch (reason) {setError(reason instanceof Error ? reason.message : String(reason));}
  }
  return <section aria-label={copy("素材与风格检索", "Reference styles")} className="personal-capability-field-summary personal-content-reference">
    <p>{copy("选择你有权使用的现有素材目录。内容仅在本页内处理；离开或清除后移除。", "Choose an existing catalog you are authorized to use. Contents stay in this page and are removed when you leave or clear it.")}</p>
    <label>{copy("导入原目录 JSON", "Import original catalog JSON")}<input ref={fileInput} accept=".json,application/json" type="file" onChange={event => void importCatalog(event.target.files?.[0])} /></label>
    <p>{copy("收录仅生成待审文件。原目录、素材状态和来源权限仍由原 owner 管理；当前页面不执行写入、恢复或发布。", "Capture creates a review artifact. The original owner retains catalog, lifecycle and source permissions; this page cannot apply, restore or publish.")}</p>
    {error ? <p role="alert" className="personal-machine-error">{error}</p> : null}
    {library ? <>
      <label>{copy("主题或用途", "Topic or use case")}<input maxLength={2000} value={query} onChange={event => {setQuery(event.target.value); setSelected(null); setDraft(null);}} /></label>
      <label>{copy("表达结构", "Structure")}<input maxLength={2000} value={structure} onChange={event => {setStructure(event.target.value); setSelected(null); setDraft(null);}} /></label>
    </> : null}
    {retrieval.error ? <p role="alert" className="personal-machine-error">{retrieval.error}</p> : null}
    {found ? <>
      <p aria-live="polite">{found.references.length} / {found.total_count} · {copy("状态未知", "Unknown lifecycle")}: {found.unknown_lifecycle_count}</p>
      {!found.references.length ? <p>{copy("没有匹配素材；可修改检索词或导入更新后的目录。", "No matching reference. Change the filters or import the updated catalog.")}</p> : null}
      {found.references.map(reference => <article key={reference.id} className="personal-capability-linked-setting">
        <div><strong>{reference.title}</strong><p>{reference.author} · <a href={reference.source_url} rel="noreferrer" target="_blank">{copy("原文", "Source")}</a></p>
          <p>{reference.structure.join(" → ")}</p><small>{reference.reuse_boundary ?? copy("使用边界未知", "Reuse boundary unknown")}</small>
          <p>{copy("开场", "Opening")}: {reference.opening ?? copy("未知", "unknown")} · {copy("语气", "Tone")}: {reference.tone ?? copy("未知", "unknown")}</p>
          <p>{copy("版本", "Revision")}: {reference.source_revision ?? copy("未知", "unknown")}</p>
          <p>{copy("阅读范围", "Reading boundary")}: {reference.reading_boundary ?? copy("未知", "unknown")}</p>
          {reference.engagement ? <small>{reference.engagement.observed_at} · {JSON.stringify(reference.engagement.counts)}</small> : null}
        </div>
        <button type="button" onClick={() => {setSelected(reference); setDraft(null); setError("");}}>{copy("参考此结构", "Use this structure")}</button>
      </article>)}
      {selected ? <div>
        <label>{copy("自己的主题", "Your subject")}<input value={subject} onChange={event => {setSubject(event.target.value); setDraft(null);}} /></label>
        <label>{copy("自己的已核验事实，每行一条", "Your verified facts, one per line")}<textarea rows={4} value={facts} onChange={event => {setFacts(event.target.value); setDraft(null);}} /></label>
        <button onClick={prepareDraft} type="button">{copy("准备署名提纲", "Prepare attributed outline")}</button>
      </div> : null}
      {draft ? <section aria-live="polite"><strong>{draft.subject}</strong><ol>{draft.steps.map(step => <li key={step.role}>{step.role}: {step.fact ?? copy("需补充自己的证据", "Add your own evidence")}</li>)}</ol>
        <p>{draft.attribution_text}</p><p>{draft.source_map[0]?.reuse_boundary}</p><ul>{draft.caveats.map(value => <li key={value}>{value}</li>)}</ul>
        <button type="button" onClick={() => download(draft, "reference-outline.private.json")}>{copy("下载私有提纲", "Download private outline")}</button>
      </section> : null}
      <details><summary>{copy("收录或修订来源与风格", "Capture or correct source/style")}</summary>
        <p>{copy("输入 reference 和 expected_source_revision。旧条目无版本时显式填 null；新版本需保留原 ID。缺失的使用边界不会被猜测。", "Provide reference and expected_source_revision. Use explicit null for an unversioned legacy entry and preserve its ID. Missing reuse conditions are never inferred.")}</p>
        <label>{copy("来源与风格 JSON", "Source/style JSON")}<textarea rows={10} value={captureText} onChange={event => {setCaptureText(event.target.value); setCaptureResult(null);}} /></label>
        <button type="button" onClick={capture}>{copy("准备收录文件", "Prepare capture artifact")}</button>
        {captureResult ? <div aria-live="polite"><p>{copy("已准备，尚未写入原目录", "Prepared; original catalog unchanged")}: {captureResult.reference.reference_ref}</p>
          <button type="button" onClick={() => download(captureResult, "reference-capture.private.json")}>{copy("下载私有收录文件", "Download private capture artifact")}</button></div> : null}
      </details>
    </> : null}
    <button onClick={clear} type="button">{copy("清除本页素材", "Clear page materials")}</button>
  </section>;
}
