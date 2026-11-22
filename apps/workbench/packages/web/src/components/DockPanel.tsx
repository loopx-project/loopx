import { useEffect, useLayoutEffect, useRef, useState, type PointerEvent, type ReactNode } from 'react';
import { t } from '../i18n';
import { clampWidth, savedWidth, WIDTH_KEY } from './FilesPanel';

/**
 * The column right of the conversation that everything opened from the overview goes into on a
 * computer: a page the agent wrote, one file's changes, the whole list of a group. One panel at a
 * time, the width of the files panel (shared, dragged from its left edge, kept on this device), so
 * the conversation stays readable beside it. On a phone each of these is a page of its own.
 */
export function DockPanel({ label, children, onDismiss, hidden = false }: { label: string; children: ReactNode; onDismiss?: () => void; hidden?: boolean }) {
  const [width, setWidth] = useState(() => clampWidth(savedWidth()));
  const panel = useRef<HTMLElement>(null);
  const hiddenRef = useRef(hidden); hiddenRef.current = hidden;
  useLayoutEffect(() => { if (!hidden) { setWidth(clampWidth(savedWidth())); panel.current?.querySelector<HTMLElement>('h1')?.focus({ preventScroll: true }); } }, [hidden]);
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (hiddenRef.current || event.key !== 'Escape' || event.defaultPrevented || event.isComposing || document.querySelector('.dialog-scrim, .lightbox, .pop-panel:not(.overview-dock)')) return;
      if (dismiss.current) { event.preventDefault(); dismiss.current(); }
    };
    document.addEventListener('keydown', key);
    return () => document.removeEventListener('keydown', key);
  }, []);
  const startResize = (e: PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = width;
    const move = (ev: globalThis.PointerEvent) => setWidth(clampWidth(startW + startX - ev.clientX));
    const up = (ev: globalThis.PointerEvent) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      localStorage.setItem(WIDTH_KEY, String(clampWidth(startW + startX - ev.clientX)));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  useEffect(() => {
    const onResize = () => setWidth((w) => clampWidth(w));
    onResize();
    window.addEventListener('resize', onResize);
    const sidebar = document.querySelector('.shell > .sidebar');
    const observer = sidebar ? new ResizeObserver(onResize) : null;
    if (observer && sidebar) observer.observe(sidebar);
    return () => { window.removeEventListener('resize', onResize); observer?.disconnect(); };
  }, []);
  return <aside ref={panel} hidden={hidden} className="files-panel dock-panel" style={{ width }} aria-label={label}>
    <div className="files-resize" onPointerDown={startResize} role="separator" aria-orientation="vertical" aria-label={t('common.resize')} />
    {children}
  </aside>;
}
