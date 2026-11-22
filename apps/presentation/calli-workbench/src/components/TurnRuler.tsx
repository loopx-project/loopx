import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type RefObject } from 'react';
import type { TurnPreview } from '../timeline';
import { t } from '../i18n';

interface Props {
  threadId?: string;
  /** The scroll container holding the thread. A ref, because it mounts with the app. */
  scrollerRef: RefObject<HTMLDivElement | null>;
  /** Preview text per turn id, from the timeline. */
  previews: Map<string, TurnPreview>;
  turnIds: string[];
  onJump: (turnId: string) => void;
}

/** Fewer turns than this and the ruler is not worth its space. */
const MIN_TURNS = 3;
/** Ticks sit at one even pitch, and the ruler never grows past MAX_VISIBLE of them, like Codex:
 *  a long thread shows a window of its turns, which follows the reading and can be scrolled. */
const PITCH = 10;
const MAX_VISIBLE = 48;
/** Dash length by distance from the hovered tick (fisheye); beyond the list, the base length. */
const FISHEYE = [26, 20, 15, 11];
const BASE_W = 8;
const ACTIVE_W = 14;
/** How far a press may move and still be a click. */
const DRAG_SLOP = 4;
/** After the person scrolls or drags the ruler, it stops following the reading for a while. */
const HANDS_OFF_MS = 2500;

/**
 * The ruler along the thread's left edge: one tick per turn, the current turn longer. Hovering
 * enlarges the neighbours and shows what that turn was about; clicking jumps there. A long thread
 * shows a window of its turns: the wheel scrolls it, and dragging along it scrubs through the
 * turns, previewing each, past either end too, and jumps where it is let go. The ticks read the
 * lightweight full turn index, including turns whose bodies have not loaded yet.
 * Desktop only; CSS hides it on narrow screens.
 */
export function TurnRuler({ threadId, scrollerRef, previews, turnIds, onJump }: Props) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [activeIdx, setActiveIdx] = useState(-1);
  const [hoverIdx, setHoverIdx] = useState(-1);
  const [height, setHeight] = useState(0);
  const [first, setFirst] = useState(0);
  const [dragging, setDragging] = useState(false);
  const handsOff = useRef(0);

  const count = turnIds.length;
  const visible = Math.max(0, Math.min(count, MAX_VISIBLE, Math.floor((height || 600) / PITCH)));
  const maxFirst = Math.max(0, count - visible);
  const clampFirst = (n: number) => Math.max(0, Math.min(maxFirst, Math.round(n)));
  // The latest window for the listeners, which are not re-bound on every change.
  const view = useRef({ first, visible, maxFirst, count });
  view.current = { first, visible, maxFirst, count };

  useEffect(() => {
    const measure = () => {
      const h = rootRef.current?.clientHeight ?? 0;
      if (h > 0) setHeight(h);
    };
    measure();
    const observer = new ResizeObserver(measure);
    if (rootRef.current) observer.observe(rootRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    let raf = 0;
    const onScroll = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const scrollerTop = scroller.getBoundingClientRect().top;
        let current = -1;
        let last = -1;
        for (const row of scroller.querySelectorAll<HTMLElement>('[data-turn]')) {
          const owner = row.closest<HTMLElement>('[data-history-thread]')?.dataset.historyThread;
          if (owner && threadId && owner !== threadId) continue;
          const top = row.getBoundingClientRect().top - scrollerTop + scroller.scrollTop;
          const index = turnIds.indexOf(row.dataset.turn ?? '');
          if (top <= scroller.scrollTop + 80) current = index;
          last = index;
        }
        // At the bottom the last turn is the one being read, however short it is.
        if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) current = last;
        setActiveIdx(current);
      });
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    // Content streams in, images load and groups expand without a scroll; recheck now and then.
    const timer = setInterval(onScroll, 800);
    return () => {
      scroller.removeEventListener('scroll', onScroll);
      clearInterval(timer);
      cancelAnimationFrame(raf);
    };
  }, [threadId, scrollerRef, turnIds]);

  // Follow the reading: keep the current turn in the window, low in it, so what came before shows.
  useEffect(() => {
    if (dragging || Date.now() < handsOff.current || activeIdx < 0) return;
    const { first: f, visible: v } = view.current;
    if (activeIdx >= f && activeIdx < f + v) return;
    setFirst(clampFirst(activeIdx - Math.floor(v * 0.75)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIdx, visible, maxFirst, dragging]);
  useEffect(() => { setFirst(f => clampFirst(f)); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [maxFirst]);

  // The wheel scrolls the window, a tick per notch-ish; the page does not move under the ruler.
  useEffect(() => {
    const track = trackRef.current;
    if (!track) return;
    let rest = 0;
    const onWheel = (e: WheelEvent) => {
      if (view.current.maxFirst === 0) return;
      e.preventDefault();
      handsOff.current = Date.now() + HANDS_OFF_MS;
      rest += e.deltaY / PITCH / 2;
      const steps = Math.trunc(rest);
      if (!steps) return;
      rest -= steps;
      setFirst(f => Math.max(0, Math.min(view.current.maxFirst, f + steps)));
    };
    track.addEventListener('wheel', onWheel, { passive: false });
    return () => track.removeEventListener('wheel', onWheel);
  }, [count >= MIN_TURNS]);

  // Dragging: the tick under the pointer is previewed, past an end the window scrolls on (faster
  // the further out), and letting go jumps there. A press that hardly moves is a plain click.
  const drag = useRef<{ id: number; y: number; moved: boolean; overshoot: number; raf: number } | null>(null);
  const indexAt = (clientY: number) => {
    const top = trackRef.current?.getBoundingClientRect().top ?? 0;
    const { first: f, visible: v } = view.current;
    return Math.max(f, Math.min(f + v - 1, f + Math.floor((clientY - top) / PITCH)));
  };
  const edgeScroll = () => {
    const d = drag.current;
    if (!d || !d.overshoot) return;
    setFirst(f => Math.max(0, Math.min(view.current.maxFirst, f + Math.sign(d.overshoot) * Math.min(4, Math.ceil(Math.abs(d.overshoot) / 24)))));
    setHoverIdx(d.overshoot < 0 ? view.current.first : view.current.first + view.current.visible - 1);
    d.raf = window.setTimeout(edgeScroll, 60);
  };
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { id: e.pointerId, y: e.clientY, moved: false, overshoot: 0, raf: 0 };
    setHoverIdx(indexAt(e.clientY));
  };
  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) {
      if (!drag.current) setHoverIdx(indexAt(e.clientY));
      return;
    }
    if (!d.moved && Math.abs(e.clientY - d.y) > DRAG_SLOP) {
      d.moved = true;
      setDragging(true);
    }
    const rect = e.currentTarget.getBoundingClientRect();
    const overshoot = e.clientY < rect.top ? e.clientY - rect.top : e.clientY > rect.bottom ? e.clientY - rect.bottom : 0;
    const was = d.overshoot;
    d.overshoot = overshoot;
    if (overshoot && !was) edgeScroll();
    if (!overshoot) setHoverIdx(indexAt(e.clientY));
  };
  const endDrag = (e: ReactPointerEvent<HTMLDivElement>, cancelled = false) => {
    const d = drag.current;
    if (!d || d.id !== e.pointerId) return;
    window.clearTimeout(d.raf);
    drag.current = null;
    setDragging(false);
    handsOff.current = Date.now() + HANDS_OFF_MS;
    const target = d.overshoot ? hoverRef.current : indexAt(e.clientY);
    if (!cancelled && target >= 0 && turnIds[target]) onJump(turnIds[target]);
  };
  const hoverRef = useRef(-1);
  hoverRef.current = hoverIdx;

  const previewOf = useMemo(() => (idx: number): TurnPreview | null => previews.get(turnIds[idx] ?? '') ?? null, [previews, turnIds]);

  // The window is centred in the rail; the popover follows the hovered tick.
  const start = Math.max(0, (height - PITCH * visible) / 2);
  const offsetOf = (i: number) => (i - first) * PITCH;
  const widthOf = (i: number) => {
    const d = hoverIdx >= 0 ? Math.abs(i - hoverIdx) : Infinity;
    if (d < FISHEYE.length) return FISHEYE[d];
    return i === activeIdx ? ACTIVE_W : BASE_W;
  };

  // The rail itself must render (empty) so it can be measured before ticks appear.
  if (count < MIN_TURNS) return <div className="turn-ruler" ref={rootRef} style={{ pointerEvents: 'none' }} aria-hidden />;

  const shown = turnIds.slice(first, first + visible);
  return (
    <div className="turn-ruler" ref={rootRef} onMouseLeave={() => { if (!drag.current) setHoverIdx(-1); }}>
      <div
        ref={trackRef}
        className={`ruler-track${first > 0 ? ' more-above' : ''}${first + visible < count ? ' more-below' : ''}${dragging ? ' dragging' : ''}`}
        style={{ top: start, height: PITCH * visible }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={e => endDrag(e)}
        onPointerCancel={e => endDrag(e, true)}
      >
        {shown.map((turnId, k) => {
          const i = first + k;
          return (
            <button
              key={turnId || i}
              className={`tick${i === activeIdx ? ' active' : ''}${i === hoverIdx ? ' hover' : ''}`}
              style={{ top: offsetOf(i), height: PITCH, ['--w' as string]: `${widthOf(i)}px` }}
              aria-label={t('ruler.jump', i + 1)}
              tabIndex={0}
              // The pointer is handled by the track; this is the keyboard's way in.
              onClick={e => { if (e.detail === 0) onJump(turnId); }}
              onFocus={() => { setHoverIdx(i); }}
              onBlur={() => setHoverIdx(-1)}
            />
          );
        })}
      </div>
      {hoverIdx >= first && hoverIdx < first + visible && (
        <div className="ruler-pop" style={{ top: start + offsetOf(hoverIdx) + PITCH / 2 }}>
          {previewOf(hoverIdx) ? <>
            <div className="ruler-pop-q">{previewOf(hoverIdx)!.question.slice(0, 90) || t('ruler.image')}</div>
            {previewOf(hoverIdx)!.answer && <div className="ruler-pop-a">{previewOf(hoverIdx)!.answer.slice(0, 110)}</div>}
          </> : <div className="ruler-unloaded">
            <span>{t('ruler.unloaded')}</span>
            <i /><i /><i />
          </div>}
          {count > visible && <div className="ruler-pop-n">{t('ruler.position', hoverIdx + 1, count)}</div>}
        </div>
      )}
    </div>
  );
}
