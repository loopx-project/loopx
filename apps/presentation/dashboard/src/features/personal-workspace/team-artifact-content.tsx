import {useState} from "react";
import type {DelegationReadback} from "../../data/chat";
import {MarkdownText} from "./markdown.js";

export type TeamArtifact = NonNullable<DelegationReadback["artifacts"]>[number];
export const isMarkdownArtifact = (ref: string) => /\.(md|markdown)$/i.test(ref);

/** Evidence stays inert: no HTML interpretation, embedded images or remote fetches. */
export function TeamArtifactContent({artifact, label, raw = false}: {artifact: TeamArtifact; label: string; raw?: boolean}) {
  if (!raw && isMarkdownArtifact(artifact.ref)) return <div className="goal-team-report" aria-label={label} tabIndex={0}>
    <MarkdownText text={artifact.text} report/>
  </div>;
  return <pre tabIndex={0} aria-label={label}>{artifact.text}</pre>;
}

export function TeamArtifactReport({artifact, zh}: {artifact: TeamArtifact; zh: boolean}) {
  const [raw, setRaw] = useState(false);
  return <article className="goal-team-artifact">
    <header><h4>{artifact.ref}</h4>{isMarkdownArtifact(artifact.ref) ? <button type="button" aria-pressed={raw} onClick={() => setRaw(value => !value)}>
      {raw ? (zh ? "阅读报告" : "Read report") : (zh ? "查看原文" : "View source")}
    </button> : null}</header>
    <TeamArtifactContent artifact={artifact} raw={raw} label={`${zh ? "证据内容" : "Evidence content"}: ${artifact.ref}`}/>
    <details><summary>{zh ? "产物版本" : "Artifact version"}</summary><code>{artifact.sha256}</code></details>
  </article>;
}
