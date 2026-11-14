import {useEffect, useRef, useState} from 'react';
import type {TrajectoryCaseStudy} from '../../../../../../loopx/control_plane/capabilities/trajectory_case_study.js';
import type {AssessmentCitation} from '../../../../../../loopx/control_plane/capabilities/trajectory_assessment_types.js';
import {MarkdownText} from './markdown';
import './research-case-study.css';

type Series = NonNullable<TrajectoryCaseStudy['sections'][number]['series']>;

/** Plot the saved observations in order on a common scale that includes zero. */
function CaseSeries({series, zh}: {series: Series; zh: boolean}) {
  const values = series.points.flatMap(point => point.value === null ? [] : [point.value]);
  const magnitude = Math.max(0, ...values.map(Math.abs)) || 1;
  const scaled = values.map(value => value / magnitude);
  const low = Math.min(0, ...scaled);
  const span = Math.max(0, ...scaled) - low || 1;
  const position = (value: number) => (value / magnitude - low) * 100 / span;
  const zero = position(0);
  const locale = zh ? 'zh-CN' : 'en';
  const decimal = new Intl.NumberFormat(locale, {maximumSignificantDigits: 17});
  const scientific = new Intl.NumberFormat(locale, {notation: 'scientific', maximumSignificantDigits: 17});
  const format = (value: number) => {
    const absolute = Math.abs(value);
    return (absolute !== 0 && (absolute < 1e-6 || absolute >= 1e12) ? scientific : decimal).format(value);
  };

  return <figure className="case-series">
    <figcaption>{series.label} <span>· {series.unit}</span></figcaption>
    <ol className="case-series-points" role="list">
      {series.points.map((point, i) => {
        const end = point.value === null ? null : position(point.value);
        return <li key={i}>
          <span className="case-chart-label">{point.label}</span>
          <span className="case-chart-value">{point.value === null ? (zh ? '未记录' : 'Not recorded') : format(point.value)}</span>
          <span className="case-chart-track" aria-hidden="true">
            <span className="case-chart-zero" style={{insetInlineStart: `${zero}%`}}/>
            {end !== null ? <>
              <span className="case-chart-bar" style={{insetInlineStart: `${Math.min(end, zero)}%`, inlineSize: `${Math.abs(end - zero)}%`}}/>
              <span className="case-chart-mark" style={{insetInlineStart: `${end}%`}}/>
            </> : null}
          </span>
        </li>;
      })}
    </ol>
  </figure>;
}

export const caseChapterHashPrefix = '#case-section-';

export type CaseCitationBookmark = {hash: string; chapterHash: string};

function focusChapter(chapter: HTMLElement) {
  chapter.focus({preventScroll: true});
  chapter.scrollIntoView({block: 'start'});
}

/** A document's author controls the composition; the host owns navigation and evidence. */
export function ResearchCaseStudy({study, zh, historical, active, scope, navigation, onCitation, onCitationLocationChange}: {
  study: TrajectoryCaseStudy; zh: boolean; historical: boolean; active: boolean;
  scope: {case_id: string; run_id: string; revision: string; window: string};
  navigation: {url: URL; goalId: string; packId: string; namespace: string | null; follow: (href: URL, replace?: boolean) => void};
  onCitation: (c: AssessmentCitation, origin: HTMLElement, bookmark: CaseCitationBookmark) => void;
  onCitationLocationChange: (hash: string) => void;
}) {
  const article = useRef<HTMLElement>(null);
  const openCitation = useRef(onCitation);
  openCitation.current = onCitation;
  const [missingCitation, setMissingCitation] = useState(false);
  const {url, goalId, packId, namespace, follow} = navigation;
  const prefix = `${caseChapterHashPrefix.slice(1)}${namespace ? namespace + '-' : ''}${packId}-`;
  useEffect(() => {
    if (!active) return;
    let frame = 0;
    const restore = () => {
      cancelAnimationFrame(frame);
      onCitationLocationChange(window.location.hash);
      frame = requestAnimationFrame(() => {
        const {searchParams, hash} = new URL(window.location.href);
        if (searchParams.get('view') !== 'research' || searchParams.get('research_reading') !== 'case'
          || searchParams.get('research_goal') !== goalId || searchParams.get('research_analysis_goal') !== goalId
          || searchParams.get('research_pack') !== packId) return;
        const citationLink = hash.startsWith(caseChapterHashPrefix) && hash.includes('~');
        const scoped = hash.startsWith(`#${prefix}`);
        if (citationLink) {
          // Resolve an ordinal against this saved study. Never turn URL text
          // into a source identifier or range for the evidence API.
          const match = scoped ? /^([a-zA-Z0-9_-]*)~([1-9][0-9]?)$/.exec(hash.slice(prefix.length + 1)) : null;
          const section = match?.[1];
          const citations = section ? study.sections.find(item => item.id === section)?.citations : study.citations;
          const citation = match ? citations?.[Number(match[2]) - 1] : undefined;
          const origin = document.getElementById(hash.slice(1));
          if (!citation || !origin || !article.current?.contains(origin)) {
            setMissingCitation(true);
            return;
          }
          setMissingCitation(false);
          const disclosure = origin.closest('details');
          if (disclosure) disclosure.open = true;
          openCitation.current(citation, origin, {hash, chapterHash: section ? `#${prefix}${section}` : ""});
          return;
        }
        setMissingCitation(false);
        if (!scoped) return;
        const chapter = document.getElementById(hash.slice(1));
        if (chapter && article.current?.contains(chapter) && chapter.getClientRects().length) focusChapter(chapter);
      });
    };
    restore();
    window.addEventListener('popstate', restore);
    window.addEventListener('hashchange', restore);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener('popstate', restore);
      window.removeEventListener('hashchange', restore);
    };
  }, [active, goalId, packId, prefix, study, onCitationLocationChange]);
  const citationLinks = (citations: AssessmentCitation[], section = "") => <ul className="case-citation-links" role="list">
    {citations.map((c, i) => {
      const id = `${prefix}${section}~${i + 1}`;
      const href = new URL(url);
      href.hash = id;
      return <li key={i}><a id={id} href={href.href} onClick={e => {
        if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
        e.preventDefault();
        setMissingCitation(false);
        follow(href, true);
        onCitation(c, e.currentTarget, {hash: href.hash, chapterHash: section ? `#${prefix}${section}` : ""});
      }}>
        {c.source_id} · {zh ? '行' : 'lines'} {c.first_line}–{c.last_line} <span aria-hidden="true">↗</span>
      </a></li>;
    })}
  </ul>;
  return <article ref={article} className="research-case-study" lang={study.language === 'zh' ? 'zh-CN' : 'en'}>
    <header className="case-opening">
      <p className="case-eyebrow">{scope.case_id}</p>
      <h2>{study.title}</h2>
      <div className="case-lead"><MarkdownText text={study.summary}/></div>
      {historical ? <p className="case-read-warning" role="status">{zh ? '来源已变化或暂不可读。下文保留注册时的解读与证据快照，不能视作当前结果。' : 'The source changed or is unavailable. This reading and its evidence snapshot describe the registered version, not current results.'}</p> : null}
      {study.language !== (zh ? 'zh' : 'en') ? <p className="case-original-language">{zh ? '显示已保存的英文原文。' : 'Showing the saved Chinese original.'}</p> : null}
      {missingCitation ? <p className="case-read-warning" role="status">{zh ? '这条链接在所选解读中找不到原文。可从下方的依据入口选择。' : 'This link does not identify an original in the selected study. Choose a source below.'}</p> : null}
      <details className="case-provenance"><summary>{zh ? '结论依据与范围' : 'Conclusion evidence and scope'}</summary>
        {citationLinks(study.citations)}
        <p>{zh ? '已保存的作者分析；来源绑定不代表结论已独立验证。' : 'Saved author analysis; source binding does not independently verify the conclusions.'}</p>
        <dl><dt>{zh ? '观察窗口' : 'Window'}</dt><dd>{scope.window}</dd><dt>{zh ? '运行' : 'Run'}</dt><dd>{scope.run_id}</dd><dt>{zh ? '版本' : 'Revision'}</dt><dd>{scope.revision}</dd></dl>
      </details>
    </header>
    <div className="case-reading-layout">
      <nav className="case-outline" aria-label={zh ? '解读目录' : 'Study contents'}>{study.sections.map((s, n) => {
        const id = prefix + s.id;
        const href = new URL(url);
        href.hash = id;
        return <a key={s.id} href={href.href} onClick={e => {
          if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
          e.preventDefault();
          onCitationLocationChange(href.hash);
          setMissingCitation(false);
          follow(href);
          const chapter = document.getElementById(id);
          if (chapter && article.current?.contains(chapter)) focusChapter(chapter);
        }}><span>{String(n + 1).padStart(2, '0')}</span>{s.title}</a>;
      })}</nav>
      <div className="case-sections">{study.sections.map((s, n) => <section className="case-section" key={s.id} id={prefix + s.id} tabIndex={-1}>
        <div className="case-section-number">{String(n + 1).padStart(2, '0')}</div><h3>{s.title}</h3>
        <MarkdownText text={s.text}/>
        {s.series ? <CaseSeries series={s.series} zh={zh}/> : null}
        {s.table ? <div className="case-table-scroll" tabIndex={0} role="region" aria-label={s.title}><table><thead><tr>{s.table.columns.map((c, i) => <th key={i} scope="col">{c}</th>)}</tr></thead>
          <tbody>{s.table.rows.map((row, i) => <tr key={i}>{row.map((cell, j) => j === 0 ? <th key={j} scope="row">{cell}</th> : <td key={j}><MarkdownText text={cell}/></td>)}</tr>)}</tbody></table></div> : null}
        {s.timeline ? <ol className="case-timeline">{s.timeline.map((event, i) => <li key={i}><span className="case-event-label">{event.label}</span><div><h4>{event.title}</h4><MarkdownText text={event.text}/></div></li>)}</ol> : null}
        <details className="case-sources"><summary>{zh ? '依据与原文' : 'Evidence and originals'}</summary>
          {citationLinks(s.citations, s.id)}
        </details>
      </section>)}
        {study.limitations.length ? <section className="case-section case-limitations"><h3>{zh ? '这份解读还缺什么' : 'What this reading cannot resolve'}</h3><ul>{study.limitations.map((text, i) => <li key={i}>{text}</li>)}</ul></section> : null}
      </div>
    </div>
  </article>;
}
