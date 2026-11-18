// The machines page (a first-class page at #/machines, persona's web/src/views/machines): one
// card per machine with today's tokens, and a detail view with the token charts behind each card.
// The address owns the level (#/machines is the list, #/machines/<id|local> a detail); the
// arithmetic lives in machine-usage.ts; this file only fetches, lays out and draws.
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import { Api, ApiError, type AgentInfo, type HostInfo, type MachineUsageReport, type UsageTokens } from '../api';
import { hostStatus } from '../hosts';
import { bucketLabel, clockLabel, compactLabel, dayLabel, niceTicks, numberLabel, stepLabel, timeTicks } from '../observability';
import {
  USAGE_COLOR, USAGE_FIELD, USAGE_KINDS, currentPerMinute, hitRate, hitRuns, offlineRuns, perMinute, percentText,
  stackLayers, todayTokens, totalTokens, type UsageKind,
} from '../machine-usage';
import { t } from '../i18n';
import { Popover } from './Popover';
import { IconBack, IconCheck, IconChevron, IconRefresh } from './icons';

const KIND_LABEL: Record<UsageKind, string> = {
  in: t('machine.kind.input'),
  out: t('machine.kind.output'),
  cacheWrite: t('machine.kind.cacheWrite'),
  cacheRead: t('machine.kind.cacheRead'),
};

/** A machine the page shows: the hub's own, or one of the configured remotes. */
interface Machine {
  /** null is the hub's own machine. */
  id: string | null;
  label: string;
  host: HostInfo | null;
  api: Api;
}

function Select({ label, short, value, options, onPick }: { label: string; short: string; value: string; options: Array<{ id: string; label: string }>; onPick: (id: string) => void }) {
  const picked = options.find(o => o.id === value && o.id !== '')?.label;
  // The wide screen spells the choice out ("all models"); a phone carries the short form,
  // and a picked value wherever it lands.
  const full = options.find(o => o.id === value)?.label ?? label;
  return <Popover label={label} triggerClassName="text-btn obs-select" triggerLabel={full} trigger={<>
    <span className="obs-select-text">{full}</span>
    <span className="obs-select-brief">{picked ?? short}</span>
    <IconChevron size={12} open />
  </>}>
    {close => <><div className="pop-title">{label}</div>{options.map(option => <button key={option.id} type="button" role="menuitemradio" aria-checked={option.id === value} className="pop-item compact" onClick={() => { onPick(option.id); close(); }}>
      <span className="pop-item-text"><span className="pop-item-label">{option.label}</span></span>
      {option.id === value && <span className="pop-item-check"><IconCheck size={16} /></span>}
    </button>)}</>}
  </Popover>;
}

/** The plot width, from the box the chart sits in. */
function useWidth(box: React.RefObject<HTMLDivElement | null>, dep: unknown): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [box, dep]);
  return width;
}

/** One machine's card numbers: today's tokens and the last hour's per-minute line. */
type Summary = { day: UsageTokens | null; hour: MachineUsageReport | null; state: 'loading' | 'ok' | 'old' | 'failed' };

/** The detail page's empty machine table: a stable identity, so the poll's effect rests. */
const NO_MACHINES: Machine[] = [];

function useSummaries(machines: Machine[]): Map<string, Summary> {
  const [summaries, setSummaries] = useState<Map<string, Summary>>(new Map());
  useEffect(() => {
    // A detail page asks for nothing; an empty read still lands as a new Map, which re-arms.
    if (machines.length === 0) return;
    let alive = true;
    const read = async () => {
      const next = new Map<string, Summary>();
      await Promise.all(machines.map(async (machine) => {
        // A remote that is down cannot answer; the card says so instead of a fetch that cannot land.
        if (machine.host && machine.host.status === 'offline') {
          next.set(machine.id ?? '', { day: null, hour: null, state: 'failed' });
          return;
        }
        try {
          const [day, hour] = await Promise.all([machine.api.machineUsage(24), machine.api.machineUsage(1)]);
          if (alive) next.set(machine.id ?? '', { day: todayTokens(day), hour, state: 'ok' });
        } catch (error) {
          next.set(machine.id ?? '', { day: null, hour: null, state: error instanceof ApiError && error.status === 404 ? 'old' : 'failed' });
        }
      }));
      if (alive) setSummaries(next);
    };
    void read();
    const timer = setInterval(() => { if (!document.hidden) void read(); }, 60_000);
    return () => { alive = false; clearInterval(timer); };
  }, [machines]);
  return summaries;
}

/** The last hour as a small line, a break over the offline steps. */
function Spark({ report }: { report: MachineUsageReport | null }) {
  const values = report ? report.points.map(point => ({ v: perMinute(totalTokens(point), report.step), gap: point.gap })) : [];
  const peak = Math.max(1, ...values.map(v => v.v));
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * 118 + 1;
  const y = (v: number) => 33 - (v / peak) * 30;
  const runs: string[] = [];
  let run = '';
  values.forEach((value, i) => {
    if (value.gap) { if (run) runs.push(run); run = ''; return; }
    run += `${run ? 'L' : 'M'}${x(i).toFixed(1)},${y(value.v).toFixed(1)}`;
  });
  if (run) runs.push(run);
  return <svg className="mu-spark" viewBox="0 0 120 36" preserveAspectRatio="none" aria-hidden="true">
    {runs.map((path, i) => <path key={i} d={path} />)}
  </svg>;
}

/** Today over every machine, with each machine's share as a neutral bar (persona's TodayTokens). */
function TodayCard({ machines, summaries }: { machines: Machine[]; summaries: Map<string, Summary> }) {
  // One machine is its own total (the card says it); a lone bar would only repeat the number
  // as a block of ink. The card appears the moment a second machine could answer.
  if (machines.length < 2) return null;
  const shares = machines
    .map(machine => ({ machine, day: summaries.get(machine.id ?? '')?.day }))
    .filter((s): s is { machine: Machine; day: UsageTokens } => s.day != null)
    .map(s => ({ ...s, tokens: totalTokens(s.day) }))
    .sort((a, b) => b.tokens - a.tokens);
  const total = shares.reduce((sum, s) => sum + s.tokens, 0);
  const missing = machines.filter(m => summaries.get(m.id ?? '')?.state !== 'ok').length;
  // Persona's shading: the first machine's ink at full strength, each later one a step lighter.
  const shade = (i: number) => Math.max(0.85 - i * 0.2, 0.2);
  // Nothing answered yet: the number is unknown, not zero, and the card keeps its shape.
  const unknown = !shares.length && missing > 0;
  return <section className="mu-today">
    <span>{t('machine.today')}</span>
    <strong title={unknown ? undefined : numberLabel(total)} className={unknown ? 'unknown' : ''}>{unknown ? '—' : total ? compactLabel(total) : '0'}</strong>
    <div className="mu-share-bar" role={unknown ? undefined : 'img'} aria-hidden={unknown || undefined} aria-label={unknown ? undefined : shares.map(s => `${s.machine.label} ${compactLabel(s.tokens)}`).join(', ')}>
      {total > 0 && shares.map((s, i) => <i key={s.machine.id ?? ''} style={{ width: `${(s.tokens / total) * 100}%`, opacity: shade(i) }} title={`${s.machine.label} · ${compactLabel(s.tokens)}`} />)}
    </div>
    {total > 0 && <div className="mu-share-legend">{shares.map((s, i) => <span key={s.machine.id ?? ''}><i style={{ opacity: shade(i) }} />{s.machine.label} · {compactLabel(s.tokens)}</span>)}</div>}
    {missing > 0 && <small>{t('machine.todayMissing', missing)}</small>}
  </section>;
}

function MachineList({ machines, summaries, onOpen }: { machines: Machine[]; summaries: Map<string, Summary>; onOpen: (id: string | null) => void }) {
  return <section aria-label={t('machine.cards')}>
    <TodayCard machines={machines} summaries={summaries} />
    <div className="mu-cards">
      {machines.map(machine => {
        const summary = summaries.get(machine.id ?? '');
        const state = summary?.state ?? 'loading';
        return <button key={machine.id ?? 'local'} type="button" className="mu-card" onClick={() => onOpen(machine.id)}>
          <span className="mu-card-head">
            <strong>{machine.label}</strong>
            <small>{machine.host ? hostStatus(machine.host.status) : t('hosts.localHint')}</small>
          </span>
          <span className="mu-card-stat">
            <span className="mu-card-today">{t('machine.today')} <strong title={state === 'ok' && summary?.day ? numberLabel(totalTokens(summary.day)) : undefined}>{state === 'ok' && summary?.day ? compactLabel(totalTokens(summary.day)) : '—'}</strong></span>
            <span className="mu-card-now">{state === 'ok' && summary?.hour ? t('machine.nowPerMinute', compactLabel(currentPerMinute(summary.hour))) : state === 'loading' ? '—' : ''}</span>
          </span>
          {/* The line keeps its place before and after the numbers arrive, so the grid does not jump. */}
          <Spark report={state === 'ok' ? summary?.hour ?? null : null} />
          {state === 'old' && <span className="mu-card-note">{t('machine.oldDaemon')}</span>}
          {state === 'failed' && <span className="mu-card-note">{machine.host?.status === 'offline' ? hostStatus('offline') : t('machine.loadFailed')}</span>}
        </button>;
      })}
    </div>
  </section>;
}

const PAD = { top: 10, right: 12, bottom: 26, left: 48 };
const at = (t: number) => new Date(t * 1000);

/** The report's four numbers as one plain row (persona's UsageSection): a label over a figure,
 *  no boxes of their own. Unknown ones hold a dash in the number's place. */
function Figures({ report }: { report: MachineUsageReport | null }) {
  const figures = report
    ? [
      [t('machine.total'), compactLabel(totalTokens(report.total)), numberLabel(totalTokens(report.total))],
      [t('machine.output'), compactLabel(report.total.output), numberLabel(report.total.output)],
      [t('machine.hitRate'), percentText(hitRate(report.total)), undefined],
      [t('machine.replies'), numberLabel(report.total.replies), undefined],
    ] as Array<[string, string, string | undefined]>
    : ([t('machine.total'), t('machine.output'), t('machine.hitRate'), t('machine.replies')] as string[]).map(label => [label, '—', undefined]);
  return <div className="mu-figures">
    {figures.map(([label, value, title]) => <div key={label}><span>{label}</span><strong title={title}>{value}</strong></div>)}
  </div>;
}

/** While the report is on its way the charts keep their final shape, so nothing jumps when it lands. */
const EMPTY_REPORT: MachineUsageReport = {
  hours: 1, step: 60,
  total: { input: 0, cacheWrite: 0, cacheRead: 0, output: 0, replies: 0 },
  points: [], models: [], launchers: [],
};

/** Tokens per minute over the range: the four numbers on top, then stacked by kind (or one total
 *  line), a crosshair readout on hover, touch and arrow keys, a clickable legend with each kind's
 *  total, and grey over the offline steps. A null report draws the same card empty. */
function ThroughputChart({ report, hidden, mode, onToggleMode, onToggleKind, onRefresh }: { report: MachineUsageReport | null; hidden: ReadonlySet<UsageKind>; mode: 'kinds' | 'total'; onToggleMode: (mode: 'kinds' | 'total') => void; onToggleKind: (kind: UsageKind) => void; onRefresh: () => void }) {
  const report1 = report ?? EMPTY_REPORT;
  const n = report1.points.length;
  const stepMinutes = report1.step / 60;
  const kinds = USAGE_KINDS.filter(kind => !hidden.has(kind));
  const layers = stackLayers(report1.points, report1.step, kinds.length ? kinds : USAGE_KINDS);
  const totals = report1.points.map(point => perMinute(totalTokens(point), report1.step));
  const peak = Math.max(1, ...layers.flatMap(l => l.values.map(v => v.y1)), ...totals);
  const ticks = niceTicks(peak);
  const top = ticks[ticks.length - 1];
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box, report);
  const [rawActive, setActive] = useState<number | null>(null);
  const active = rawActive === null || !n ? null : Math.min(rawActive, n - 1);
  const height = width < 480 ? 150 : 180;
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n > 1 ? i / (n - 1) : 0.5) * plotW;
  const y = (v: number) => PAD.top + plotH - (Math.min(v, top) / top) * plotH;
  const gaps = offlineRuns(report1.points, report1.step);
  const series = { stepMinutes, points: report1.points.map(p => ({ at: at(p.t).toISOString(), tpm: 0 })) };
  const xTicks = timeTicks(series, Math.max(3, Math.floor(plotW / 75)) + 2);
  const xLabels = xTicks.indexes.filter(i => x(i) - PAD.left >= 16 && PAD.left + plotW - x(i) >= 16);
  const rangeTotals = Object.fromEntries(USAGE_KINDS.map(kind => [kind, report1.points.reduce((sum, p) => sum + p[USAGE_FIELD[kind]], 0)])) as Record<UsageKind, number>;
  const busy = totals.indexOf(Math.max(...totals));
  const point = active !== null ? report1.points[active] : null;

  const pick = (clientX: number) => {
    const left = box.current?.getBoundingClientRect().left ?? 0;
    setActive(Math.max(0, Math.min(n - 1, Math.round(((clientX - left - PAD.left) / (plotW || 1)) * (n - 1)))));
  };
  const onKey = (event: KeyboardEvent) => {
    const moves: Record<string, number> = { ArrowLeft: (active ?? n) - 1, ArrowRight: (active ?? -1) + 1, Home: 0, End: n - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    setActive(Math.max(0, Math.min(n - 1, moves[event.key])));
  };

  return <section className="obs-chart" aria-label={t('machine.throughput')} aria-busy={report ? undefined : true}>
    <Figures report={report} />
    <header className="obs-chart-head">
      <span>{t('machine.throughput')} · {t('machine.perMinuteUnit')}{report ? ` · ${stepLabel(stepMinutes)}` : ''}</span>
      <span className="mu-head-side">
        {totals[busy] > 0 && <span className="mu-peak">{t('chart.peak')} <strong>{compactLabel(totals[busy])}</strong> · {dayLabel(at(report1.points[busy].t))} {clockLabel(at(report1.points[busy].t))}</span>}
        {/* persona keeps the by-kind/total choice here, beside the chart it changes. */}
        <span className="mu-mode" role="group" aria-label={t('machine.display')}>
          <button type="button" aria-pressed={mode === 'kinds'} className={mode === 'kinds' ? 'selected' : ''} onClick={() => onToggleMode('kinds')}>{t('machine.modeKinds')}</button>
          <button type="button" aria-pressed={mode === 'total'} className={mode === 'total' ? 'selected' : ''} onClick={() => onToggleMode('total')}>{t('machine.modeTotal')}</button>
        </span>
        <button className="icon-btn" aria-label={t('obs.refresh')} onClick={onRefresh}><IconRefresh size={16} /></button>
      </span>
    </header>
    <div ref={box} className="obs-chart-plot" style={{ height }} tabIndex={0} role="group"
      aria-label={t('machine.throughput')}
      onPointerDown={(e: PointerEvent) => pick(e.clientX)} onPointerMove={(e: PointerEvent) => pick(e.clientX)}
      onPointerLeave={(e: PointerEvent) => { if (e.pointerType === 'mouse') setActive(null); }}
      onKeyDown={onKey} onBlur={() => setActive(null)}>
      {width > 0 && <svg width={width} height={height} aria-hidden="true">
        {/* No report, no ticks: an empty plot waits, it does not promise a zero scale. */}
        {report && ticks.map(v => <g key={v}>
          <line className="obs-chart-grid" x1={PAD.left} x2={PAD.left + plotW} y1={y(v)} y2={y(v)} />
          <text className="obs-chart-axis" x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end">{compactLabel(v)}</text>
        </g>)}
        {report && xLabels.map(i => <text key={i} className="obs-chart-axis" x={x(i)} y={height - 6} textAnchor="middle">
          {xTicks.every < 1440 ? clockLabel(at(report1.points[i].t)) : dayLabel(at(report1.points[i].t))}
        </text>)}
        {/* Offline steps: grey over the plot, so quiet reads as quiet and gone reads as gone. */}
        {gaps.map(([start, end], i) => {
          const from = (start - report1.points[0].t) / (report1.step * n);
          const to = (end - report1.points[0].t) / (report1.step * n);
          return <rect key={i} className="mu-offline" x={PAD.left + from * plotW} width={Math.max(0, (to - from) * plotW)} y={PAD.top} height={plotH} />;
        })}
        {mode === 'kinds' ? (n > 0 ? layers.map(layer => {
          const up = layer.values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v.y1).toFixed(1)}`).join('');
          const down = [...layer.values.keys()].reverse().map(i => `L${x(i).toFixed(1)},${y(layer.values[i].y0).toFixed(1)}`).join('');
          // The 2px surface stroke is the gap the stacked kinds keep from each other.
          return <path key={layer.kind} d={`${up}${down}Z`} className="mu-stack" style={{ fill: USAGE_COLOR[layer.kind] }} />;
        }) : null) : n > 0 ? <>
          <path className="obs-chart-area" d={`M${x(0).toFixed(1)},${y(totals[0]).toFixed(1)}${totals.map((v, i) => `L${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')}L${x(n - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z`} />
          <path className="obs-chart-line" style={{ stroke: 'var(--chart-total)' }} d={totals.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')} />
        </> : null}
        {active !== null && <line className="obs-chart-cross" x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + plotH} />}
      </svg>}
      {point && width > 0 && <div className="obs-chart-tip" style={{ left: Math.max(0, Math.min(width - 200, x(active!) + 12)) }}>
        <span>{bucketLabel(at(point.t).toISOString(), stepMinutes)}</span>
        {mode === 'total'
          ? <div className="obs-chart-tip-row"><strong>{compactLabel(totals[active!])}</strong><span>{t('machine.total')}</span></div>
          : <>
            {layers.map(layer => <div key={layer.kind} className="obs-chart-tip-row">
              <i style={{ borderColor: USAGE_COLOR[layer.kind], borderTopStyle: 'solid' }} /><strong>{compactLabel(perMinute(point[USAGE_FIELD[layer.kind]], report1.step))}</strong><span>{KIND_LABEL[layer.kind]}</span>
            </div>)}
            <div className="obs-chart-tip-row"><strong>{compactLabel(perMinute(totalTokens(point), report1.step))}</strong><span>{t('machine.total')}</span></div>
          </>}
        <div className="obs-chart-tip-row"><strong>{numberLabel(point.replies)}</strong><span>{t('machine.replies')}</span></div>
      </div>}
    </div>
    {/* The legend keeps its row while the report loads (a dash where the total will be), so the
        card does not grow under the reader when the data lands. */}
    {mode === 'kinds' && <div className="obs-chart-legend" aria-label={t('machine.kinds')}>
      {USAGE_KINDS.map(kind => {
        const on = !hidden.has(kind);
        return <button key={kind} type="button" aria-pressed={on} disabled={!report || (on && kinds.length <= 1)}
          title={on ? t('chart.hide', KIND_LABEL[kind]) : t('chart.show', KIND_LABEL[kind])} onClick={() => onToggleKind(kind)}>
          <i style={{ borderColor: USAGE_COLOR[kind], borderTopStyle: 'solid' }} />{KIND_LABEL[kind]}<em>{report ? compactLabel(rangeTotals[kind]) : '—'}</em>
        </button>;
      })}
    </div>}
  </section>;
}

/** Cache hit rate over the range: one line, broken where replies were too few or the machine was
 *  offline (persona draws the same rule). */
function HitChart({ report }: { report: MachineUsageReport | null }) {
  const report1 = report ?? EMPTY_REPORT;
  const n = report1.points.length;
  const stepMinutes = report1.step / 60;
  const runs = report ? hitRuns(report1.points) : [];
  const box = useRef<HTMLDivElement>(null);
  const width = useWidth(box, report);
  const [rawActive, setActive] = useState<number | null>(null);
  const active = rawActive === null || !n ? null : Math.min(rawActive, n - 1);
  const height = width < 480 ? 110 : 130;
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n > 1 ? i / (n - 1) : 0.5) * plotW;
  const y = (v: number) => PAD.top + plotH - v * plotH;
  const series = { stepMinutes, points: report1.points.map(p => ({ at: at(p.t).toISOString(), tpm: 0 })) };
  const xTicks = timeTicks(series, Math.max(3, Math.floor(plotW / 75)) + 2);
  const xLabels = xTicks.indexes.filter(i => x(i) - PAD.left >= 16 && PAD.left + plotW - x(i) >= 16);
  const point = active !== null ? report1.points[active] : null;
  const pointRate = point ? hitRate(point) : null;

  const pick = (clientX: number) => {
    const left = box.current?.getBoundingClientRect().left ?? 0;
    setActive(Math.max(0, Math.min(n - 1, Math.round(((clientX - left - PAD.left) / (plotW || 1)) * (n - 1)))));
  };
  const onKey = (event: KeyboardEvent) => {
    const moves: Record<string, number> = { ArrowLeft: (active ?? n) - 1, ArrowRight: (active ?? -1) + 1, Home: 0, End: n - 1 };
    if (!(event.key in moves)) return;
    event.preventDefault();
    setActive(Math.max(0, Math.min(n - 1, moves[event.key])));
  };

  return <section className="obs-chart" aria-label={t('machine.hitChart')}>
    <header className="obs-chart-head">
      <span>{t('machine.hitChart')}</span>
      <span>{t('machine.hitNote')}</span>
    </header>
    <div ref={box} className="obs-chart-plot" style={{ height }} tabIndex={0} role="group"
      aria-label={t('machine.hitChart')}
      onPointerDown={(e: PointerEvent) => pick(e.clientX)} onPointerMove={(e: PointerEvent) => pick(e.clientX)}
      onPointerLeave={(e: PointerEvent) => { if (e.pointerType === 'mouse') setActive(null); }}
      onKeyDown={onKey} onBlur={() => setActive(null)}>
      {width > 0 && <svg width={width} height={height} aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map(v => <g key={v}>
          <line className="obs-chart-grid" x1={PAD.left} x2={PAD.left + plotW} y1={y(v)} y2={y(v)} />
          <text className="obs-chart-axis" x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end">{Math.round(v * 100)}%</text>
        </g>)}
        {report && xLabels.map(i => <text key={i} className="obs-chart-axis" x={x(i)} y={height - 6} textAnchor="middle">
          {xTicks.every < 1440 ? clockLabel(at(report1.points[i].t)) : dayLabel(at(report1.points[i].t))}
        </text>)}
        {runs.map((run, i) => <path key={i} className="obs-chart-line" style={{ stroke: 'var(--chart-total)' }}
          d={run.map((p, j) => {
            const index = report1.points.findIndex(pp => pp.t === p.t);
            return `${j ? 'L' : 'M'}${x(index).toFixed(1)},${y(p.v).toFixed(1)}`;
          }).join('')} />)}
      </svg>}
      {report && runs.length === 0 && <p className="mu-chart-empty">{t('machine.hitEmpty')}</p>}
      {point && width > 0 && <div className="obs-chart-tip" style={{ left: Math.max(0, Math.min(width - 200, x(active!) + 12)) }}>
        <span>{bucketLabel(at(point.t).toISOString(), stepMinutes)}</span>
        <div className="obs-chart-tip-row"><strong>{percentText(pointRate)}</strong><span>{t('machine.hitRate')}</span></div>
        <div className="obs-chart-tip-row"><strong>{numberLabel(point.replies)}</strong><span>{t('machine.replies')}</span></div>
      </div>}
    </div>
  </section>;
}

/** By model / by launcher: one row per share, busiest first; a row click filters the page to it. */
function ShareList({ title, shares, active, label, onPick }: {
  title: string;
  shares: MachineUsageReport['models'];
  active: string | null;
  label: (name: string) => string;
  onPick: (name: string | null) => void;
}) {
  const grand = shares.reduce((sum, s) => sum + totalTokens(s), 0);
  return <section className="mu-shares">
    <h3>{title}</h3>
    {!shares.length && <p className="panel-empty">{t('machine.nothing')}</p>}
    {shares.map(share => <button key={share.name} type="button" className={share.name === active ? 'selected' : ''}
      aria-pressed={share.name === active} onClick={() => onPick(share.name === active ? null : share.name)}>
      <span className="mu-share-name" title={label(share.name)}>{label(share.name)}</span>
      <span className="mu-share-numbers"><strong title={numberLabel(totalTokens(share))}>{compactLabel(totalTokens(share))}</strong>
        <small>{grand > 0 ? `${Math.round((totalTokens(share) / grand) * 100)}%` : '0%'}</small></span>
    </button>)}
  </section>;
}

function MachineDetail({ machine, onBack }: { machine: Machine; onBack: () => void }) {
  const [hours, setHours] = useState<1 | 24 | 168>(24);
  const [model, setModel] = useState<string | null>(null);
  const [launcher, setLauncher] = useState<string | null>(null);
  const [mode, setMode] = useState<'kinds' | 'total'>('kinds');
  const [hidden, setHidden] = useState<ReadonlySet<UsageKind>>(new Set());
  const [report, setReport] = useState<MachineUsageReport | null>(null);
  const [old, setOld] = useState(false);
  const [failed, setFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [agents, setAgents] = useState<AgentInfo[]>([]);

  useEffect(() => {
    let alive = true;
    void machine.api.agents().then(list => { if (alive) setAgents(list); }).catch(() => {});
    return () => { alive = false; };
  }, [machine.api]);

  useEffect(() => {
    let alive = true, pending = false;
    setReport(null); setOld(false); setFailed(false);
    const read = async () => {
      if (pending) return;
      pending = true;
      try {
        const data = await machine.api.machineUsage(hours, model, launcher);
        if (alive) { setReport(data); setOld(false); setFailed(false); }
      } catch (error) {
        if (!alive) return;
        setOld(error instanceof ApiError && error.status === 404);
        setFailed(true);
      } finally { pending = false; }
    };
    void read();
    const timer = setInterval(() => { if (!document.hidden) void read(); }, 30_000);
    return () => { alive = false; clearInterval(timer); };
  }, [machine.api, hours, model, launcher, refresh]);

  const toggleKind = (kind: UsageKind) => setHidden(prev => {
    const next = new Set(prev);
    if (!next.delete(kind)) next.add(kind);
    return next;
  });
  const launcherLabel = (name: string) => name === '' ? t('machine.otherLauncher') : agents.find(a => a.id === name)?.label ?? name;

  return <>
    <div className="mu-detail-head">
      <button type="button" className="text-btn" onClick={onBack}><IconBack size={16} />{t('machine.back')}</button>
      <h2>{machine.label}</h2>
    </div>
    {/* One row: a compact range control and the two filters, scrolling sideways when narrow
        (the by-kind/total choice lives in the chart's own header, where persona keeps it). */}
    <div className="mu-toolbar">
      <div className="mu-range" role="group" aria-label={t('activity.range')}>
        {([[1, 'machine.rangeHour'], [24, 'machine.rangeDay'], [168, 'machine.rangeWeek']] as const).map(([value, key]) =>
          <button key={value} type="button" aria-pressed={hours === value} className={hours === value ? 'selected' : ''} onClick={() => setHours(value)}>{t(key)}</button>)}
      </div>
      <Select label={t('machine.model')} short={t('machine.model')} value={model ?? ''} options={[{ id: '', label: t('machine.allModels') }, ...(report?.models ?? []).map(m => ({ id: m.name, label: m.name }))]} onPick={id => setModel(id || null)} />
      <Select label={t('machine.launcher')} short={t('machine.launcher')} value={launcher ?? ''} options={[{ id: '', label: t('machine.allLaunchers') }, ...(report?.launchers ?? []).map(l => ({ id: l.name || '-', label: launcherLabel(l.name) }))]} onPick={id => setLauncher(id === '' ? null : id === '-' ? '' : id)} />
    </div>
    {failed && <p role="alert" className="obs-error">{old ? t('machine.oldDaemon') : t('machine.loadFailed')}</p>}
    <div className="mu-detail">
      <div className="mu-charts">
        <ThroughputChart report={report} hidden={hidden} mode={mode} onToggleMode={next => { setMode(next); setHidden(new Set()); }} onToggleKind={toggleKind} onRefresh={() => setRefresh(n => n + 1)} />
        <HitChart report={report} />
      </div>
      <div className="mu-side">
        {report
          ? <>
            <ShareList title={t('machine.byModel')} shares={report.models} active={model} label={name => name} onPick={setModel} />
            <ShareList title={t('machine.byLauncher')} shares={report.launchers} active={launcher} label={launcherLabel} onPick={setLauncher} />
            {report.models.length === 0 && <p className="obs-note">{t('machine.nothing')}</p>}
          </>
          : [t('machine.byModel'), t('machine.byLauncher')].map(title => <section key={title} className="mu-shares" aria-busy="true">
            <h3>{title}</h3>
            <div className="mu-share-skeleton" /><div className="mu-share-skeleton" />
          </section>)}
      </div>
    </div>
  </>;
}

/** The machines page: every machine's cards, and one machine's detail behind its card. The
 *  address names the level — '' the list, 'local' the hub's own machine, a host id a remote. */
export function MachineUsagePage({ api, token, hosts, open, onOpen }: { api: Api; token: string; hosts: HostInfo[]; open: string; onOpen: (path: string) => void }) {
  const machines = useMemo<Machine[]>(() => [
    // In the demo the local machine is the demo api itself: nothing else is there to answer.
    { id: null, label: t('hosts.local'), host: null, api: api.demo ? api : new Api(token) },
    ...hosts.map(host => ({ id: host.id, label: host.label || host.target, host, api: api.demo ? api : new Api(token, '', host.id) })),
  ], [api, token, hosts]);
  const summaries = useSummaries(open ? NO_MACHINES : machines);
  return <section className="obs-page">
    <h1>{t('hosts.machines')}</h1>
    {open === ''
      ? <MachineList machines={machines} summaries={summaries} onOpen={id => onOpen(id ?? 'local')} />
      : <MachineDetail machine={machines.find(m => (m.id ?? '') === (open === 'local' ? '' : open)) ?? machines[0]} onBack={() => onOpen('')} />}
  </section>;
}
