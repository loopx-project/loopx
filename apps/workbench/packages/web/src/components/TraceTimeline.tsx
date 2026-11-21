import { useMemo, useState } from 'react';
import type { TraceSegment, TraceStep, TurnTrace } from '../api';
import { t } from '../i18n';

/** 65 → 1:05, 0 → 0:00: an axis label nobody has to think about. */
const clock = (ms: number): string => {
  const total = Math.round(ms / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
};

/** 5 10 15 30 60 120 300 …: the first step that keeps the axis at a handful of ticks. */
const TICKS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800];

interface Bar {
  kind: 'segment' | 'tool';
  /** The step this bar selects (1-based, as the list numbers them); null when it maps to none. */
  key: string | null;
  start: number;
  end: number;
  waitMs: number;
  genMs: number;
  failed: boolean;
  running: boolean;
  title: string;
}

const isFailed = (step: TraceStep): boolean => step.kind === 'tool' && (step.status === 'failed' || step.status === 'error');

/** How long a step still open has been going, when the trace itself does not say. */
const horizon = (trace: TurnTrace): number => Date.parse(trace.endedAt ?? new Date().toISOString());

/**
 * The turn drawn in real time: one bar per model segment (waiting, then generating) and one per
 * tool, each at its true start and length. Hovering a bar says when it began and how long it
 * waited and generated; clicking it selects its step below. Bars fade while one is selected, and
 * a hovered list row outlines its bar here.
 */
export function TraceTimeline({ trace, selectedKey, hoverKey, onSelect }: {
  trace: TurnTrace;
  selectedKey: string | null;
  hoverKey: string | null;
  onSelect: (key: string | null) => void;
}) {
  const [tipIndex, setTipIndex] = useState<number | null>(null);

  const bars = useMemo<Bar[]>(() => {
    const last = horizon(trace);
    const segmentBars = (trace.segments ?? []).map((seg: TraceSegment): Bar => {
      const start = Date.parse(seg.start);
      const end = seg.end != null ? Date.parse(seg.end) : Math.max(last, start);
      const first = seg.firstOutput != null ? Date.parse(seg.firstOutput) : end;
      const key = seg.steps.length ? String(seg.steps[seg.steps.length - 1]) : null;
      const step = seg.steps.length ? trace.steps[seg.steps[seg.steps.length - 1] - 1] : undefined;
      return {
        kind: 'segment', key, start, end,
        waitMs: Math.max(0, first - start),
        genMs: Math.max(0, end - first),
        failed: false,
        running: seg.end == null,
        title: step?.kind === 'request' && step.model ? step.model : t('trace.kind.request'),
      };
    });
    const toolBars = trace.steps.flatMap((step, i): Bar[] => {
      if (step.kind !== 'tool') return [];
      const start = Date.parse(step.ts);
      const end = step.endTs != null ? Date.parse(step.endTs) : Math.max(last, start);
      return [{
        kind: 'tool', key: String(i + 1), start, end,
        waitMs: 0, genMs: 0,
        failed: isFailed(step), running: step.endTs == null,
        title: step.title && step.title !== step.tool ? step.title : step.tool ?? t('trace.kind.tool'),
      }];
    });
    return [...segmentBars, ...toolBars].filter((b) => Number.isFinite(b.start) && b.end >= b.start);
  }, [trace]);

  // The axis spans the whole turn: from the ask to the last thing that happened in it.
  const origin = Date.parse(trace.startedAt);
  const horizonMs = trace.endedAt != null ? Date.parse(trace.endedAt) : horizon(trace);
  const total = Math.max(
    1,
    horizonMs - origin,
    ...bars.map((b) => b.end - origin),
    ...bars.map((b) => b.start - origin),
  );
  const step = TICKS.find((v) => total / 1000 / v <= 4.5) ?? 3600;
  const ticks: number[] = [];
  for (let v = 0; v <= total + 500; v += step * 1000) ticks.push(v);
  const at = (ms: number) => `${Math.max(0, Math.min(100, ((ms - origin) / total) * 100))}%`;
  const selectable = selectedKey != null;
  const canHover = typeof window !== 'undefined' && window.matchMedia?.('(hover: hover)').matches;

  const tipText = (bar: Bar): string[] => [
    t('trace.at', clock(bar.start - origin)),
    bar.kind === 'segment' ? t('trace.wait', clock(bar.waitMs)) : null,
    bar.kind === 'segment' ? (bar.running ? t('trace.generating') : t('trace.gen', clock(bar.genMs))) : null,
  ].filter((v): v is string => v !== null);

  const center = (bar: Bar): string => {
    const pct = ((bar.start - origin) / total) * 100 + Math.max(0.6, ((bar.end - bar.start) / total) * 100) / 2;
    return `clamp(70px, ${Math.max(0, Math.min(100, pct))}%, calc(100% - 70px))`;
  };

  return (
    <div className={`ov${selectable ? ' has-sel' : ''}`} role="img" aria-label={t('trace.timeline')}>
      <div className="ov-plot">
        {ticks.slice(1).map((v) => (
          <span key={v} className="ov-grid" style={{ left: `${Math.min(100, (v / total) * 100)}%` }} aria-hidden="true" />
        ))}
        {bars.map((bar, i) => {
          const selected = bar.key != null && bar.key === selectedKey;
          const hovered = bar.key != null && bar.key === hoverKey;
          return (
            <div key={`${bar.kind}-${i}`} className="ov-row">
              <button
                type="button"
                className={[
                  'ov-bar',
                  // 'seg', not 'segment': a permission chip class of the same name exists.
                  bar.kind === 'segment' ? 'ov-seg' : 'tool',
                  bar.failed ? 'fail' : '',
                  bar.running ? 'live' : '',
                  selected ? 'sel' : '',
                  hovered ? 'hl' : '',
                ].filter(Boolean).join(' ')}
                style={{ left: at(bar.start), width: `${Math.max(0.6, ((bar.end - bar.start) / total) * 100)}%` }}
                aria-label={`${bar.title} · ${clock(bar.start - origin)}`}
                onClick={() => onSelect(selected ? null : bar.key)}
                onPointerEnter={(e) => { if (canHover && e.pointerType === 'mouse') setTipIndex(i); }}
                onPointerLeave={() => setTipIndex(null)}
              >
                {bar.kind === 'segment' && (
                  <>
                    <span className="ov-wait" style={{ width: bar.waitMs + bar.genMs > 0 ? `${(bar.waitMs / (bar.waitMs + bar.genMs)) * 100}%` : '0%' }} />
                    <span className="ov-gen" />
                  </>
                )}
              </button>
              {tipIndex === i && (
                <div className="ov-tip" style={{ left: center(bar) }} role="presentation">
                  <strong>{bar.title}</strong>
                  {tipText(bar).map((line) => <span key={line}>{line}</span>)}
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="ov-axis" aria-hidden="true">
        {ticks.map((v) => (
          <span key={v} style={ticks[0] === v ? undefined : { left: `${Math.min(100, (v / total) * 100)}%` }}>{clock(v)}</span>
        ))}
      </div>
      <div className="ov-legend" aria-hidden="true">
        <span><i className="ov-legend-wait" />{t('trace.legendWait')}</span>
        <span><i className="ov-legend-gen" />{t('trace.legendGen')}</span>
        <span><i className="ov-legend-tool" />{t('trace.legendTool')}</span>
      </div>
    </div>
  );
}
