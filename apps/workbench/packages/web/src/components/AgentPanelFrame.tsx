import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type RefObject } from 'react';
import { t } from '../i18n';

/** The width the user dragged the panel to, on a wide screen. Unset, the panel keeps its own. */
const WIDTH_KEY = 'calli.agents.width';
const MIN_WIDTH = 320;
/** What the conversation beside the panel is always left with. */
const MIN_MAIN = 360;
const clampWidth = (w: number, container?: number) => {
  const room = (container && container > 0 ? container : window.innerWidth) - MIN_MAIN;
  return Math.max(MIN_WIDTH, Math.min(w, Math.max(MIN_WIDTH, room)));
};

/** The same resizable conversation panel for team members and referenced threads. */
export function AgentPanelFrame({ children, detail = false, panelRef, label }: {
  children: ReactNode; detail?: boolean; panelRef?: RefObject<HTMLElement>; label: string;
}) {
  const [width, setWidth] = useState<number | null>(() => { const saved = Number(localStorage.getItem(WIDTH_KEY)); return saved > 0 ? clampWidth(saved) : null; });
  const ownPanel = useRef<HTMLElement>(null);
  const panel = panelRef ?? ownPanel;
  const containerWidth = () => panel.current?.parentElement?.getBoundingClientRect().width;
  const startResize = (e: ReactPointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = panel.current?.getBoundingClientRect().width ?? MIN_WIDTH;
    const at = (ev: PointerEvent) => clampWidth(startW + startX - ev.clientX, containerWidth());
    const move = (ev: PointerEvent) => setWidth(at(ev));
    const up = (ev: PointerEvent) => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      localStorage.setItem(WIDTH_KEY, String(at(ev)));
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };
  // A window made narrower must not leave the panel wider than the room there is.
  useEffect(() => {
    const fit = () => setWidth((w) => (w === null ? w : clampWidth(w, containerWidth())));
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, []);
  return <aside ref={panel} tabIndex={-1} className={`agent-panel${detail ? ' detail' : ''}`} role="region" aria-label={label} style={width === null ? undefined : { ['--agent-panel-width' as string]: `${width}px` }}>
    <div className="agent-panel-resize" onPointerDown={startResize} onDoubleClick={() => { setWidth(null); localStorage.removeItem(WIDTH_KEY); }} role="separator" aria-orientation="vertical" aria-label={t('common.resize')} title={t('team.resizeHint')} />
    {children}
  </aside>;
}
