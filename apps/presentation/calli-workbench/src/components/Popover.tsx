import { createPortal } from 'react-dom';
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type CSSProperties } from 'react';

interface Props {
  /** Accessible name of the panel. */
  label: string;
  triggerClassName?: string;
  triggerTitle?: string;
  triggerLabel?: string;
  panelClassName?: string;
  trigger: ReactNode;
  /** Which edge of the trigger the panel lines up with on wide screens. */
  align?: 'start' | 'end';
  placement?: 'above' | 'below';
  disabled?: boolean;
  /** Opens the panel each time this number changes, so something else can trigger it. */
  openSignal?: number;
  /** Called once when the panel opens, whether by click or by an open signal. */
  onOpen?: () => void;
  children: (close: () => void) => ReactNode;
}

/**
 * A button that opens a panel above or below it. On phones the panel becomes a bottom sheet.
 * Used instead of native <select> so menus look and behave the same everywhere.
 */
export function Popover({
  label,
  triggerClassName,
  triggerTitle,
  triggerLabel,
  panelClassName,
  trigger,
  align = 'start',
  placement = 'above',
  disabled,
  openSignal,
  onOpen,
  children,
}: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const wasOpen = useRef(false);

  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<CSSProperties>({});

  useLayoutEffect(() => {
    if (!open || placement !== 'below') return;
    const place = () => {
      if (!rootRef.current || !panelRef.current) return;
      const anchor = rootRef.current.getBoundingClientRect();
      const panel = panelRef.current.getBoundingClientRect();
      const top = anchor.bottom + 8;
      const left = Math.max(16, Math.min(align === 'end' ? anchor.right - panel.width : anchor.left, innerWidth - panel.width - 16));
      setPosition({ '--pop-top': `${top}px`, '--pop-left': `${left}px`, '--pop-height': `${Math.max(80, innerHeight - top - 16)}px` } as CSSProperties);
    };
    place();
    panelRef.current?.querySelector<HTMLElement>('[role^="menuitem"]')?.focus({ preventScroll: true });
    window.addEventListener('resize', place);
    window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [open, placement, align]);

  useEffect(() => {
    if (openSignal) setOpen(true);
  }, [openSignal]);

  useEffect(() => {
    if (open && !wasOpen.current) onOpen?.();
    wasOpen.current = open;
  }, [open, onOpen]);

  useEffect(() => {
    if (!open) return;
    // composedPath is captured at dispatch, so it still works if the clicked node is removed by the handler.
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (rootRef.current && !e.composedPath().includes(rootRef.current) && (!panelRef.current || !e.composedPath().includes(panelRef.current))) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); setOpen(false); rootRef.current?.querySelector('button')?.focus(); }
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey, true);
    };
  }, [open]);

  const close = () => setOpen(false);
  const panel = <>
    <div className="pop-scrim" onClick={close} aria-hidden="true" />
    <div ref={panelRef} style={placement === 'below' ? position : undefined} className={`pop-panel align-${align} placement-${placement}${panelClassName ? ` ${panelClassName}` : ''}`} role="menu" aria-label={label}>
      {open && children(close)}
    </div>
  </>;

  return (
    <div className="pop" ref={rootRef}>
      <button
        type="button"
        className={triggerClassName}
        title={triggerTitle}
        aria-label={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
      >
        {trigger}
      </button>
      {open && (placement === 'below' ? createPortal(panel, document.body) : panel)}
    </div>
  );
}
