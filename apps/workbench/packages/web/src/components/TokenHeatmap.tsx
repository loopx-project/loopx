import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { Api } from '../api';
import { compactLabel, heatGrid, type DailyUsage, type HeatCell } from '../observability';
import { t } from '../i18n';

const GAP = 3;
const MIN_CELL = 10;
const MAX_CELL = 14;
const MONTH_ROW = 18;

/** What one day says: its tokens and turns, or why there is no number for it. */
function dayText(cell: HeatCell): string {
  if (!cell.recorded) return t('heatmap.notRecorded');
  if (cell.value == null) return t('heatmap.unmeasured', cell.turns);
  if (!cell.value) return t('heatmap.zero');
  return t('heatmap.value', compactLabel(cell.value), cell.turns);
}

/**
 * Tokens per day over the last year as a grid of weeks, as many as the width holds, newest on the
 * right. One line above it reads the day under the pointer, finger or arrow keys, else the sum.
 */
export function TokenHeatmap({ api, agent, metric, refresh }: { api: Api; agent: string; metric: 'total' | 'output'; refresh: number }) {
  const [daily, setDaily] = useState<DailyUsage | null>(null);
  const [failed, setFailed] = useState(false);
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<number | null>(null);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    setDaily(null); setFailed(false); setActive(null);
    api.usageDaily(agent).then(value => { if (alive) setDaily(value); }).catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; };
  }, [api, agent, refresh]);

  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const weeks = Math.max(1, Math.min(53, Math.floor((width + GAP) / (MIN_CELL + GAP))));
  const cell = Math.max(MIN_CELL, Math.min(MAX_CELL, Math.floor((width - GAP * (weeks - 1)) / weeks)));
  const pitch = cell + GAP;
  const grid = useMemo(() => daily && width ? heatGrid(daily, metric, weeks, new Date()) : null, [daily, metric, weeks, width]);
  const cells = useMemo(() => grid?.columns.flat() ?? [], [grid]);
  const shown = active === null ? null : cells[Math.min(active, cells.length - 1)] ?? null;
  const summary = grid ? t('heatmap.summary', compactLabel(grid.total), grid.activeDays) : '';
  // Month names need room: one that would run into the next is left out.
  const months = grid?.months.filter((m, i, all) => (all[i + 1]?.column ?? Infinity) - m.column >= 3 && m.column * pitch + 24 <= weeks * pitch) ?? [];

  const pick = (event: PointerEvent) => {
    const rect = box.current?.getBoundingClientRect();
    if (!rect || !grid) return;
    const column = Math.floor((event.clientX - rect.left) / pitch), row = Math.floor((event.clientY - rect.top) / pitch);
    if (column < 0 || column >= grid.columns.length || row < 0 || row >= (grid.columns[column]?.length ?? 0)) return;
    setActive(column * 7 + row);
  };
  const onKey = (event: KeyboardEvent) => {
    const last = cells.length - 1;
    const from = active ?? last;
    const moves: Record<string, number> = { ArrowUp: from - 1, ArrowDown: from + 1, ArrowLeft: from - 7, ArrowRight: from + 7, Home: 0, End: last };
    if (!(event.key in moves)) return;
    event.preventDefault();
    setActive(Math.max(0, Math.min(last, active === null ? last : moves[event.key])));
  };

  return <section className="obs-chart obs-heatmap" aria-label={t('heatmap.title', metric === 'output')}>
    <header className="obs-chart-head">
      <span>{t('heatmap.title', metric === 'output')} · {t('heatmap.daily')}</span>
      {shown ? <span><strong>{t('activity.day', new Date(`${shown.date}T00:00:00`))}</strong> · {dayText(shown)}</span>
        : grid && <span>{summary}</span>}
    </header>
    <div ref={box} className="obs-heatmap-plot" tabIndex={grid ? 0 : -1} role="group" aria-label={grid ? t('heatmap.aria', summary) : undefined}
      onPointerDown={pick} onPointerMove={pick} onPointerLeave={(e: PointerEvent) => { if (e.pointerType === 'mouse') setActive(null); }}
      onKeyDown={onKey} onBlur={() => setActive(null)}>
      {failed ? <p className="obs-error" role="alert">{t('obs.loadFailed')}</p>
        : !grid ? <p className="obs-heatmap-wait">{t('obs.loading')}</p>
        : <svg width={weeks * pitch - GAP} height={7 * pitch - GAP + MONTH_ROW} aria-hidden="true">
          {grid.columns.map((column, c) => column.map((day, r) => {
            const index = c * 7 + r;
            return <rect key={day.date} x={c * pitch} y={r * pitch} width={cell} height={cell} rx={3}
              className={`heat-cell ${day.level === null ? 'unknown' : `level-${day.level}`}${index === active ? ' active' : ''}`} />;
          }))}
          {months.map(m => <text key={m.column} className="obs-chart-axis" x={m.column * pitch} y={7 * pitch - GAP + MONTH_ROW - 4}>{t('heatmap.month', m.date)}</text>)}
        </svg>}
    </div>
  </section>;
}
