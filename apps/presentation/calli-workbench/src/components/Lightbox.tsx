import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type PointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { savePicture, savePictureAsPng } from '../images';
import { MAX_ZOOM, REST, fitted, panBy, zoomAt, type View } from '../zoom';
import { IconClose, IconDownload, IconMinus, IconPlus } from './icons';
import { t } from '../i18n';

interface Props {
  url?: string;
  /** Trusted, themed Mermaid output. Mounted only while the viewer is open. */
  svg?: string;
  /** What the picture is called, for saving it. A path will do: its last part is used. */
  name?: string;
  onClose: () => void;
}

/** How far a pointer travels before it is dragging rather than tapping. */
const TAP_SLOP = 6;
/** One press of a zoom button, or of + and -. */
const STEP = 1.5;

/**
 * A picture at full size over everything. A tap closes it, as long as it is not zoomed in. The
 * wheel, a pinch or the buttons zoom towards the pointer; zoomed in, a drag moves the picture.
 * Diagrams stay open on single taps so that double taps can toggle between fit and detail.
 */
export function Lightbox({ url = '', svg, name, onClose }: Props) {
  const [view, setView] = useState<View>(REST);
  /** Why the picture could not be saved, shown for a moment. */
  const [note, setNote] = useState<string | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const drawingRef = useRef<HTMLDivElement>(null);
  const natural = useRef({ width: 0, height: 0 });
  const isSvg = url.startsWith('data:image/svg+xml');

  // The size the picture rests at, worked out from its shape and the room above the bar. Left to
  // the stylesheet, a drawing that names no size would come out small, or taller than the screen.
  const [rest, setRest] = useState<{ width: number; height: number } | null>(null);
  const actualScale = rest?.width ? Math.max(1, natural.current.width / rest.width) : 1;
  const maximum = svg ? Math.max(MAX_ZOOM, actualScale * 2) : MAX_ZOOM;
  const fit = () => {
    const img = imgRef.current;
    const stage = stageRef.current;
    const vector = drawingRef.current?.querySelector('svg')?.viewBox.baseVal;
    const size = vector ? { width: vector.width, height: vector.height } : { width: img?.naturalWidth ?? 0, height: img?.naturalHeight ?? 0 };
    if (!stage || !size.width) return;
    natural.current = size;
    setRest(fitted(size, { width: stage.clientWidth, height: stage.clientHeight }, isSvg));
  };
  const fitRef = useRef(fit);
  fitRef.current = fit;
  useLayoutEffect(() => { fitRef.current(); }, [svg]);
  useEffect(() => {
    const onResize = () => {
      fitRef.current();
      setView(REST);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  /** The picture's size at rest, and where its centre rests on screen. */
  const frame = () => {
    const img = drawingRef.current ?? imgRef.current;
    const stage = stageRef.current?.getBoundingClientRect();
    if (!img || !stage) return null;
    return { size: { width: img.offsetWidth, height: img.offsetHeight }, cx: stage.left + stage.width / 2, cy: stage.top + stage.height / 2 };
  };
  /** Zooms towards a point on screen, or the middle of the picture's place when none is given. */
  const zoom = (factor: number, at?: { x: number; y: number }) => {
    const f = frame();
    if (f) setView((v) => zoomAt(v, factor, at ? { x: at.x - f.cx, y: at.y - f.cy } : { x: 0, y: 0 }, f.size, maximum));
  };
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onClose(); }
      else if (e.key === 'Tab') {
        const buttons = [...(rootRef.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? [])];
        const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
        e.preventDefault();
        buttons[(index + (e.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus();
      }
      else if (e.key === '+' || e.key === '=') zoomRef.current(STEP);
      else if (e.key === '-') zoomRef.current(1 / STEP);
      else if (e.key === '0') setView(REST);
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  // Not a React handler: the page must not scroll or zoom under the picture, and React's wheel
  // listeners are passive.
  const rootRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    rootRef.current?.querySelector<HTMLButtonElement>('.lightbox-close')?.focus();
    return () => { document.body.style.overflow = overflow; previous?.focus({ preventScroll: true }); };
  }, []);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      // A trackpad pinch arrives as a wheel with ctrl held, in much smaller steps than a mouse wheel's.
      zoomRef.current(Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), { x: e.clientX, y: e.clientY });
    };
    root.addEventListener('wheel', onWheel, { passive: false });
    return () => root.removeEventListener('wheel', onWheel);
  }, []);

  useEffect(() => {
    if (!note) return;
    const timer = setTimeout(() => setNote(null), 2400);
    return () => clearTimeout(timer);
  }, [note]);

  // Fingers and the mouse alike: one pointer drags, two pinch.
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef({ moved: false, onPicture: false });
  const lastTap = useRef<{ time: number; x: number; y: number } | null>(null);
  const onControls = (target: EventTarget) => target instanceof Element && target.closest('.lightbox-bar, .lightbox-close') !== null;
  const spread = () => {
    const [a, b] = [...pointers.current.values()];
    return { distance: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
  };

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    if (onControls(e.target) || (e.pointerType === 'mouse' && e.button !== 0)) return;
    if (pointers.current.size === 0) gesture.current = { moved: false, onPicture: e.target === imgRef.current || !!drawingRef.current?.contains(e.target as Node) };
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size > 1) { gesture.current.moved = true; lastTap.current = null; }
    e.currentTarget.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const last = pointers.current.get(e.pointerId);
    if (!last) return;
    const before = pointers.current.size === 2 ? spread() : null;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const f = frame();
    if (!f) return;
    if (before) {
      const now = spread();
      gesture.current.moved = true;
      if (before.distance > 0) {
        setView((v) => panBy(zoomAt(v, now.distance / before.distance, { x: before.x - f.cx, y: before.y - f.cy }, f.size, maximum), now.x - before.x, now.y - before.y, f.size));
      }
    } else if (pointers.current.size === 1) {
      const dx = e.clientX - last.x;
      const dy = e.clientY - last.y;
      if (!gesture.current.moved && Math.hypot(dx, dy) < TAP_SLOP) {
        // Still within a tap: remember where the pointer started, so the slop is measured from there.
        pointers.current.set(e.pointerId, last);
        return;
      }
      gesture.current.moved = true;
      setView((v) => panBy(v, dx, dy, f.size));
    }
  };
  const onPointerEnd = (e: PointerEvent<HTMLDivElement>) => {
    if (svg && pointers.current.has(e.pointerId) && !gesture.current.moved && e.type !== 'pointercancel') {
      const last = lastTap.current;
      if (last && Date.now() - last.time < 350 && Math.hypot(last.x - e.clientX, last.y - e.clientY) < 24) {
        if (view.scale > 1) setView(REST);
        else zoom(Math.max(2, actualScale), { x: e.clientX, y: e.clientY });
        lastTap.current = null;
      } else lastTap.current = { time: Date.now(), x: e.clientX, y: e.clientY };
    }
    if (e.type === 'pointercancel') { gesture.current.moved = true; lastTap.current = null; }
    pointers.current.delete(e.pointerId);
  };
  // The click that ends a press, not the release itself: closing on the release would leave the
  // click to land on whatever lies under the picture. A tap closes, unless it was a drag or landed
  // on a picture that is zoomed in.
  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    e.stopPropagation();
    if (svg || onControls(e.target)) return;
    if (!gesture.current.moved && !(gesture.current.onPicture && view.scale > 1)) onClose();
  };

  const save = (how: (url: string, name?: string) => Promise<void>) => () => {
    how(url, name).catch(() => setNote(t('lightbox.saveFailed')));
  };

  const zoomed = view.scale > 1;
  return createPortal(
    <div
      ref={rootRef}
      className={`lightbox${svg ? ' lightbox-diagram' : ''}${zoomed ? ' zoomed' : ''}`}
      role="dialog"
      aria-modal="true"
      aria-label={t(svg ? 'view.mermaid' : 'lightbox.image')}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerEnd}
      onPointerCancel={onPointerEnd}
      onClick={onClick}
    >
      <div className="lightbox-stage" ref={stageRef}>
        {svg ? <div ref={drawingRef} className="lightbox-vector"
          style={{ ...(rest ? { width: rest.width, height: rest.height } : { visibility: 'hidden' }), transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` }}
          dangerouslySetInnerHTML={{ __html: svg }} /> : <img
          ref={imgRef}
          src={url}
          alt={t('lightbox.alt')}
          draggable={false}
          onLoad={fit}
          style={{
            ...(rest ? { width: rest.width, height: rest.height } : { visibility: 'hidden' }),
            ...(zoomed ? { transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})` } : {}),
          }}
        />}
      </div>
      <div className="lightbox-bar" role="toolbar" aria-label={t('lightbox.toolbar')}>
        {note && (
          <span className="lightbox-note" role="status">
            {note}
          </span>
        )}
        <button type="button" aria-label={t('lightbox.zoomOut')} title={t('lightbox.zoomOutKey')} disabled={!zoomed} onClick={() => zoom(1 / STEP)}>
          <IconMinus size={17} />
        </button>
        {svg ? <>
          <button type="button" onClick={() => setView(REST)}>{t('lightbox.fit')}</button>
          <button type="button" aria-label={t('lightbox.actual')} onClick={() => setView({ scale: actualScale, x: 0, y: 0 })}>{t('lightbox.percent', 100)}</button>
        </> : <button type="button" className="lightbox-scale" aria-label={t('lightbox.reset')} title={t('lightbox.resetKey')} disabled={!zoomed} onClick={() => setView(REST)}>
          {Math.round(view.scale * 100)}%
        </button>}
        <button type="button" aria-label={t('lightbox.zoomIn')} title={t('lightbox.zoomInKey')} disabled={view.scale >= maximum} onClick={() => zoom(STEP)}>
          <IconPlus size={17} />
        </button>
        {!svg && <>
        <span className="lightbox-sep" aria-hidden="true" />
        {isSvg ? (
          <>
            <button type="button" className="lightbox-save" aria-label={t('lightbox.svg')} title={t('lightbox.svg')} onClick={save(savePicture)}>
              <IconDownload size={16} /> SVG
            </button>
            <button type="button" className="lightbox-save" aria-label={t('lightbox.png')} title={t('lightbox.png')} onClick={save(savePictureAsPng)}>
              <IconDownload size={16} /> PNG
            </button>
          </>
        ) : (
          <button type="button" aria-label={t('common.download')} title={t('common.download')} onClick={save(savePicture)}>
            <IconDownload size={17} />
          </button>
        )}
        </>}
      </div>
      <button type="button" className="lightbox-close" aria-label={t('common.close')} onClick={onClose}>
        <IconClose size={18} />
      </button>
    </div>, document.body,
  );
}
