import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { AgentInfo, Thread } from '../api';
import { useStoredFlag } from '../hooks';
import { folderName } from '../paths';
import { projectDir } from '../sidebar';
import { QUICK_SWITCH_HIDDEN_KEY, clampQuickSwitchBottom, savedQuickSwitchBottom, saveQuickSwitchBottom } from '../quick-switch';
import { t } from '../i18n';
import { AgentMark, IconArrowLeft } from './icons';

const QUICK_SWITCH_SIDE = 10;
export function ThreadQuickSwitch({ threads, selectedId, agents, onSelect }: {
  threads: Thread[];
  selectedId: string;
  agents: AgentInfo[];
  onSelect: (id: string) => void;
}) {
  const selectedIndex = threads.findIndex((thread) => thread.id === selectedId);
  const items = useMemo(() => selectedIndex < 0 ? [] : threads.slice(Math.max(0, selectedIndex - QUICK_SWITCH_SIDE), Math.min(threads.length, selectedIndex + QUICK_SWITCH_SIDE + 1)), [threads, selectedIndex]);
  const root = useRef<HTMLDivElement>(null);
  const current = useRef<HTMLButtonElement | null>(null);
  const start = useRef<{ pointerId: number; x: number; y: number; bottom: number; moved: boolean } | null>(null);
  const [open, setOpen] = useState(false);
  const [hidden, setHidden] = useStoredFlag(QUICK_SWITCH_HIDDEN_KEY);
  const [bottom, setBottom] = useState(savedQuickSwitchBottom);
  const agentById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  // The panel is short: it opens on the current session, with its neighbours above and below.
  useLayoutEffect(() => { if (open) current.current?.scrollIntoView({ block: 'center' }); }, [open]);
  useEffect(() => {
    if (!open) return;
    // Dismissing is all a tap outside does: what lies under it is not pressed as well.
    const onPointerDown = (e: PointerEvent) => {
      if (!root.current || root.current.contains(e.target as Node)) return;
      setOpen(false);
      const swallow = (click: MouseEvent) => { click.stopPropagation(); click.preventDefault(); };
      document.addEventListener('click', swallow, { capture: true, once: true });
      window.setTimeout(() => document.removeEventListener('click', swallow, { capture: true }), 500);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown, true);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);
  if (hidden || selectedIndex < 0 || items.length <= 1) return null;
  const release = (el: HTMLElement, pointerId: number) => {
    if (el.hasPointerCapture(pointerId)) el.releasePointerCapture(pointerId);
    start.current = null;
  };
  return (
    <div className="quick-switch" ref={root}>
      {/* A small visible handle near the right edge, inset from iOS's own edge gestures. Tap it or
          pull it left to open the panel; drag it up or down to move it out of the way of text. It
          covers only its own few pixels, so selecting text elsewhere is never intercepted. */}
      <button
        type="button"
        className={`quick-switch-handle${open ? ' open' : ''}`}
        style={{ bottom: `calc(${bottom}px + var(--safe-bottom))` }}
        aria-label={t('quickSwitch.sessions')}
        aria-expanded={open}
        onPointerDown={(e) => {
          if (e.pointerType === 'mouse' && e.button !== 0) return;
          e.currentTarget.setPointerCapture(e.pointerId);
          start.current = { pointerId: e.pointerId, x: e.clientX, y: e.clientY, bottom, moved: false };
        }}
        onPointerMove={(e) => {
          const from = start.current;
          if (from?.pointerId !== e.pointerId) return;
          const dx = from.x - e.clientX;
          const dy = e.clientY - from.y;
          if (!from.moved && dx > 24 && dx > Math.abs(dy)) { release(e.currentTarget, e.pointerId); setOpen(true); return; }
          if (!from.moved && Math.abs(dy) > 8) from.moved = true;
          if (from.moved) setBottom(clampQuickSwitchBottom(from.bottom - dy));
        }}
        onPointerUp={(e) => {
          const from = start.current;
          if (from?.pointerId !== e.pointerId) return;
          release(e.currentTarget, e.pointerId);
          if (from.moved) saveQuickSwitchBottom(bottom);
          else setOpen((was) => !was);
        }}
        onPointerCancel={(e) => {
          if (start.current?.pointerId === e.pointerId) release(e.currentTarget, e.pointerId);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault();
            const next = clampQuickSwitchBottom(bottom + (e.key === 'ArrowUp' ? 24 : -24));
            setBottom(next);
            saveQuickSwitchBottom(next);
          }
        }}
        onClick={(e) => { if (e.detail === 0) setOpen((was) => !was); }}
      ><IconArrowLeft size={12} /></button>
      {open && (
        <div className="quick-switch-panel" role="listbox" aria-label={t('quickSwitch.sessions')}>
          {items.map((thread) => {
            const agent = agentById.get(thread.agent);
            const isCurrent = thread.id === selectedId;
            const place = thread.scratch ? t('projects.none') : folderName(projectDir(thread).cwd);
            return (
              <button
                type="button"
                key={thread.id}
                ref={isCurrent ? current : undefined}
                className={`quick-switch-card${isCurrent ? ' current' : ''}`}
                role="option"
                aria-selected={isCurrent}
                onClick={() => {
                  setOpen(false);
                  if (!isCurrent) onSelect(thread.id);
                }}
              >
                <span className="quick-switch-title">{thread.title}</span>
                <span className="quick-switch-meta">
                  {agent?.icon && <AgentMark path={agent.icon} size={13} />}
                  <span>{agent?.label ?? thread.agent}</span>
                  <span>{place}</span>
                  {thread.status === 'running' && <span className="dot running" aria-label={t('status.running')} />}
                  {thread.waiting && <span className="dot waiting" aria-label={t('notices.unknown.label')} />}
                  {thread.unread && !isCurrent && <span className="dot unread" aria-label={t('sidebar.unread')} />}
                </span>
              </button>
            );
          })}
          <button type="button" className="quick-switch-hide" onClick={() => { setOpen(false); setHidden(true); }}>{t('quickSwitch.hide')}</button>
        </div>
      )}
    </div>
  );
}
