import {useEffect, useRef, useState} from "react";
import {readLoopXTeamWork, type DelegationDependency, type DelegationReadback} from "../../data/chat";

type VerifiedLink = {link: DelegationDependency; source: DelegationReadback};
type Episode = {original: VerifiedLink; response: VerifiedLink; downstream: DelegationReadback | null};

/** Resolve only an explicitly requested correction path. Reads are on demand and never start work. */
export function GoalTeamEpisode({sessionId, result, zh, onInspect}: {
  sessionId: string; result: DelegationReadback; zh: boolean; onInspect: (operationId: string) => void;
}) {
  const original = result.dependencies?.find(link => link.relation === "revises");
  const response = result.dependencies?.find(link => link.relation === "responds_to" && link.operation_id !== original?.operation_id);
  const adoption = result.adoptions?.find(row => row.state === "current") ?? result.adoptions?.[0];
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  useEffect(() => {
    generation.current++; setEpisode(null); setError(""); setBusy(false);
    return () => {generation.current++;};
  }, [result, sessionId]);
  if (!original || !response) return null;
  async function trace() {
    const current = ++generation.current;
    setEpisode(null); setError(""); setBusy(true);
    try {
      const [first, challenge, revised, downstream] = await Promise.all([
        readLoopXTeamWork(sessionId, original!.operation_id),
        readLoopXTeamWork(sessionId, response!.operation_id),
        readLoopXTeamWork(sessionId, result.operation_id),
        adoption?.state === "current" ? readLoopXTeamWork(sessionId, adoption.consumer_operation_id) : Promise.resolve(null),
      ]);
      const matches = (link: DelegationDependency, source: DelegationReadback) =>
        link.state === "current" && source.status === "accepted" && !source.recovery_required && !source.error
        && source.artifacts?.some(artifact => artifact.ref === link.ref && artifact.sha256 === link.sha256);
      const responseBindsOriginal = challenge.dependencies?.some(link =>
        link.relation === "responds_to" && link.operation_id === original!.operation_id && matches(link, first));
      const revisionMatchesObservation = revised.status === "accepted" && revised.agent_id === result.agent_id
        && !revised.recovery_required && !revised.error
        && revised.artifacts?.length === result.artifacts?.length
        && result.artifacts?.every(expected => revised.artifacts?.some(
          artifact => artifact.ref === expected.ref && artifact.sha256 === expected.sha256))
        && revised.dependencies?.some(link => link.relation === "revises" && link.operation_id === original!.operation_id && matches(link, first))
        && revised.dependencies?.some(link => link.relation === "responds_to" && link.operation_id === response!.operation_id && matches(link, challenge));
      const refreshedAdoption = adoption?.state === "current" ? revised.adoptions?.find(row =>
        row.consumer_operation_id === adoption.consumer_operation_id && row.requester_agent_id === adoption.requester_agent_id
        && row.consumer_agent_id === adoption.consumer_agent_id && row.state === "current") : null;
      const downstreamBindsRevision = downstream?.dependencies?.some(link =>
        link.relation === "uses" && link.operation_id === revised.operation_id && matches(link, revised));
      const downstreamMatchesReceipt = downstream?.status === "accepted" && !downstream.recovery_required && !downstream.error
        && refreshedAdoption?.consumer_artifacts.every(expected => downstream.artifacts?.some(
          artifact => artifact.ref === expected.ref && artifact.sha256 === expected.sha256));
      const sourceMatchesReceipt = refreshedAdoption?.source_artifacts.every(expected => revised.artifacts?.some(
        artifact => artifact.ref === expected.ref && artifact.sha256 === expected.sha256));
      if (result.status !== "accepted" || result.error || result.recovery_required
          || !matches(original!, first) || !matches(response!, challenge)
          || !responseBindsOriginal || !revisionMatchesObservation
          || (adoption?.state === "current" && (!sourceMatchesReceipt || !downstreamBindsRevision || !downstreamMatchesReceipt))) {
        throw new Error("linked evidence unavailable");
      }
      if (current === generation.current) setEpisode({original: {link: original!, source: first},
        response: {link: response!, source: challenge}, downstream});
    } catch {
      if (current === generation.current) setError(zh ? "关联执行或版本已变化；请重新读取证据。" : "A linked execution or version changed; recheck the evidence.");
    } finally {if (current === generation.current) setBusy(false);}
  }
  return <section className="goal-team-episode" aria-label={zh ? "纠偏证据路径" : "Correction evidence path"} aria-busy={busy}>
    <div className="goal-team-episode-heading"><h4>{zh ? "追踪一次纠偏" : "Trace a correction"}</h4>
      <button type="button" disabled={busy} onClick={() => void trace()}>{zh ? "核验关联执行" : "Verify linked work"}</button></div>
    <p>{zh ? "按需核验原始版本、复核回应、修订及后续采用。关系和验收不能替代对异议内容的判断。"
      : "Verify the original version, review response, revision and downstream use on demand. Relationships and acceptance do not judge the objection's content."}</p>
    {busy ? <p role="status">{zh ? "正在核验关联执行与指定版本…" : "Checking linked executions and exact versions…"}</p> : null}
    {error ? <p role="alert">{error}</p> : null}
    {episode ? <ol>
      <li><span>01 · {zh ? "原始版本" : "Original version"}</span><strong>{episode.original.source.agent_id}</strong>
        <button type="button" onClick={() => onInspect(episode.original.link.operation_id)}>{zh ? "阅读原始产物" : "Read original"}</button></li>
      <li><span>02 · {zh ? "复核回应" : "Review response"}</span><strong>{episode.response.source.agent_id}</strong>
        <button type="button" onClick={() => onInspect(episode.response.link.operation_id)}>{zh ? "阅读回应与证据" : "Read response and evidence"}</button></li>
      <li><span>03 · {zh ? "修订产物 · 当前验收有效" : "Revised output · currently accepted"}</span><strong>{result.agent_id}</strong>
        <span>{zh ? "与原始版本的正文对照见下方" : "Compare with the original below"}</span>
        <span>{zh ? "独立验收 · 证据未提供" : "Independent verification · evidence not provided"}</span>
        <span>{zh ? "当前读回未提供独立验收者与指定版本回执。" : "This readback does not provide an independent verifier and an exact-version receipt."}</span></li>
      <li><span>04 · {episode.downstream ? (zh ? "后续结果 · 当前验收与采用记录有效" : "Downstream result · acceptance and adoption current")
        : adoption ? (zh ? "采用证据无法核验" : "Adoption evidence unavailable") : (zh ? "尚无请求方采用" : "No requester adoption")}</span>
        {episode.downstream && adoption ? <><strong>{episode.downstream.agent_id} · {adoption.requester_agent_id}</strong>
          <button type="button" onClick={() => onInspect(adoption.consumer_operation_id)}>{zh ? "阅读后续结果" : "Read downstream result"}</button></> : null}</li>
    </ol> : null}
  </section>;
}
