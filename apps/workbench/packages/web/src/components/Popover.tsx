import { createPortal } from 'react-dom';
import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type CSSProperties, type RefObject } from 'react';

const ParentPanels = createContext<string[]>([]);

interface Props {
  /** Accessible name of the panel. */
  label: string;
  triggerClassName?: string;
  triggerTitle?: string;
  triggerLabel?: string;
  triggerBusy?: boolean;
  panelClassName?: string;
  trigger?: ReactNode;
  triggerRef?: RefObject<HTMLButtonElement>;
  /** A menu action can open another panel at the same button without adding a second trigger. */
  anchorRef?: RefObject<HTMLElement>;
  /** Which edge of the trigger the panel lines up with on wide screens. */
  align?: 'start' | 'end';
  placement?: 'above' | 'below';
  disabled?: boolean;
  /** Opens the panel each time this number changes, so something else can trigger it. */
  openSignal?: number;
  /** Called once when the panel opens, whether by click or by an open signal. */
  onOpen?: () => void;
  /** Read a cold panel before showing it; progress stays in the existing trigger. */
  prepare?: () => Promise<void>;
  children: (close: () => void) => ReactNode;
}

/**
 * A button that opens one anchored surface above or below it on every screen size.
 * Used instead of native <select> so menus look and behave the same everywhere.
 */
export function Popover({
  label,
  triggerClassName,
  triggerTitle,
  triggerLabel,
  triggerBusy,
  panelClassName,
  trigger,
  triggerRef,
  anchorRef,
  align = 'start',
  placement = 'above',
  disabled,
  openSignal,
  onOpen,
  prepare,
  children,
}: Props) {
  const [open, setOpen] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const request = useRef(0);
  const close = () => { request.current++; setPreparing(false); setOpen(false); };
  const show = () => {
    if (!prepare) { setOpen(true); return; }
    const ticket = ++request.current;
    setPreparing(true);
    void prepare().then(() => {
      if (request.current !== ticket) return;
      setPreparing(false); setOpen(true);
    }, () => { if (request.current === ticket) setPreparing(false); });
  };
  useEffect(() => () => { request.current++; }, []);
  const panelId = useId();
  const parentPanels = useContext(ParentPanels);
  const rootRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);

  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    const anchor = anchorRef?.current;
    if (!open || !anchor) return;
    const controls = anchor.getAttribute('aria-controls');
    const expanded = anchor.getAttribute('aria-expanded');
    anchor.setAttribute('aria-controls', panelId);
    anchor.setAttribute('aria-expanded', 'true');
    return () => {
      if (controls === null) anchor.removeAttribute('aria-controls'); else anchor.setAttribute('aria-controls', controls);
      if (expanded === null) anchor.removeAttribute('aria-expanded'); else anchor.setAttribute('aria-expanded', expanded);
    };
  }, [open, anchorRef, panelId]);

  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const at = anchorRef?.current ?? rootRef.current;
      if (!at || !panelRef.current) return;
      const anchor = at.getBoundingClientRect();
      const panel = { width: panelRef.current.offsetWidth, height: panelRef.current.offsetHeight };
      const viewport = window.visualViewport;
      const edge = 12;
      const minY = (viewport?.offsetTop ?? 0) + edge;
      const maxY = minY + (viewport?.height ?? innerHeight) - edge * 2;
      const below = maxY - anchor.bottom - 8;
      const above = anchor.top - minY - 8;
      const belowFirst = placement === 'below';
      const useBelow = belowFirst ? below >= Math.min(panel.height, 240) || below >= above : above < Math.min(panel.height, 240) && below > above;
      const height = Math.max(0, Math.min(520, maxY - minY, Math.max(80, useBelow ? below : above)));
      const top = Math.max(minY, Math.min(useBelow ? anchor.bottom + 8 : anchor.top - Math.min(panel.height, height) - 8, maxY - Math.min(panel.height, height)));
      const left = Math.max(edge, Math.min(align === 'end' ? anchor.right - panel.width : anchor.left, innerWidth - panel.width - edge));
      const next = { '--pop-top': `${top}px`, '--pop-left': `${left}px`, '--pop-height': `${height}px` } as CSSProperties;
      setPosition(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next);
    };
    place();
    const observer = new ResizeObserver(place);
    if (panelRef.current) observer.observe(panelRef.current);
    const at = anchorRef?.current ?? rootRef.current;
    if (at) observer.observe(at);
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    window.visualViewport?.addEventListener('resize', place);
    const focusable = matchMedia('(hover: none)').matches ? '[role^="menuitem"]:not(:disabled), button:not(:disabled)' : 'input:not(:disabled), [role^="menuitem"]:not(:disabled), button:not(:disabled)';
    panelRef.current?.querySelector<HTMLElement>(focusable)?.focus({ preventScroll: true });
    return () => { observer.disconnect(); window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); window.visualViewport?.removeEventListener('resize', place); };
  }, [open, placement, align, anchorRef]);

  useEffect(() => {
    if (openSignal !== undefined) setOpen(openSignal > 0);
  }, [openSignal]);

  useEffect(() => {
    if (open && !wasOpen.current) onOpen?.();
    wasOpen.current = open;
  }, [open, onOpen]);

  useEffect(() => {
    if (!open && !preparing) return;
    // composedPath is captured at dispatch, so it still works if the clicked node is removed by the handler.
    const onDown = (e: MouseEvent | TouchEvent) => {
      // Portaled child menus are still inside their owning menus.
      if (e.composedPath().some(node => node instanceof HTMLElement && node.dataset.popoverParents?.split(' ').includes(panelId))) return;
      const at = anchorRef?.current ?? rootRef.current;
      if (anchorRef && at && e.composedPath().includes(at)) { close(); return; }
      if (at && !e.composedPath().includes(at) && (!panelRef.current || !e.composedPath().includes(panelRef.current))) close();
    };
    const onKey = (e: KeyboardEvent) => {
      // An open inline disclosure consumes Escape before its containing menu.
      if (e.key === 'Escape' && e.target instanceof Element && e.target.closest('[data-menu-disclosure="open"]')) return;
      if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) && e.target instanceof HTMLElement && !e.target.matches('input, textarea')) {
        const rows = [...(panelRef.current?.querySelectorAll<HTMLElement>('[role^="menuitem"]:not(:disabled), button:not(:disabled)') ?? [])].filter(el => el.getClientRects().length > 0);
        if (rows.length) { e.preventDefault(); const i = rows.indexOf(document.activeElement as HTMLElement); rows[e.key === 'Home' ? 0 : e.key === 'End' ? rows.length - 1 : (i + (e.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length]?.focus(); }
      }
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); (anchorRef?.current ?? rootRef.current?.querySelector('button'))?.focus({ preventScroll: true }); }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open, preparing, anchorRef]);
  const panel = <>
    <div id={panelId} ref={panelRef} data-popover-parents={parentPanels.join(' ')} style={position} className={`pop-panel anchored-pop align-${align} placement-${placement}${panelClassName ? ` ${panelClassName}` : ''}`} role="menu" aria-label={label}>
      <ParentPanels.Provider value={[...parentPanels, panelId]}>{open && children(close)}</ParentPanels.Provider>
    </div>
  </>;

  if (anchorRef) return open ? createPortal(panel, document.body) : null;
  return (
    <div className="pop" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        title={triggerTitle}
        aria-label={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-busy={preparing || triggerBusy || undefined}
        aria-controls={open ? panelId : undefined}
        disabled={disabled}
        onClick={() => open || preparing ? close() : show()}
      >
        {trigger}
      </button>
      {open && createPortal(panel, document.body)}
    </div>
  );
}
