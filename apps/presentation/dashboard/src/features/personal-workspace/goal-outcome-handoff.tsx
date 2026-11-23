import {useState} from "react";
import {acceptanceRequest} from "../../data/goal-acceptance-actions";
import type {OutcomeRequirements} from "../../data/goal-acceptance-observation";

type Selection = {from_agent_id: string; to_agent_id: string; outcomes: {id: string; revision: number}[]};
type Request = {document: Selection; expected_provider_revision: string; operation_id: string};

export function GoalOutcomeHandoff({goalId, declaration, canManage, revision, onRefresh, zh}: {
  goalId: string; declaration: OutcomeRequirements; canManage: boolean; revision: string | null;
  onRefresh: () => Promise<void>; zh: boolean;
}) {
  const [source, setSource] = useState("");
  const [receiver, setReceiver] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [prepared, setPrepared] = useState<Request | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const missing = declaration.outcomes.filter(row => row.owner_registration_missing);
  const sources = [...new Set(missing.map(row => row.owner_agent_id))];
  const from = source || sources[0];
  const candidates = missing.filter(row => row.owner_agent_id === from);
  const receivers = declaration.handoff_receivers ?? [];
  const ready = canManage && !!revision && !busy;
  async function submit() {
    if (!ready || !revision) return;
    const outcomes = candidates.filter(row => selected.includes(row.id)).map(({id, revision}) => ({id, revision}));
    if (!prepared && (!outcomes.length || !receivers.includes(receiver))) return;
    const request = prepared ?? {document: {from_agent_id: from, to_agent_id: receiver, outcomes},
      expected_provider_revision: revision, operation_id: `handoff-ui:${crypto.randomUUID()}`};
    setBusy(true);
    try {
      await acceptanceRequest(goalId, {...request, action: prepared ? "handoff" : "preview-handoff"});
      if (prepared) {
        setPrepared(null);
        setSelected([]);
        setNotice(zh ? "已交接选中的成果。" : "Selected outcomes transferred.");
        await onRefresh();
      } else {
        setPrepared(request);
        setNotice("");
      }
    } catch {
      setNotice(zh ? "未能确认结果。请刷新归属，或重试同一请求。" : "Could not confirm the result. Refresh ownership or retry the same request.");
    } finally {
      setBusy(false);
    }
  }
  if ((!sources.length || !canManage) && !prepared && !notice) return null;
  return <details className="goal-outcome-handoff"><summary>{zh ? "交接退役身份的成果" : "Transfer retired Agent outcomes"}</summary>
    <form onSubmit={event => {event.preventDefault(); void submit();}}>
      {!prepared ? <fieldset disabled={!ready}>
        <label>{zh ? "原负责身份" : "Previous owner"}<select value={from ?? ""} onChange={event => {setSource(event.target.value); setSelected([]);}}>
          {sources.map(id => <option key={id}>{id}</option>)}</select></label>
        <label>{zh ? "接手身份" : "Receiving Agent"}<select value={receiver} onChange={event => setReceiver(event.target.value)}>
          <option value="">{zh ? "选择已注册身份" : "Choose a registered Agent"}</option>
          {receivers.map(id => <option key={id}>{id}</option>)}</select></label>
        {candidates.map(row => <label key={row.id}><input type="checkbox" checked={selected.includes(row.id)}
          onChange={event => setSelected(event.target.checked ? [...selected, row.id] : selected.filter(id => id !== row.id))}/>
          {row.objective} <small>{row.id}</small></label>)}
      </fieldset> : <div>
        <p>{prepared.document.from_agent_id} → {prepared.document.to_agent_id}</p>
        <ul>{prepared.document.outcomes.map(row => <li key={row.id}>{declaration.outcomes.find(item => item.id === row.id)?.objective ?? row.id} · {row.id} (v{row.revision})</li>)}</ul>
      </div>}
      <div className="goal-outcome-handoff-actions"><button type="submit" disabled={!ready || (!prepared && (!receivers.includes(receiver) || !selected.length))}>
        {prepared ? (zh ? "应用交接" : "Apply handoff") : (zh ? "预览交接" : "Preview handoff")}</button>
      {prepared ? <button type="button" disabled={busy} onClick={() => {setPrepared(null); setSelected([]); setNotice("");}}>
        {zh ? "重新选择" : "Change selection"}</button> : null}
      <button type="button" disabled={busy} onClick={() => void onRefresh()}>{zh ? "刷新归属" : "Refresh ownership"}</button></div>
    </form>
    {notice ? <p role="status">{notice}</p> : null}
  </details>;
}
