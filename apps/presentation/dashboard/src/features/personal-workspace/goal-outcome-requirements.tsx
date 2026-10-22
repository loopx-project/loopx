import {useId, useState} from "react";
import {ChevronDown} from "lucide-react";
import {acceptanceRequest} from "../../data/goal-acceptance-actions";
import type {OutcomeRequirements} from "../../data/goal-acceptance-observation";
import {useWorkspaceI18n} from "./i18n";
import {ContextHelp} from "./context-help";

type Requirement = OutcomeRequirements["outcomes"][number]["requirements"][number];
// Presentation of the existing native vocabulary, never a new acceptance decision.
const stateOrder: Requirement["status"][] = ["failed", "held", "stale", "partial", "unverified", "accepted"];
function displayState(item: Requirement): Requirement["status"] {
  return item.status === "unverified" && item.agent_review && !item.agent_review.basis_current ? "stale" : item.status;
}
function stateLabels(zh: boolean) {
  return {accepted: zh ? "检查通过" : "Checks passing", unverified: zh ? "未核验" : "Unverified",
    stale: zh ? "需复查" : "Review needed", failed: zh ? "检查失败" : "Checks failed",
    partial: zh ? "部分检查" : "Partial checks", held: zh ? "关联待确认" : "Association held"};
}
function stateLabel(item: Requirement, fresh: boolean, zh: boolean) {
  const label = item.status === "unverified" && item.agent_review?.basis_current
    ? {met: zh ? "自查满足 · 未独立核验" : "Agent reports met · unverified", unmet: zh ? "自查有缺口" : "Agent found gaps", uncertain: zh ? "自查尚不确定" : "Agent uncertain"}[item.agent_review.conclusion]
    : stateLabels(zh)[displayState(item)];
  return fresh ? label : `${zh ? "历史" : "Recorded"} · ${label}`;
}
function requirementKey(outcome: OutcomeRequirements["outcomes"][number], requirement: Requirement): string {
  // Native IDs are opaque and may contain colons. Keep both identities
  // unambiguous instead of relying on a separator the vocabulary forbids.
  return JSON.stringify([outcome.id, requirement.id]);
}

/** One native declaration/readback owner. The form only appends a requirement. */
export function GoalOutcomeRequirements({goalId, declaration, fresh, canManage, providerRevision, onRefresh}: {
  goalId: string; declaration: OutcomeRequirements; fresh: boolean; canManage: boolean;
  providerRevision: string | null; onRefresh: () => Promise<void>;
}) {
  const zh = useWorkspaceI18n().locale === "zh-CN";
  const [all, setAll] = useState(false);
  const listId = useId();
  const current = fresh && declaration.current_lifetime;
  const rows = declaration.outcomes.flatMap(outcome => outcome.requirements.map(requirement => ({outcome, requirement})));
  const counts = stateOrder.map(state => ({state, count: rows.filter(row => displayState(row.requirement) === state).length}));
  const attention = rows.filter(({requirement}) => !["accepted", "unverified"].includes(displayState(requirement))
    || current && requirement.agent_review?.basis_current && requirement.agent_review.conclusion === "unmet")
    .sort((a, b) => stateOrder.indexOf(displayState(a.requirement)) - stateOrder.indexOf(displayState(b.requirement)));
  const preview = attention.slice(0, 3);
  const visible = new Set((all ? rows : preview).map(row => requirementKey(row.outcome, row.requirement)));
  const labels = stateLabels(zh);
  if (!declaration.outcomes.length) return null;
  return <section className={`goal-outcomes${all ? " is-expanded" : ""}`} aria-label={zh ? "支撑成果" : "Supporting outcomes"}>
    <header className="goal-outcomes-heading"><h4>{zh ? "支撑成果" : "Supporting outcomes"} <span>{declaration.outcomes.length}</span></h4>
      <ContextHelp label={zh ? "成果状态与范围" : "Outcome status and scope"}>
        <p>{zh ? "数量统计要求，不代表 Goal 已完成。Agent 自查与独立检查分别记录；自查依据变化时显示需复查。完整要求、证据和来源可展开阅读。" : "Counts describe requirements, not Goal completion. Agent reviews and independent checks remain distinct; changed review bases need another review. Expand a requirement for its complete text, evidence and sources."}</p>
        <p>{zh ? "默认显示最多三项需要处理的要求，按失败、关联待确认、需复查和部分检查排列，其余要求仍在全部列表。未配置自动检查不影响保留成果要求。" : "The preview shows up to three requirements needing attention: failed, held, changed or partial checks. All other requirements remain in the full list. Automated checks are optional."}</p>
      </ContextHelp>
    </header>
    <div className="goal-outcomes-counts" aria-label={zh ? "要求状态" : "Requirement states"}>
      {!current ? <span role="status">{zh ? "记录待刷新" : "Refresh needed"}</span> : null}
      {counts.filter(item => item.count).map(item => <span key={item.state} className={`is-${current ? item.state : "recorded"}`}><strong>{item.count}</strong> {labels[item.state]}</span>)}
      {!rows.length ? <span>{zh ? "尚无要求" : "No requirements yet"}</span> : null}
    </div>
    {rows.length ? <div className="goal-outcomes-distribution" aria-hidden="true">{counts.filter(item => item.count).map(item => <i key={item.state} className={`is-${current ? item.state : "recorded"}`} style={{flexGrow: item.count}}/>)}</div> : null}
    <div id={listId} className="goal-outcomes-list">
      {declaration.outcomes.map(outcome => <Outcome key={`${goalId}:${outcome.id}`} goalId={goalId} outcome={outcome}
        fresh={current} canManage={canManage && declaration.current_lifetime} visible={visible} all={all}
        revision={providerRevision} onRefresh={onRefresh} zh={zh}/>)}
    </div>
    <button className="goal-outcomes-more" type="button" aria-expanded={all} aria-controls={listId} onClick={() => setAll(value => !value)}>
      {all ? (zh ? "收起完整列表" : "Collapse full list") : (zh ? `查看全部 ${rows.length} 项要求` : `View all ${rows.length} requirements`)}<ChevronDown aria-hidden size={15}/>
    </button>
  </section>;
}
function Outcome({goalId, outcome, fresh, canManage, revision, onRefresh, zh, visible, all}: {
  goalId: string; outcome: OutcomeRequirements["outcomes"][number]; fresh: boolean; canManage: boolean;
  revision: string | null; onRefresh: () => Promise<void>; zh: boolean;
  visible: Set<string>; all: boolean;
}) {
  const [text, setText] = useState("");
  const [groupsOpen, setGroupsOpen] = useState<Partial<Record<Requirement["status"], boolean>>>({failed: true, held: true, stale: true, partial: true});
  const [busy, setBusy] = useState(false), [notice, setNotice] = useState("");
  const [pending, setPending] = useState<Record<string, unknown> | null>(null);
  async function save() {
    if (busy || !fresh || !canManage || !revision || !text.trim()) return;
    const identity = crypto.randomUUID();
    const request = pending ?? {action: "refine", expected_provider_revision: revision, operation_id: `outcome-ui:${identity}`,
      refinement: {outcome_id: outcome.id,
        requirements: [{id: `requirement-${identity}`, description: text.trim()}],
        source: {ref: `owner:${identity}`, summary: text.trim()}}};
    setPending(request); setBusy(true); setNotice("");
    try {
      await acceptanceRequest(goalId, request);
      await onRefresh(); setText(""); setPending(null);
      setNotice(zh ? "已保留新要求；原有要求保持，尚未运行检查。" : "Requirement retained alongside earlier requirements. No checks ran.");
    } catch {
      // Preserve the exact operation after ambiguity. A fresh read can reveal
      // a committed result; a conflicting basis requires explicit rebase.
      setNotice(zh ? "结果未确认。先刷新；重试保留原操作，版本冲突时重新准备。" : "Result unconfirmed. Refresh first; retry retains the original operation. Rebase after a version conflict.");
      await onRefresh();
    } finally {setBusy(false);}
  }
  const shown = all || outcome.requirements.some(item => visible.has(requirementKey(outcome, item)));
  return <article className="goal-outcome" hidden={!shown}>
    <header><h5>{outcome.objective}</h5><span>{outcome.requirements.length} {zh ? "项要求" : "requirements"}</span></header>
    {stateOrder.map(state => {
      const requirements = outcome.requirements.filter(item => displayState(item) === state);
      if (!requirements.length) return null;
      const hasVisible = requirements.some(item => visible.has(requirementKey(outcome, item)));
      return <details className="goal-outcome-status-group" key={state} hidden={!hasVisible} open={!all || groupsOpen[state] === true}
        onToggle={event => {const open = event.currentTarget.open; if (all) setGroupsOpen(previous => ({...previous, [state]: open}));}}>
        <summary hidden={!all}>{!fresh ? (zh ? "历史 · " : "Recorded · ") : ""}{stateLabels(zh)[state]} · {requirements.length}</summary>
        <ul className="goal-overview-criteria goal-outcome-criteria">{requirements.map(requirement => <li key={requirement.id} hidden={!visible.has(requirementKey(outcome, requirement))}>
          <RequirementRow requirement={requirement} fresh={fresh} zh={zh}/>
        </li>)}</ul>
      </details>;
    })}
    <details hidden={!all}><summary>{zh ? "关联工作与来源" : "Linked work and sources"}</summary>
      <p>{outcome.objective}</p><p>{outcome.owner_agent_id} · {zh ? "版本" : "Revision"} {outcome.revision}</p>
      <ul>{outcome.work.map(work => <li key={work.todo_id}><code>{work.todo_id}</code> · {work.status}</li>)}</ul>
      {!outcome.work.length ? <p>{zh ? "尚未关联执行任务" : "No execution task linked"}</p> : null}
      <ul>{outcome.sources.map(source => <li key={source.ref}>{source.summary}</li>)}</ul>
    </details>
    {/* Keep an existing draft readable while its same-source write basis is unavailable. */}
    {canManage || text || pending ? <details hidden={!all && !text && !pending}><summary>{zh ? "补充要求" : "Add a requirement"}</summary>
      <form onSubmit={event => {event.preventDefault(); void save();}}>
        <label>{zh ? "新增要求（保留已有要求）" : "New requirement (earlier requirements stay)"}
          <textarea value={text} maxLength={512} disabled={busy || !!pending} onChange={event => setText(event.target.value)}/></label>
        <button type="submit" disabled={busy || !fresh || !canManage || !text.trim()}>{zh ? "保留要求" : "Retain requirement"}</button>
        <button type="button" disabled={busy} onClick={() => void onRefresh()}>{zh ? "刷新结果" : "Refresh result"}</button>
        {pending && !busy ? <button type="button" disabled={!fresh || !canManage || !revision} onClick={() => {setPending({...pending, operation_id: `outcome-ui:${crypto.randomUUID()}`, expected_provider_revision: revision}); setNotice("");}}>{zh ? "按当前版本重新准备" : "Rebase on current version"}</button> : null}
      </form>
    </details> : null}
    {notice ? <p role="status">{notice}</p> : null}
  </article>;
}

function RequirementRow({requirement, fresh, zh}: {requirement: Requirement; fresh: boolean; zh: boolean}) {
  const [open, setOpen] = useState(false);
  const review = requirement.agent_review;
  return <details className="goal-outcome-requirement" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary aria-expanded={open}><ChevronDown aria-hidden size={15}/><span>{requirement.description}</span><small className={`is-${fresh ? displayState(requirement) : "recorded"}`}>{stateLabel(requirement, fresh, zh)}</small></summary>
    {review && (review.note.trim() || review.evidence_refs.length > 0) ? <div className="goal-outcome-evidence">
      <h6>{zh ? "上次 Agent 自查" : "Previous Agent review"} · {({met: zh ? "满足" : "Met", unmet: zh ? "有缺口" : "Unmet", uncertain: zh ? "不确定" : "Uncertain"})[review.conclusion]}</h6>
      {review.note.trim() ? <p>{review.note}</p> : null}
      {review.evidence_refs.length ? <ul>{review.evidence_refs.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul> : null}
    </div> : null}
  </details>;
}
