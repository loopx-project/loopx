import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { AgentInfo } from '../api';
import { bucketLabel, chartLines, clockLabel, compactLabel, dayLabel, niceTicks, numberLabel, stepLabel, timeTicks, type UsageSeries } from '../observability';
import { formatList, t } from '../i18n';

const PAD = { top: 10, right: 12, bottom: 26, left: 48 };

/** TPM over the selected range: the total and each instance, a crosshair readout on hover, touch and arrow keys. */
export function TpmChart({ series, agents, empty: noMeasurements, metric = 'total' }: { series: UsageSeries; agents: AgentInfo[]; empty: boolean; metric?: 'total' | 'output' }) {
  const empty = noMeasurements || series.points.length === 0;
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [rawActive, setActive] = useState<number | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set());
  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, [empty]);

  const { points, stepMinutes } = series;
  const n = points.length;
  const active = rawActive === null || !n ? null : Math.min(rawActive, n - 1);
  const lines = chartLines(series, agents, metric);
  const shown = lines.filter(l => !hidden.has(l.id));
  const visible = shown.length ? shown : lines;
  const total = lines[0];
  const peak = total.tpm.reduce((best, v, i) => v > total.tpm[best] ? i : best, 0);
  const ticks = niceTicks(visible.reduce((max, l) => l.tpm.reduce((m, v) => Math.max(m, v), max), 0));
  const top = ticks[ticks.length - 1];
  const height = width < 480 ? 150 : 180;
  const plotW = Math.max(0, width - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (n > 1 ? i / (n - 1) : 0.5) * plotW;
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const path = (tpm: number[]) => tpm.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('');
  // Labels too close to an edge would run into the y-axis or out of the card.
  const xTicks = timeTicks(series, Math.max(3, Math.floor(plotW / 75)) + 2);
  const xLabels = xTicks.indexes.filter(i => x(i) - PAD.left >= 16 && PAD.left + plotW - x(i) >= 16);
  const peakAt = new Date(points[peak]?.at ?? 0);
  const totalShown = visible.includes(total);

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
  const toggle = (id: string) => setHidden(prev => {
    const next = new Set(prev);
    if (!next.delete(id)) next.add(id);
    return next;
  });
  const dot = (tpm: number[], i: number, color: string, key: string) => <circle key={key} className="obs-chart-dot" style={{ fill: color }} cx={x(i)} cy={y(tpm[i])} r={4} />;

  return <section className="obs-chart" aria-label={t('chart.label')}>
    <header className="obs-chart-head">
      <span>{t('chart.trend', metric === 'output' ? t('chart.outputTpm') : 'TPM')} · {stepLabel(stepMinutes)}{series.estimated ? ` · ${t('chart.estimated')}` : ''}</span>
      {!empty && <span>{t('chart.peak')} <strong>{compactLabel(total.tpm[peak])}</strong> · {dayLabel(peakAt)} {clockLabel(peakAt)}</span>}
    </header>
    {empty ? <p className="obs-chart-empty">{t('chart.empty')}</p> : <>
      <div ref={box} className="obs-chart-plot" style={{ height }} tabIndex={0} role="group"
        aria-label={t('chart.aria', formatList(visible.map(l => l.label)), compactLabel(total.tpm[peak]), `${dayLabel(peakAt)} ${clockLabel(peakAt)}`)}
        onPointerDown={(e: PointerEvent) => pick(e.clientX)} onPointerMove={(e: PointerEvent) => pick(e.clientX)}
        onPointerLeave={(e: PointerEvent) => { if (e.pointerType === 'mouse') setActive(null); }}
        onKeyDown={onKey} onBlur={() => setActive(null)}>
        {width > 0 && <svg width={width} height={height} aria-hidden="true">
          {ticks.map(v => <g key={v}>
            <line className="obs-chart-grid" x1={PAD.left} x2={PAD.left + plotW} y1={y(v)} y2={y(v)} />
            <text className="obs-chart-axis" x={PAD.left - 8} y={y(v)} dy="0.32em" textAnchor="end">{compactLabel(v)}</text>
          </g>)}
          {xLabels.map(i => <text key={i} className="obs-chart-axis" x={x(i)} y={height - 6} textAnchor="middle">
            {xTicks.every < 1440 ? clockLabel(new Date(points[i].at)) : dayLabel(new Date(points[i].at))}
          </text>)}
          {/* Instances underneath, the total on top with its wash. */}
          {visible.filter(l => l !== total).map(l => <path key={l.id} className="obs-chart-line" style={{ stroke: l.color, strokeDasharray: l.dash }} d={path(l.tpm)} />)}
          {totalShown && <>
            <path className="obs-chart-area" d={`${path(total.tpm)}L${x(n - 1).toFixed(1)},${y(0)}L${x(0).toFixed(1)},${y(0)}Z`} />
            <path className="obs-chart-line" style={{ stroke: total.color }} d={path(total.tpm)} />
          </>}
          {active !== null && <line className="obs-chart-cross" x1={x(active)} x2={x(active)} y1={PAD.top} y2={PAD.top + plotH} />}
          {active === null
            ? totalShown && total.tpm[peak] > 0 && dot(total.tpm, peak, total.color, 'peak')
            : [...visible].reverse().map(l => dot(l.tpm, active, l.color, l.id))}
        </svg>}
        {active !== null && width > 0 && <div className="obs-chart-tip" style={{ left: Math.max(0, Math.min(width - 200, x(active) + 12)) }}>
          <span>{bucketLabel(points[active].at, stepMinutes)}</span>
          {visible.map(l => <div key={l.id} className="obs-chart-tip-row">
            <i style={{ borderColor: l.color, borderTopStyle: l.dash ? 'dashed' : 'solid' }} /><strong>{compactLabel(l.tpm[active])}</strong>{lines.length > 1 && <span>{l.label}</span>}
          </div>)}
        </div>}
      </div>
      {lines.length > 1 && <div className="obs-chart-legend" aria-label={t('chart.lines')}>
        {lines.map(l => {
          const on = visible.includes(l);
          return <button key={l.id} type="button" aria-pressed={on} disabled={on && visible.length === 1} title={on ? t('chart.hide', l.label) : t('chart.show', l.label)} onClick={() => toggle(l.id)}>
            <i style={{ borderColor: l.color, borderTopStyle: l.dash ? 'dashed' : 'solid' }} />{l.label}
          </button>;
        })}
      </div>}
      <details className="obs-chart-table"><summary>{t('chart.table')}</summary><div>
        <table><thead><tr><th>{t('chart.period')}</th>{visible.map(l => <th key={l.id}>{l.label}</th>)}</tr></thead>
          <tbody>{points.map((p, i) => <tr key={p.at}><th>{bucketLabel(p.at, stepMinutes)}</th>{visible.map(l => <td key={l.id}>{numberLabel(l.tpm[i])}</td>)}</tr>)}</tbody></table>
      </div></details>
    </>}
  </section>;
}
