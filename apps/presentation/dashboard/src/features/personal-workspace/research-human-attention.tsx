import type {AssessmentCitation, TrajectoryEpisode} from '../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js';
import {HUMAN_ATTENTION_KINDS, type HumanAttentionKind, type TrajectoryHumanAttention} from '../../../../../../loopx/control_plane/capabilities/trajectory_human_attention.js';
import {ContextHelp} from './context-help';
import {useMemo, useState} from 'react';
import './research-human-attention.css';

export type ResearchHumanAttentionCapture = {projection: TrajectoryHumanAttention; applicability: 'current' | 'historical'};
const names: Record<HumanAttentionKind, [string, string]> = {
  clarification: ['澄清', 'Clarifications'], correction: ['纠偏', 'Corrections'], review: ['审查', 'Reviews'],
  approval: ['授权判断', 'Authorization decisions'], status: ['状态回应', 'Status responses'], other: ['其他介入', 'Other inputs'],
};
const suggestions: Record<HumanAttentionKind, [string, string]> = {
  clarification: ['检查交接是否带上已有背景和约束，减少重复补充。', 'Check that handoffs include available context and constraints.'],
  correction: ['核对研究上下文是否保留反例、版本与适用条件，并检查下一步是否采用纠正。', 'Check that context preserves counterevidence, versions and applicability, then verify adoption of the correction.'],
  review: ['将待判断的问题、适用范围和关键证据放在一起，减少来回核验。', 'Present the decision, applicability and key evidence together to reduce review round trips.'],
  approval: ['核对是否重复请求已有授权；保留必要的权限和风险判断。', 'Check for requests duplicating existing authorization; retain necessary permission and risk decisions.'],
  status: ['检查阶段结果是否回到原对话，减少人工追问进展。', 'Check that milestone results return to the original conversation.'],
  other: ['从原始记录核对介入原因，再确定可减少的协调工作。', 'Inspect the original cause before choosing which coordination work to reduce.'],
};

/** Formats measured observations only; opening this reader never calls a model. */
export function ResearchHumanAttention({capture, episodes, zh, onCitation}: {
  capture?: ResearchHumanAttentionCapture; episodes?: TrajectoryEpisode[]; zh: boolean;
  onCitation: (citation: AssessmentCitation, origin?: HTMLButtonElement) => void;
}) {
  const a = capture?.projection;
  const [page, setPage] = useState(0);
  const byId = useMemo(() => new Map(a?.events.map(e => [e.event_id, e])), [a]);
  const pageSize = 8, pages = Math.max(1, Math.ceil((a?.events.length ?? 0) / pageSize));
  const current = Math.min(page, pages - 1);
  const number = (n: number) => n.toLocaleString(zh ? 'zh-CN' : 'en');
  const duration = (ms: number | null) => ms === null ? (zh ? '未采集' : 'Not recorded')
    : ms > 0 && ms < 6000 ? (zh ? '<0.1 分钟' : '<0.1 min')
    : `${new Intl.NumberFormat(zh ? 'zh-CN' : 'en', {maximumFractionDigits: 1}).format(ms / 60000)} ${zh ? '分钟' : 'min'}`;
  const necessity = (n: string) => n === 'required' ? (zh ? '必要' : 'Required')
    : n === 'avoidable' ? (zh ? '标记可避免' : 'Marked avoidable') : (zh ? '必要性未知' : 'Necessity unknown');
  return <section className="research-human-attention" aria-label={zh ? '人工注意力投入' : 'Human attention'}>
    <div className="research-usage-heading"><h4>{zh ? '人工注意力投入' : 'Human attention'}</h4>
      <ContextHelp label={zh ? '人工投入的测量范围' : 'Human attention measurement scope'}>
        <p>{zh ? '只统计绑定到当前研究范围的人工介入记录。分类与必要性由记录方声明，来源可核对；用户角色消息、自动重规划和待审查状态不能直接证明人工投入。' : 'Counts human observations bound to this research scope. Kind and necessity are recorder declarations with inspectable sources. User-role messages, automatic replans and required-review states do not prove human input.'}</p>
        <p>{zh ? '主动投入与等待分别按已记录时间区间去重，不相加。覆盖不全的时间只是已观测部分；未采集不是零。优化点是待核查建议，必须与后续成果质量一起验证。' : 'Active time and response waiting are separate unions of recorded intervals. Partial time coverage is observed-only; unrecorded is not zero. Optimization points are proposals to verify against subsequent outcome quality.'}</p>
      </ContextHelp>
    </div>
    {!a ? <p>{zh ? '未采集' : 'Not recorded'}</p> : <>
      <p className="research-attention-total"><strong>{number(a.total)}</strong> {zh ? '次已记录介入' : 'recorded inputs'}
        <span>{capture?.applicability === 'historical' ? (zh ? '历史来源' : 'Historical source')
          : a.complete ? (zh ? '记录方声明完整' : 'Recorder declares complete coverage') : (zh ? '部分覆盖' : 'Partial coverage')}</span></p>
      <dl className="research-attention-counts">{HUMAN_ATTENTION_KINDS.filter(k => a.counts[k] > 0).map(k =>
        <div key={k}><dt>{names[k][zh ? 0 : 1]}</dt><dd>{number(a.counts[k])}</dd></div>)}</dl>
      <dl className="research-attention-times">
        <div><dt>{zh ? '主动投入' : 'Active attention'}</dt><dd>{duration(a.active_ms)}<span>{a.active_covered} / {a.events.length} {zh ? '条有时间记录' : 'records timed'}</span></dd></div>
        <div><dt>{zh ? '等待人工响应' : 'Waiting for human response'}</dt><dd>{duration(a.wait_ms)}<span>{a.wait_covered} {zh ? '段已结束等待' : 'closed waits'}{a.pending_requests ? ` · ${a.pending_requests} ${zh ? '段待响应' : 'pending'}` : ''}</span></dd></div>
      </dl>
      <details className="research-attention-detail"><summary>{zh ? '优化点与介入记录' : 'Optimization points and input records'}</summary>
        <p>{zh ? `必要 ${a.required} · 标记可避免 ${a.avoidable} · 必要性未知 ${a.unknown}` : `Required ${a.required} · Marked avoidable ${a.avoidable} · Necessity unknown ${a.unknown}`}</p>
        {a.optimization_points.length ? <ul className="research-attention-opportunities">{a.optimization_points.map(point => <li key={point.kind}>
          <strong>{names[point.kind][zh ? 0 : 1]} · {point.event_ids.length}</strong><p>{suggestions[point.kind][zh ? 0 : 1]}</p>
          {point.event_ids.slice(0, 3).map(id => {const event = byId.get(id)!; return <button type="button" key={id}
            onClick={e => onCitation(event.citation, e.currentTarget)}>{zh ? '核对来源' : 'Check source'} · {id}</button>;})}
          {point.event_ids.length > 3 ? <span>{zh ? '其余来源见下方介入记录' : 'More sources in the input records below'}</span> : null}
        </li>)}</ul> : <p>{zh ? '尚无标记为可避免的介入。' : 'No inputs are marked avoidable.'}</p>}
        <ol className="research-attention-events" start={current * pageSize + 1}>{a.events.slice(current * pageSize, (current + 1) * pageSize).map(event => <li key={event.event_id}><details>
          <summary>{event.request?.end === null ? (zh ? '待响应请求 · ' : 'Pending request · ') : ''}{names[event.kind][zh ? 0 : 1]} · {necessity(event.necessity)}<time dateTime={event.occurred_at}>{event.occurred_at}</time></summary>
          <p>{episodes?.find(e => e.id === event.episode_id)?.question ?? (event.episode_id ?? (zh ? '未关联研究片段' : 'No episode association'))}</p>
          <dl><dt>{zh ? '主动投入' : 'Active attention'}</dt><dd>{duration(event.active ? Date.parse(event.active.end) - Date.parse(event.active.start) : null)}</dd>
            <dt>{zh ? '等待响应' : 'Response waiting'}</dt><dd>{event.request?.end === null ? (zh ? '待响应' : 'Pending') : duration(event.request ? Date.parse(event.request.end!) - Date.parse(event.request.start) : null)}</dd></dl>
          <button type="button" onClick={e => onCitation(event.citation, e.currentTarget)}>{zh ? '读取介入依据' : 'Read input evidence'} · {event.event_id}</button>
        </details></li>)}</ol>
        {pages > 1 ? <nav className="research-attention-pages" aria-label={zh ? '人工介入记录分页' : 'Human input record pages'}>
          <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>{zh ? '上一页' : 'Previous'}</button>
          <span>{current + 1} / {pages}</span>
          <button type="button" disabled={current + 1 === pages} onClick={() => setPage(current + 1)}>{zh ? '下一页' : 'Next'}</button>
        </nav> : null}
        <p><time dateTime={a.start}>{a.start}</time> — <time dateTime={a.end}>{a.end}</time></p>
      </details>
    </>}
  </section>;
}
