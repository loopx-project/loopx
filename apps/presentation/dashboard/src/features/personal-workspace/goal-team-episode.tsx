import {useEffect, useRef, useState} from "react";
import {readLoopXTeamWork, type DelegationAdoption, type DelegationDependency, type DelegationReadback} from "../../data/chat";

type VerifiedLink = {link: DelegationDependency; source: DelegationReadback};
type Episode = {original: VerifiedLink; response: VerifiedLink;
  adoption: DelegationAdoption | null; downstream: DelegationReadback | null};

/** Resolve only an explicitly requested correction path. Reads are on demand and never start work. */
export function GoalTeamEpisode({sessionId, result, zh, onInspect, onObservation}: {
  sessionId: string; result: DelegationReadback; zh: boolean; onInspect: (operationId: string) => void;
  onObservation: (result: DelegationReadback | null) => void;
}) {
  const original = result.dependencies?.find(link => link.relation === "revises");
  const response = result.dependencies?.find(link => link.relation === "responds_to" && link.operation_id !== original?.operation_id);
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    generation.current++; setEpisode(null); setBusy(false);
    return () => {generation.current++;};
  // A checked observation updates the surrounding evidence too. Keep this
  // trace mounted for that same execution; changing executions resets it.
  }, [sessionId, result.operation_id, result.request_id]);
  if (!original || !response) return null;
  async function trace() {
    const current = ++generation.current;
    setEpisode(null); setBusy(true);
    try {
      const [first, challenge, revised] = await Promise.all([
        readLoopXTeamWork(sessionId, original!.operation_id),
        readLoopXTeamWork(sessionId, response!.operation_id),
        readLoopXTeamWork(sessionId, result.operation_id),
      ]);
      const matches = (link: DelegationDependency, source: DelegationReadback) =>
        link.state === "current" && source.operation_id === link.operation_id
        && source.status === "accepted" && source.current_use?.state !== "unavailable" && !source.recovery_required && !source.error
        && source.artifacts?.some(artifact => artifact.ref === link.ref && artifact.sha256 === link.sha256);
      const responseBindsOriginal = challenge.dependencies?.some(link =>
        link.relation === "responds_to" && link.operation_id === original!.operation_id && matches(link, first));
      const revisionMatchesObservation = revised.operation_id === result.operation_id
        && revised.request_id === result.request_id && revised.todo_id === result.todo_id
        && revised.status === "accepted" && revised.current_use?.state !== "unavailable" && revised.agent_id === result.agent_id
        && !revised.recovery_required && !revised.error
        && revised.artifacts?.length === result.artifacts?.length
        && result.artifacts?.every(expected => revised.artifacts?.some(
          artifact => artifact.ref === expected.ref && artifact.sha256 === expected.sha256))
        && revised.dependencies?.some(link => link.relation === "revises" && link.operation_id === original!.operation_id && matches(link, first))
        && revised.dependencies?.some(link => link.relation === "responds_to" && link.operation_id === response!.operation_id && matches(link, challenge));
      if (result.status !== "accepted" || result.current_use?.state === "unavailable" || result.error || result.recovery_required
          || !matches(original!, first) || !matches(response!, challenge)
          || !responseBindsOriginal || !revisionMatchesObservation) {
        throw new Error("linked evidence unavailable");
      }
      // Use this check's owner observation, including adoption added or restored since the report opened.
      const adoption = revised.adoptions?.find(row => row.state === "current") ?? revised.adoptions?.[0] ?? null;
      const refreshedAdoption = adoption?.state === "current" ? adoption : null;
      // Adoption is a separate observation: its loss must not erase a current correction.
      const downstream = refreshedAdoption
        ? await readLoopXTeamWork(sessionId, refreshedAdoption.consumer_operation_id).catch(() => null) : null;
      const downstreamBindsRevision = downstream?.dependencies?.some(link =>
        link.relation === "uses" && link.operation_id === revised.operation_id && matches(link, revised));
      const downstreamMatchesReceipt = downstream?.status === "accepted" && downstream.current_use?.state !== "unavailable" && !downstream.recovery_required && !downstream.error
        && downstream.operation_id === refreshedAdoption?.consumer_operation_id
        && downstream.request_id === refreshedAdoption?.consumer_request_id
        && downstream.agent_id === refreshedAdoption?.consumer_agent_id
        && downstream.todo_id === refreshedAdoption?.consumer_todo_id
        && refreshedAdoption.consumer_artifacts.length > 0
        && refreshedAdoption?.consumer_artifacts.every(expected => downstream.artifacts?.some(
          artifact => artifact.ref === expected.ref && artifact.sha256 === expected.sha256));
      const sourceMatchesReceipt = refreshedAdoption && refreshedAdoption.source_artifacts.length > 0
        && refreshedAdoption.source_artifacts.every(expected => revised.artifacts?.some(
        artifact => artifact.ref === expected.ref && artifact.sha256 === expected.sha256));
      const verifiedDownstream = sourceMatchesReceipt && downstreamBindsRevision && downstreamMatchesReceipt ? downstream : null;
      if (current === generation.current) {
        // This read-only projection includes the consumer observation. A
        // transport failure cannot leave its earlier success in the details.
        onObservation({...revised, adoptions: revised.adoptions?.map(row => row === adoption && !verifiedDownstream
          ? {...row, state: "unavailable"} : row)});
        setEpisode({original: {link: original!, source: first},
          response: {link: response!, source: challenge}, adoption,
          downstream: verifiedDownstream});
      }
    } catch {
      if (current === generation.current) {
        // A failed core check invalidates the whole evidence view, including
        // earlier acceptance, comparison and adoption details.
        onObservation(null);
      }
    } finally {if (current === generation.current) setBusy(false);}
  }
  return <section className="goal-team-episode" aria-label={zh ? "纠偏证据路径" : "Correction evidence path"} aria-busy={busy}>
    <div className="goal-team-episode-heading"><h4>{zh ? "追踪一次纠偏" : "Trace a correction"}</h4>
      <button type="button" disabled={busy} onClick={() => void trace()}>{zh ? "核验关联执行" : "Verify linked work"}</button></div>
    <p>{zh ? "按需核验原始版本、复核回应、修订及后续采用。关系和验收不能替代对异议内容的判断。"
      : "Verify the original version, review response, revision and downstream use on demand. Relationships and acceptance do not judge the objection's content."}</p>
    {busy ? <p role="status">{zh ? "正在核验关联执行与指定版本…" : "Checking linked executions and exact versions…"}</p> : null}
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
        : episode.adoption ? (zh ? "采用证据无法核验" : "Adoption evidence unavailable") : (zh ? "尚无请求方采用" : "No requester adoption")}</span>
        {episode.downstream && episode.adoption ? <><strong>{episode.downstream.agent_id} · {episode.adoption.requester_agent_id}</strong>
          <button type="button" onClick={() => onInspect(episode.adoption!.consumer_operation_id)}>{zh ? "阅读后续结果" : "Read downstream result"}</button></> : null}</li>
    </ol> : null}
  </section>;
}
