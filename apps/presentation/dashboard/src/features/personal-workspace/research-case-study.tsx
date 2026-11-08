import {useId} from 'react';
import type {TrajectoryCaseStudy} from '../../../../../../loopx/control_plane/capabilities/trajectory_case_study.js';
import type {AssessmentCitation} from '../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js';
import {MarkdownText} from './markdown';
import './research-case-study.css';

type Series = NonNullable<TrajectoryCaseStudy['sections'][number]['series']>;

/** Categorical observations, not an interpolated time series or a ranking. Gaps remain gaps. */
function CaseSeries({series, zh}: {series: Series; zh: boolean}) {
  const values = series.points.flatMap(p => p.value === null ? [] : [p.value]);
  const scale = Math.max(1, ...values.map(Math.abs));
  const low = Math.min(0, ...values.map(v => v / scale)), high = Math.max(0, ...values.map(v => v / scale)), span = high - low || 1;
  const width = Math.max(700, series.points.length * 100);
  const x = (i: number) => 54 + i * (width - 110) / Math.max(1, series.points.length - 1);
  const y = (v: number) => 186 - (v / scale - low) * 132 / span;
  const format = (v: number) => v.toLocaleString(zh ? 'zh-CN' : 'en', {maximumFractionDigits: 6});
  return <figure className="case-series">
    <figcaption>{series.label} <span>· {series.unit}</span></figcaption>
    <div className="case-chart-scroll" tabIndex={0} role="region" aria-label={series.label}>
      <svg viewBox={`0 0 ${width} 238`} style={{minWidth: width}} role="img" aria-label={series.points.map(p => `${p.label}: ${p.value === null ? (zh ? '未知' : 'unknown') : format(p.value)}`).join('; ')}>
        <line x1="30" x2={width - 25} y1={y(0)} y2={y(0)} className="case-chart-axis"/>
        {series.points.map((p, i) => <g key={i}>
          {p.value !== null ? <><line x1={x(i)} x2={x(i)} y1={y(0)} y2={y(p.value)} className="case-chart-stem"/>
            <circle cx={x(i)} cy={y(p.value)} r="4"/><text x={x(i)} y={y(p.value) - 14} textAnchor="middle">{format(p.value)}</text></>
            : <text x={x(i)} y="172" textAnchor="middle">—</text>}
          <text x={x(i)} y="218" textAnchor="middle" className="case-chart-label">{Array.from(p.label).length > 12 ? `${Array.from(p.label).slice(0, 11).join('')}…` : p.label}</text>
        </g>)}
      </svg>
    </div>
    <details><summary>{zh ? '查看数值' : 'Read values'}</summary><dl>{series.points.map((p, i) => <div key={i}><dt>{p.label}</dt><dd>{p.value === null ? (zh ? '未记录' : 'Not recorded') : `${format(p.value)} ${series.unit}`}</dd></div>)}</dl></details>
  </figure>;
}

/** A document's author controls the composition; the host owns navigation and evidence. */
export function ResearchCaseStudy({study, zh, historical, scope, onCitation}: {
  study: TrajectoryCaseStudy; zh: boolean; historical: boolean;
  scope: {case_id: string; run_id: string; revision: string; window: string};
  onCitation: (c: AssessmentCitation, origin?: HTMLButtonElement) => void;
}) {
  const id = useId().replace(/:/g, '');
  const sources = (citations: AssessmentCitation[]) => <details className="case-sources"><summary>{zh ? '依据与原文' : 'Evidence and originals'}</summary>
    {citations.map((c, i) => <button key={i} type="button" onClick={e => onCitation(c, e.currentTarget)}>
      {c.source_id} · {c.first_line}–{c.last_line} ↗</button>)}
  </details>;
  return <article className="research-case-study" lang={study.language === 'zh' ? 'zh-CN' : 'en'}>
    <header className="case-opening">
      <p className="case-eyebrow">{scope.case_id} <span>· {zh ? 'Case 深度解读' : 'Case study'}</span></p>
      <h2>{study.title}</h2>
      <div className="case-lead"><MarkdownText text={study.summary}/></div>
      {historical ? <p className="case-read-warning" role="status">{zh ? '来源已变化或暂不可读。下文保留注册时的解读与证据快照，不能视作当前结果。' : 'The source changed or is unavailable. This reading and its evidence snapshot describe the registered version, not current results.'}</p> : null}
      {study.language !== (zh ? 'zh' : 'en') ? <p className="case-original-language">{zh ? '显示已保存的英文原文。' : 'Showing the saved Chinese original.'}</p> : null}
      <details className="case-provenance"><summary>{zh ? '范围与来源' : 'Scope and provenance'}</summary>
        <p>{zh ? '已保存的作者分析；来源绑定不代表结论已独立验证。' : 'Saved author analysis; source binding does not independently verify the conclusions.'}</p>
        <dl><dt>{zh ? '观察窗口' : 'Window'}</dt><dd>{scope.window}</dd><dt>{zh ? '运行' : 'Run'}</dt><dd>{scope.run_id}</dd><dt>{zh ? '版本' : 'Revision'}</dt><dd>{scope.revision}</dd></dl>
        {sources(study.citations)}
      </details>
    </header>
    <div className="case-reading-layout">
      <nav className="case-outline" aria-label={zh ? '解读目录' : 'Study contents'}>{study.sections.map((s, n) =>
        <button type="button" key={s.id} onClick={() => {const target = document.getElementById(`${id}-${s.id}`); target?.focus({preventScroll: true}); target?.scrollIntoView({block: 'start'});}}>
          <span>{String(n + 1).padStart(2, '0')}</span>{s.title}</button>)}</nav>
      <div className="case-sections">{study.sections.map((s, n) => <section className="case-section" key={s.id} id={`${id}-${s.id}`} tabIndex={-1}>
        <div className="case-section-number">{String(n + 1).padStart(2, '0')}</div><h3>{s.title}</h3>
        <MarkdownText text={s.text}/>
        {s.series ? <CaseSeries series={s.series} zh={zh}/> : null}
        {s.table ? <div className="case-table-scroll" tabIndex={0} role="region" aria-label={s.title}><table><thead><tr>{s.table.columns.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr></thead>
          <tbody>{s.table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => j === 0 ? <th key={j} scope="row">{cell}</th> : <td key={j}><MarkdownText text={cell}/></td>)}</tr>)}</tbody></table></div> : null}
        {s.timeline ? <ol className="case-timeline">{s.timeline.map((event, i) => <li key={i}><span className="case-event-label">{event.label}</span><div><h4>{event.title}</h4><MarkdownText text={event.text}/></div></li>)}</ol> : null}
        {sources(s.citations)}
      </section>)}
        {study.limitations.length ? <section className="case-section case-limitations"><h3>{zh ? '这份解读还缺什么' : 'What this reading cannot resolve'}</h3><ul>{study.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></section> : null}
      </div>
    </div>
  </article>;
}
