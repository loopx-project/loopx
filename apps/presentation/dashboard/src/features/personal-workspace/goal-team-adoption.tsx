import {useEffect, useRef, useState} from "react";
import {adoptLoopXTeamWork, fetchLoopXTeamWork, readLoopXTeamWork, type DelegationReadback} from "../../data/chat";
import {isMarkdownArtifact, TeamArtifactReport} from "./team-artifact-content";

const accepted = (row: DelegationReadback) => row.status === "accepted"
  && !row.error && !row.recovery_required && Boolean(row.artifacts?.length);
const sameResult = (expected: DelegationReadback, observed: DelegationReadback) => accepted(observed)
  && expected.operation_id === observed.operation_id && expected.request_id === observed.request_id
  && expected.agent_id === observed.agent_id && expected.todo_id === observed.todo_id
  && expected.artifacts?.length === observed.artifacts?.length
  && expected.artifacts?.every(row => observed.artifacts?.some(item => item.ref === row.ref && item.sha256 === row.sha256));
const uses = (source: DelegationReadback, consumer: DelegationReadback) => accepted(consumer)
  && consumer.operation_id !== source.operation_id && consumer.dependencies?.every(link => link.state === "current")
  && consumer.dependencies?.some(link =>
    link.relation === "uses" && link.state === "current" && link.operation_id === source.operation_id
    && source.artifacts?.some(row => row.ref === link.ref && row.sha256 === link.sha256));
const hasReceipt = (source: DelegationReadback, consumer: DelegationReadback) => source.adoptions?.some(row =>
  row.state === "current" && row.consumer_operation_id === consumer.operation_id
  && row.consumer_request_id === consumer.request_id && row.consumer_agent_id === consumer.agent_id
  && row.consumer_todo_id === consumer.todo_id && row.source_artifacts.length > 0
  && row.source_artifacts.every(item => source.artifacts?.some(artifact => item.ref === artifact.ref && item.sha256 === artifact.sha256)
    && consumer.dependencies?.some(link => link.relation === "uses" && link.state === "current"
      && link.operation_id === source.operation_id && link.ref === item.ref && link.sha256 === item.sha256))
  && row.consumer_artifacts.length === consumer.artifacts?.length
  && consumer.artifacts?.every(item => row.consumer_artifacts.some(artifact => item.ref === artifact.ref && item.sha256 === artifact.sha256)));

/** Explicit owner decision through the existing requester-scoped adoption owner.
 * Discovery/read/reload never writes; the backend rechecks every confirmation. */
export function GoalTeamAdoption({sessionId, result, zh, onObservation}: {
  sessionId: string; result: DelegationReadback; zh: boolean;
  onObservation: (result: DelegationReadback | null) => void;
}) {
  const [candidates, setCandidates] = useState<DelegationReadback[]>([]);
  const [selected, setSelected] = useState("");
  const [consumer, setConsumer] = useState<DelegationReadback | null>(null);
  const [artifactRef, setArtifactRef] = useState("");
  const [cursor, setCursor] = useState<string | null>(null);
  const [searched, setSearched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [incomplete, setIncomplete] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const generation = useRef(0);
  const inspectedPages = useRef(0);
  const versionKey = JSON.stringify(result.artifacts?.map(({ref, sha256}) => ({ref, sha256})));
  useEffect(() => {
    generation.current++; inspectedPages.current = 0;
    setCandidates([]); setSelected(""); setConsumer(null); setArtifactRef(""); setCursor(null);
    setSearched(false); setBusy(false); setError(""); setIncomplete(false); setUncertain(false);
    return () => {generation.current++;};
  }, [sessionId, result.operation_id, result.request_id, versionKey]);
  const unavailable = zh ? "指定版本或后续结果无法核验；请重新读取证据。" : "The referenced version or downstream result cannot be verified; recheck the evidence.";
  async function discover(next = false) {
    const current = ++generation.current;
    const previous = next ? candidates : [];
    if (!next) inspectedPages.current = 0;
    setBusy(true); setError(""); setConsumer(null); setUncertain(false);
    if (!next) {setCandidates([]); setSelected(""); setSearched(false); setIncomplete(false);}
    try {
      const page = await fetchLoopXTeamWork(sessionId, next && cursor ? cursor : undefined);
      if (current !== generation.current) return;
      if (page.has_more && (!page.next_cursor || (next && cursor && page.next_cursor <= cursor))) {
        throw new Error(zh ? "执行分页无法继续核验。" : "Execution paging cannot be verified.");
      }
      const matches: DelegationReadback[] = [];
      let missing = !page.page_readback_complete;
      // One bounded page at a time. A newer scope cancels queued reads.
      for (const row of page.items) {
        if (current !== generation.current) return;
        if (!row.operation_id || row.operation_id === result.operation_id || row.status !== "accepted" || row.recovery_required) continue;
        try {
          const value = await readLoopXTeamWork(sessionId, row.operation_id);
          if (value.operation_id === row.operation_id && uses(result, value)) matches.push(value);
        } catch {missing = true;}
      }
      if (current !== generation.current) return;
      const values = [...new Map([...previous, ...matches].map(row => [row.operation_id, row])).values()];
      inspectedPages.current++;
      setCandidates(values); setSelected(values[0]?.operation_id ?? ""); setSearched(true);
      setCursor(page.has_more ? page.next_cursor : null); setIncomplete(old => old || missing);
    } catch (failure) {
      if (current === generation.current) {
        setCandidates([]); setSelected(""); setCursor(null); setSearched(false);
        setError(failure instanceof Error ? failure.message : String(failure));
      }
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function read() {
    const current = ++generation.current;
    const expected = candidates.find(row => row.operation_id === selected);
    setBusy(true); setError(""); setConsumer(null); setUncertain(false);
    try {
      const source = await readLoopXTeamWork(sessionId, result.operation_id).catch(() => null);
      if (current !== generation.current) return;
      if (!source || !sameResult(result, source)) {onObservation(null); return;}
      onObservation(source);
      const downstream = await readLoopXTeamWork(sessionId, selected);
      if (current !== generation.current) return;
      if (!expected || !sameResult(expected, downstream) || !uses(source, downstream)) throw new Error(unavailable);
      setConsumer(downstream);
    } catch (failure) {
      if (current === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
    } finally {if (current === generation.current) setBusy(false);}
  }
  async function confirm() {
    if (!consumer) return;
    const current = ++generation.current;
    setBusy(true); setError("");
    try {
      let source: DelegationReadback;
      try {source = await adoptLoopXTeamWork(sessionId, result.operation_id, consumer.operation_id);}
      catch (failure) {
        // An acknowledgement may be lost after commit. Reconcile once without
        // another write; a later retry retains this same source/consumer pair.
        source = await readLoopXTeamWork(sessionId, result.operation_id);
        if (!hasReceipt(source, consumer)) throw failure;
      }
      if (current !== generation.current) return;
      if (!sameResult(result, source)) {onObservation(null); return;}
      onObservation(source);
      if (!hasReceipt(source, consumer)) throw new Error(unavailable);
      setUncertain(false);
    } catch (failure) {
      if (current === generation.current) {
        setUncertain(true);
        setError((zh ? "采用结果尚未核实。重试将核验并记录同一版本与后续结果，不会重新派工。"
          : "Adoption remains unconfirmed. Retry checks and records the same version and downstream result without dispatching work.")
          + " " + (failure instanceof Error ? failure.message : String(failure)));
      }
    } finally {if (current === generation.current) setBusy(false);}
  }
  if (!accepted(result)) return null;
  const recorded = consumer && hasReceipt(result, consumer) && !uncertain;
  const artifact = consumer?.artifacts?.find(row => row.ref === artifactRef)
    ?? consumer?.artifacts?.find(row => isMarkdownArtifact(row.ref)) ?? consumer?.artifacts?.[0];
  return <details className="goal-team-adoption" aria-busy={busy}>
    <summary>{zh ? "确认采用于后续结果" : "Confirm adoption into a downstream result"}</summary>
    <p>{zh ? "先阅读使用此版本的已验收后续结果，再由此对话的协调身份记录采用。不会启动任务或补足独立验收证据。"
      : "Read an accepted downstream result using this version, then record adoption as this conversation's coordinator. This starts no work and supplies no independent-verifier evidence."}</p>
    <button type="button" disabled={busy || uncertain} onClick={() => void discover()}>{zh ? "查找使用此版本的后续结果" : "Find downstream results using this version"}</button>
    {busy ? <p role="status">{zh ? "正在核验指定版本与后续结果…" : "Checking exact versions and downstream results…"}</p> : null}
    {incomplete ? <p>{zh ? "部分执行无法核验；列表不代表全部后续结果。" : "Some executions cannot be verified; this list does not represent all downstream results."}</p> : null}
    {searched && !candidates.length ? <p>{zh ? "已检查的工作中没有可核验的使用结果。" : "No verifiable uses result was found in the inspected work."}</p> : null}
    {cursor && inspectedPages.current < 10 ? <button type="button" disabled={busy || uncertain} onClick={() => void discover(true)}>{zh ? "继续查找下一页" : "Search the next page"}</button> : null}
    {cursor && inspectedPages.current >= 10 ? <p>{zh ? "本次已检查十页；仍有未检查工作，请核实原请求。" : "Ten pages inspected; more work remains uninspected. Reconcile the original request."}</p> : null}
    {candidates.length ? <>
      <label>{zh ? "后续结果" : "Downstream result"}
        <select aria-label={zh ? "后续结果" : "Downstream result"} value={selected} disabled={busy || uncertain} onChange={event => {setSelected(event.target.value); setConsumer(null); setError("");}}>
          {candidates.map(row => <option key={row.operation_id} value={row.operation_id}>{row.agent_id} · {row.operation_id}</option>)}
        </select>
      </label>
      <button type="button" disabled={busy || uncertain} onClick={() => void read()}>{zh ? "阅读后续结果" : "Read downstream result"}</button>
    </> : null}
    {consumer ? <>
      {artifact ? <TeamArtifactReport key={`${consumer.operation_id}:${artifact.ref}`} artifact={artifact} zh={zh}/> : null}
      {consumer.artifacts && consumer.artifacts.length > 1 ? <label>{zh ? "后续结果的其他产物" : "Other downstream artifacts"}
        <select aria-label={zh ? "后续结果的其他产物" : "Other downstream artifacts"} value={artifact?.ref}
          onChange={event => setArtifactRef(event.target.value)}>
          {consumer.artifacts.map(row => <option key={row.ref} value={row.ref}>{row.ref}</option>)}
        </select>
      </label> : null}
      {recorded ? <p role="status">{zh ? "采用已记录，指定版本与后续结果当前有效。" : "Adoption recorded; the referenced version and downstream result are current."}</p>
        : <button type="button" disabled={busy} onClick={() => void confirm()}>{uncertain
          ? (zh ? "重试同一采用决定" : "Retry this adoption decision") : (zh ? "确认采用于此结果" : "Confirm adoption into this result")}</button>}
    </> : null}
    {error ? <p role="alert">{error}</p> : null}
  </details>;
}
