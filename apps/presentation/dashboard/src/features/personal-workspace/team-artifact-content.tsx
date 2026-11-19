import {useState} from "react";
import type {DelegationReadback} from "../../data/chat";
import {MarkdownText} from "./markdown.js";
import {downloadReport} from "./report-download";

export type TeamArtifact = NonNullable<DelegationReadback["artifacts"]>[number];
export const isMarkdownArtifact = (ref: string) => /\.(md|markdown)$/i.test(ref);

/** The managed result API carries a content type rather than an artifact filename. */
export function managedReportArtifact(contentType: string, sha256: string, text: string): TeamArtifact {
  const ref = contentType === "text/markdown" ? "accepted-report.md"
    : contentType === "application/json" ? "accepted-report.json" : "accepted-report.txt";
  return {ref, sha256, text};
}

/** Evidence stays inert: no HTML interpretation, embedded images or remote fetches. */
export function TeamArtifactContent({artifact, label, raw = false}: {artifact: TeamArtifact; label: string; raw?: boolean}) {
  if (!raw && isMarkdownArtifact(artifact.ref)) return <div className="goal-team-report" aria-label={label} tabIndex={0}>
    <MarkdownText text={artifact.text}/>
  </div>;
  return <pre tabIndex={0} aria-label={label}>{artifact.text}</pre>;
}

export function TeamArtifactReport({artifact, zh, heading = artifact.ref}: {artifact: TeamArtifact; zh: boolean; heading?: string}) {
  const [raw, setRaw] = useState(false);
  const [downloadError, setDownloadError] = useState(false);
  function download() {
    setDownloadError(false);
    try {downloadReport(artifact.ref, artifact.sha256, artifact.text);}
    catch {setDownloadError(true);}
  }
  return <article className="goal-team-artifact">
    <header><h4>{heading}</h4><div className="goal-team-artifact-actions">{isMarkdownArtifact(artifact.ref) ? <button type="button" aria-pressed={raw} onClick={() => setRaw(value => !value)}>
      {raw ? (zh ? "阅读报告" : "Read report") : (zh ? "查看原文" : "View source")}
    </button> : null}<button type="button" onClick={download} title={zh ? "保存当前显示版本的 UTF-8 原文" : "Save the displayed version as UTF-8 source"}>
      {zh ? "下载原文" : "Download source"}
    </button></div></header>
    {downloadError ? <p role="alert">{zh ? "无法开始下载，请重试。" : "Could not start the download. Please retry."}</p> : null}
    <TeamArtifactContent artifact={artifact} raw={raw} label={`${zh ? "证据内容" : "Evidence content"}: ${artifact.ref}`}/>
    <details><summary>{zh ? "产物版本" : "Artifact version"}</summary><code>{artifact.sha256}</code></details>
  </article>;
}
