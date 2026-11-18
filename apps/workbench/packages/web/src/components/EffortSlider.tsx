import { useState, type CSSProperties, type PointerEvent } from 'react';
import { EFFORT_LABELS } from '../permissions';
import { t } from '../i18n';

interface Props {
  levels: string[];
  value: string | null;
  onChange: (value: string | null) => void;
  onPreview: (value: string | null | undefined) => void;
}

/**
 * A discrete, pointer- and keyboard-operated slider. Dragging previews; releasing commits once.
 * The thumb follows the finger between the levels and settles on the nearest one when let go.
 */
export function EffortSlider({ levels, value, onChange, onPreview }: Props) {
  const options = [null, ...levels];
  // Where the finger is, in levels (fractional); null when not dragging.
  const [dragAt, setDragAt] = useState<number | null>(null);
  const index = dragAt === null ? Math.max(0, options.indexOf(value)) : Math.round(dragAt);
  const at = dragAt ?? index;
  const label = (i: number) => options[i] === null ? t('pickers.default') : (EFFORT_LABELS[options[i]!] ?? options[i]!);
  const position = (i: number) => `${i / levels.length * 100}%`;
  const levelAt = (event: PointerEvent<HTMLDivElement>) => {
    const rail = event.currentTarget.querySelector('.effort-rail')!.getBoundingClientRect();
    return Math.max(0, Math.min(levels.length, (event.clientX - rail.left) / rail.width * levels.length));
  };
  const preview = (level: number) => { setDragAt(level); onPreview(options[Math.round(level)]); };
  const clear = () => { setDragAt(null); onPreview(undefined); };
  return (
    <div
      className={`effort-slider${index === 0 ? ' is-default' : ''}${dragAt === null ? '' : ' dragging'}`}
      role="slider"
      tabIndex={0}
      aria-label={t('effort.label')}
      aria-valuemin={0}
      aria-valuemax={levels.length}
      aria-valuenow={index}
      aria-valuetext={label(index)}
      aria-orientation="horizontal"
      style={{ '--effort-position': position(at), '--effort-fraction': at / levels.length } as CSSProperties}
      onPointerDown={(event) => {
        if (!event.isPrimary || event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus();
        event.currentTarget.setPointerCapture(event.pointerId);
        preview(levelAt(event));
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) preview(levelAt(event));
      }}
      onPointerUp={(event) => {
        if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
        onChange(options[Math.round(levelAt(event))]);
        clear();
        event.currentTarget.releasePointerCapture(event.pointerId);
      }}
      onPointerCancel={clear}
      onLostPointerCapture={clear}
      onKeyDown={(event) => {
        let next = index;
        if (event.key === 'ArrowRight' || event.key === 'ArrowUp') next++;
        else if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') next--;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = levels.length;
        else return;
        event.preventDefault();
        onChange(options[Math.max(0, Math.min(levels.length, next))]);
      }}
    >
      <div className="effort-fill" />
      <div className="effort-rail">
        {options.map((option, i) => (
          <span key={option ?? 'default'} className={`effort-dot${i <= at ? ' filled' : ''}`} style={{ left: position(i) }} title={label(i)} />
        ))}
        <span className="effort-thumb" />
      </div>
    </div>
  );
}
