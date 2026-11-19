import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useIsNarrow } from '../hooks';
import { IconCheck, IconChevron } from './icons';

export interface MenuItem {
  id: string;
  label: string;
  icon?: (p: { size?: number }) => JSX.Element;
  /** Red text, for actions that destroy something. */
  danger?: boolean;
  checked?: boolean;
  disabled?: boolean;
  /** Runs when picked. Ignored when the item opens a submenu. */
  onSelect?: () => void;
  submenu?: MenuEntry[];
}

export type MenuEntry = MenuItem | 'separator';

interface Props {
  /** Accessible name. */
  label: string;
  /** Where the menu opens, in viewport coordinates. */
  x: number;
  y: number;
  /** Which corner of the panel sits at that point. A menu opened from the bottom of the screen grows upwards. */
  corner?: 'top-left' | 'bottom-right';
  items: MenuEntry[];
  /** Optional account summary, mounted only while the root menu is visible. */
  header?: ReactNode;
  /** Tighter rows, for a menu that carries many first-level actions and must stay short. */
  dense?: boolean;
  onClose: () => void;
}

const EDGE = 8;

/** How long after the finger that opened a menu lifts before the menu takes taps. */
const ARM_DELAY_MS = 400;

/** Fingers on the screen right now. A menu opened by a long press appears under one of them. */
let fingers = 0;
if (typeof document !== 'undefined') {
  const count = (e: TouchEvent) => { fingers = e.touches.length; };
  for (const type of ['touchstart', 'touchend', 'touchcancel'] as const) document.addEventListener(type, count, { capture: true, passive: true });
}

/**
 * A menu with icons, separators and nested groups.
 * Wide screens keep the root beside one branch; deeper groups replace the branch's contents.
 * Phones: the same anchored surface, with a submenu replacing its parent list.
 */
export function Menu({ label, x, y, corner = 'top-left', items, header, dense, onClose }: Props) {
  const narrow = useIsNarrow();
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const branchRef = useRef<HTMLDivElement>(null);
  const [openPath, setOpenPath] = useState<string[]>([]);
  // A menu opened under a finger (a long press) ignores that finger: lifting it must not pick
  // the row that slid in beneath it, nor close the menu. Safari sends the lifted finger's mouse
  // press and click a moment later, wherever it now is, so those are swallowed too, and the menu
  // listens only a little after every finger is up.
  const [armed, setArmed] = useState(() => fingers === 0);
  useEffect(() => {
    if (armed) return;
    let timer: number | undefined;
    const swallow = (e: Event) => {
      e.preventDefault();
      e.stopImmediatePropagation();
    };
    const up = (e: TouchEvent) => {
      if (e.touches.length) return;
      if (e.cancelable) e.preventDefault();
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setArmed(true), ARM_DELAY_MS);
    };
    const touch = ['touchend', 'touchcancel'] as const;
    const mouse = ['mousedown', 'mouseup', 'click'] as const;
    for (const type of touch) document.addEventListener(type, up, true);
    for (const type of mouse) document.addEventListener(type, swallow, true);
    return () => {
      window.clearTimeout(timer);
      for (const type of touch) document.removeEventListener(type, up, true);
      for (const type of mouse) document.removeEventListener(type, swallow, true);
    };
  }, [armed]);

  const branches: MenuItem[] = [];
  let entries = items;
  for (const id of openPath) {
    const entry = entries.find((entry): entry is MenuItem => entry !== 'separator' && entry.id === id && Boolean(entry.submenu));
    if (!entry) break;
    branches.push(entry);
    entries = entry.submenu!;
  }
  const branch = branches.at(-1);
  const focusBranch = () => requestAnimationFrame(() => (narrow ? panelRef : branchRef).current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus({ preventScroll: true }));
  const back = () => {
    const id = branches.at(-1)?.id;
    setOpenPath(path => path.slice(0, -1));
    requestAnimationFrame(() => [...(rootRef.current?.querySelectorAll<HTMLButtonElement>('button[data-menu-id]') ?? [])].find(button => button.dataset.menuId === id)?.focus({ preventScroll: true }));
  };

  // Both surfaces stay inside the visible viewport, including above a phone keyboard.
  useLayoutEffect(() => {
    const place = () => {
      const el = panelRef.current;
      if (!el) return;
      // Measure layout dimensions: the opening animation must not feed back into placement.
      const rect = { width: el.offsetWidth, height: el.offsetHeight };
      const viewport = window.visualViewport;
      const minY = (viewport?.offsetTop ?? 0) + EDGE;
      const bottom = (viewport?.offsetTop ?? 0) + (viewport?.height ?? innerHeight);
      const [wantLeft, wantTop] = corner === 'bottom-right' ? [x - rect.width, y - rect.height] : [x, y];
      const left = Math.max(EDGE, Math.min(wantLeft, innerWidth - rect.width - EDGE));
      const top = Math.max(minY, Math.min(wantTop, bottom - rect.height - EDGE));
      const child = branchRef.current;
      const childLeft = child && left + rect.width + child.offsetWidth + EDGE * 2 <= innerWidth ? left + rect.width + EDGE : left - (child?.offsetWidth ?? 0) - EDGE;
      const subLeft = Math.max(EDGE, Math.min(childLeft, innerWidth - (child?.offsetWidth ?? 0) - EDGE));
      const subTop = Math.max(minY, Math.min(top, bottom - (child?.offsetHeight ?? 0) - EDGE));
      // Placement is DOM geometry, not React state. Recreated items and queued observers
      // must not schedule another synchronous render while a branch is being removed.
      Object.assign(el.style, { left: `${left}px`, top: `${top}px`, visibility: 'visible' });
      if (child) Object.assign(child.style, { left: `${subLeft}px`, top: `${subTop}px`, visibility: 'visible' });
    };
    place();
    const observer = new ResizeObserver(place);
    if (panelRef.current) observer.observe(panelRef.current);
    if (branchRef.current) observer.observe(branchRef.current);
    window.addEventListener('resize', place);
    window.visualViewport?.addEventListener('resize', place);
    return () => { observer.disconnect(); window.removeEventListener('resize', place); window.visualViewport?.removeEventListener('resize', place); };
  }, [x, y, corner, narrow, items, openPath]);

  useEffect(() => {
    const onDown = (event: MouseEvent | TouchEvent) => {
      if (rootRef.current && !event.composedPath().includes(rootRef.current)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault(); event.stopImmediatePropagation();
      if (openPath.length) back(); else onClose();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('touchstart', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('touchstart', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose, openPath]);

  useEffect(() => {
    if (!armed) return;
    const previous = document.activeElement as HTMLElement | null;
    panelRef.current?.querySelector<HTMLElement>('button:not(:disabled)')?.focus({ preventScroll: true });
    return () => { if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, [armed]);

  useEffect(() => {
    if (!armed) return;
    const onKey = (event: KeyboardEvent) => {
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End', 'ArrowRight', 'ArrowLeft'].includes(event.key)) return;
      const current = document.activeElement as HTMLElement;
      const menu = current?.closest('[role="menu"]');
      if (!menu || !rootRef.current?.contains(menu)) return;
      const rows = [...menu.querySelectorAll<HTMLElement>('button:not(:disabled)')];
      if (!rows.length) return;
      event.preventDefault(); event.stopPropagation();
      if (event.key === 'ArrowRight') { if (current.getAttribute('aria-haspopup') === 'menu') current.click(); return; }
      if (event.key === 'ArrowLeft') { if (openPath.length) back(); return; }
      const index = rows.indexOf(current);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
      rows[next]?.focus();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [armed, openPath]);

  const pick = (item: MenuItem, inBranch: boolean) => {
    if (item.disabled) return;
    if (item.submenu) {
      setOpenPath(inBranch ? [...branches.map(entry => entry.id), item.id] : [item.id]);
      focusBranch();
      return;
    }
    onClose();
    item.onSelect?.();
  };

  const list = (rows: MenuEntry[], inBranch: boolean) => rows.map((item, index) => {
    if (item === 'separator') return <div key={`sep-${index}`} className="menu-sep" role="separator" />;
    const Icon = item.icon;
    const expanded = branches.some(entry => entry.id === item.id);
    return <div key={item.id} className="menu-cell"><button
      type="button"
      role={item.checked !== undefined ? 'menuitemradio' : 'menuitem'}
      aria-checked={item.checked}
      aria-haspopup={item.submenu ? 'menu' : undefined}
      aria-expanded={item.submenu ? expanded : undefined}
      data-menu-id={item.id}
      className={`menu-row${item.danger ? ' danger' : ''}${expanded ? ' open' : ''}`}
      disabled={item.disabled}
      onClick={() => pick(item, inBranch)}
      onMouseEnter={() => {
        if (narrow || inBranch) return;
        setOpenPath(path => item.submenu && !item.disabled ? path[0] === item.id ? path : [item.id] : path.length ? [] : path);
      }}
    >
      <span className="menu-icon">{Icon ? <Icon size={17} /> : null}</span>
      <span className="menu-label">{item.label}</span>
      {item.checked && <span className="menu-trail"><IconCheck size={15} /></span>}
      {item.submenu && <span className="menu-trail"><IconChevron size={14} /></span>}
    </button></div>;
  });
  const branchContent = branch && <><button type="button" role="menuitem" className="menu-back" onClick={back}><span className="menu-back-icon"><IconChevron size={14} /></span>{branch.label}</button>{list(entries, true)}</>;
  return createPortal(
    <div ref={rootRef} className={armed ? undefined : 'sheet-unarmed'}>
      <div ref={panelRef} className={`menu-panel menu-float${narrow ? ' menu-touch' : ''}${header ? ' menu-with-header' : ''}${dense ? ' menu-dense' : ''}`} role="menu" aria-label={narrow && branch ? branch.label : label}
        style={{ left: x, top: y, visibility: 'hidden' }}>
        {narrow && branch ? branchContent : <>{header}{list(items, false)}</>}
      </div>
      {!narrow && branch && <div ref={branchRef} className="menu-panel menu-sub menu-branch" role="menu" aria-label={branch.label} style={{ left: x, top: y, visibility: 'hidden' }}>{branchContent}</div>}
    </div>, document.body,
  );
}
