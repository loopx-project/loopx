import type {AssessmentCitation, TrajectoryEpisode} from '../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js';
import {HUMAN_ATTENTION_KINDS, type HumanAttentionKind, type TrajectoryHumanAttention} from '../../../../../../loopx/control_plane/capabilities/trajectory_human_attention.js';
import {ContextHelp} from './context-help';
import {useMemo, useState} from 'react';
import {Bell, Ellipsis, Hourglass, MessageCircle, ScanSearch, ShieldCheck, Undo2, UserCheck, type LucideIcon} from 'lucide-react';
import type {ReactNode} from 'react';
import './research-human-attention.css';

export type ResearchHumanAttentionCapture = {projection: TrajectoryHumanAttention; applicability: 'current' | 'historical'};
const names: Record<HumanAttentionKind, [string, string]> = {
  clarification: ['澄清', 'Clarifications'], correction: ['纠偏', 'Corrections'], review: ['审查', 'Reviews'],
  approval: ['授权判断', 'Authorization decisions'], status: ['状态回应', 'Status responses'], other: ['其他介入', 'Other inputs'],
};
const icons: Record<HumanAttentionKind, LucideIcon> = {
  clarification: MessageCircle, correction: Undo2, review: ScanSearch,
  approval: ShieldCheck, status: Bell, other: Ellipsis,
};
function attentionDuration(ms: number | null, zh: boolean): string {
  if (ms === null) return zh ? '未采集' : 'Not recorded';
  if (ms > 0 && ms < 6000) return zh ? '<0.1 分钟' : '<0.1 min';
  return `${new Intl.NumberFormat(zh ? 'zh-CN' : 'en', {maximumFractionDigits: 1}).format(ms / 60000)} ${zh ? '分钟' : 'min'}`;
}
/** Independent observation tracks share the capture window, never a summed cost. */
function AttentionTime({label, Icon, value, windowMs, coverage, zh, className}: {
  label: string; Icon: LucideIcon; value: number | null; windowMs: number;
  coverage: ReactNode; zh: boolean; className: string;
}) {
  const share = value === null || windowMs <= 0 ? null : value / windowMs;
  return <div className={`research-attention-time ${className}`}>
    <dt><Icon size={17} aria-hidden="true"/>{label}</dt>
    <dd><strong>{attentionDuration(value, zh)}</strong>
      <div className="research-attention-time-track" data-observed={share !== null}
        role="img" aria-label={`${label} · ${attentionDuration(value, zh)} · ${zh ? '记录窗口' : 'Capture window'} ${attentionDuration(windowMs, zh)}`}>
        {share === null ? null : <span data-share={share} style={{width: `${share * 100}%`}}/>}
      </div><span className="research-attention-time-coverage">{coverage}</span>
    </dd>
  </div>;
}
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
  const duration = (ms: number | null) => attentionDuration(ms, zh);
  const percent = (share: number) => share > 0 && share < 0.001 ? '<0.1%'
    : new Intl.NumberFormat(zh ? 'zh-CN' : 'en', {style: 'percent', maximumFractionDigits: 1}).format(share);
  const windowMs = a ? Date.parse(a.end) - Date.parse(a.start) : 0;
  let offset = 0;
  const categories = a && a.total > 0 ? HUMAN_ATTENTION_KINDS.filter(kind => a.counts[kind] > 0)
    .sort((left, right) => a.counts[right] - a.counts[left]).map(kind => {
      const share = a.counts[kind] / a.total, start = offset;
      offset += share * 100;
      return {kind, count: a.counts[kind], share, start};
    }) : [];
  const necessity = (n: string) => n === 'required' ? (zh ? '必要' : 'Required')
    : n === 'avoidable' ? (zh ? '标记可避免' : 'Marked avoidable') : (zh ? '必要性未知' : 'Necessity unknown');
  return <section className="research-human-attention" aria-label={zh ? '人工注意力投入' : 'Human attention'}>
    <div className="research-usage-heading"><h4>{zh ? '人工注意力投入' : 'Human attention'}</h4>
      <ContextHelp label={zh ? '人工投入的测量范围' : 'Human attention measurement scope'}>
        <p>{zh ? '只统计绑定到当前研究范围的人工介入记录。分类与必要性由记录方声明，来源可核对；用户角色消息、自动重规划和待审查状态不能直接证明人工投入。' : 'Counts human observations bound to this research scope. Kind and necessity are recorder declarations with inspectable sources. User-role messages, automatic replans and required-review states do not prove human input.'}</p>
        <p>{zh ? '类型占比以已记录的完成人工介入为分母，百分比经四舍五入。两条时间轨道分别对照记录窗口；未填色区域没有该类时间记录，不能理解为空闲。主动投入与等待不相加，未采集不是零。优化点须与后续成果质量一起验证。' : 'Type shares use recorded completed inputs; percentages are rounded. Time tracks independently compare their recorded interval unions with the capture window. Unfilled regions have no time attributed to that category, not proven idle time. Active attention and waiting are not added; unrecorded is not zero. Verify optimization points against subsequent outcome quality.'}</p>
      </ContextHelp>
      {a ? <span className="research-attention-scope">{capture?.applicability === 'historical' ? (zh ? '历史来源' : 'Historical source')
        : a.complete ? (zh ? '记录方声明完整' : 'Recorder declares complete coverage') : (zh ? '部分覆盖' : 'Partial coverage')}</span> : null}
    </div>
    {!a ? <p>{zh ? '未采集' : 'Not recorded'}</p> : <>
      <div className="research-attention-overview">
        <div className="research-attention-breakdown">
          <div className="research-attention-donut" role="img" aria-label={zh ? `介入类型占比 · ${a.total} 次已记录介入` : `Input type shares · ${a.total} recorded inputs`}>
            <svg viewBox="0 0 160 160" aria-hidden="true">
              <circle cx="80" cy="80" r="64" className="research-attention-ring-track"/>
              {categories.map(row => <circle key={row.kind} cx="80" cy="80" r="64" pathLength="100"
                transform="rotate(-90 80 80)" data-kind={row.kind} data-count={row.count} data-share={row.share}
                strokeDasharray={`${row.share * 100} ${100 - row.share * 100}`} strokeDashoffset={-row.start}/>) }
            </svg>
            <p className="research-attention-total"><strong>{number(a.total)}</strong><span>{zh ? '次已记录介入' : 'recorded inputs'}</span></p>
          </div>
          <dl className="research-attention-counts">{categories.map(row => {
            const Icon = icons[row.kind];
            return <div key={row.kind} data-kind={row.kind}><dt><Icon size={18} aria-hidden="true"/>{names[row.kind][zh ? 0 : 1]}</dt>
              <dd><strong>{number(row.count)}</strong><span>{percent(row.share)}</span></dd></div>;
          })}</dl>
        </div>
        <div className="research-attention-time-overview">
          <dl className="research-attention-times">
            <AttentionTime label={zh ? '主动投入' : 'Active attention'} Icon={UserCheck} value={a.active_ms} windowMs={windowMs}
              coverage={<>{a.active_covered} / {a.events.length} {zh ? '条有时间记录' : 'records timed'}</>} zh={zh} className="attention-active"/>
            <AttentionTime label={zh ? '等待人工响应' : 'Waiting for human response'} Icon={Hourglass} value={a.wait_ms} windowMs={windowMs}
              coverage={<>{a.wait_covered} {zh ? '段已结束等待' : 'closed waits'}{a.pending_requests ? ` · ${a.pending_requests} ${zh ? '段待响应' : 'pending'}` : ''}</>} zh={zh} className="attention-waiting"/>
          </dl>
          {windowMs > 0 ? <p className="research-attention-window">{zh ? '记录窗口' : 'Capture window'} · {duration(windowMs)}</p> : null}
        </div>
      </div>
      {a.total > 0 ? <div className="research-attention-opportunity">
        <div className="research-attention-necessity-legend"><span data-necessity="avoidable">{zh ? '标记可避免' : 'Marked avoidable'} <strong>{a.avoidable}</strong> · {percent(a.avoidable / a.total)}</span>
          <span>{zh ? '必要' : 'Required'} {a.required}</span><span>{zh ? '必要性未知' : 'Necessity unknown'} {a.unknown}</span></div>
        <div className="research-attention-necessity-track" role="img" aria-label={zh ? `标记可避免 ${a.avoidable} · 必要 ${a.required} · 必要性未知 ${a.unknown}` : `Marked avoidable ${a.avoidable} · Required ${a.required} · Necessity unknown ${a.unknown}`}>
          <span data-necessity="avoidable" data-count={a.avoidable} style={{width: `${a.avoidable / a.total * 100}%`}}/>
          <span data-necessity="required" data-count={a.required} style={{width: `${a.required / a.total * 100}%`}}/>
          <span data-necessity="unknown" data-count={a.unknown} style={{width: `${a.unknown / a.total * 100}%`}}/>
        </div>
      </div> : null}
      <details className="research-attention-detail"><summary>{zh ? '优化点与介入记录' : 'Optimization points and input records'}</summary>
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
