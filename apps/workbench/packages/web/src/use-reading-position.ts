import { useLayoutEffect, useRef, type MutableRefObject, type RefObject } from 'react';
import type { StoredEvent } from './timeline';
import { captureReadingAnchor, restoreReadingAnchor, type ReadingAnchor } from './scroll-anchor';

export function useReadingPosition(scroller: RefObject<HTMLElement | null>, pinned: MutableRefObject<boolean>, threadId: string | null, events: StoredEvent[], resetKey: string, paused: boolean) {
  const anchor = useRef<ReadingAnchor | null>(null);
  const restored = useRef<number | null>(null);
  const pending = useRef<{ threadId: string | null; seq: number; anchor: ReadingAnchor | null; fromBottom: number } | null>(null);
  const pausedNow = useRef(paused);
  pausedNow.current = paused;

  useLayoutEffect(() => {
    anchor.current = null; pending.current = null; restored.current = null;
  }, [threadId, resetKey]);

  const capture = () => {
    const el = scroller.current;
    anchor.current = el && !pinned.current && !pausedNow.current ? captureReadingAnchor(el) : null;
  };
  const apply = (el: HTMLElement, position = anchor.current) => {
    const delta = restoreReadingAnchor(el, position);
    if (delta !== null && Math.abs(delta) > 0.5) restored.current = el.scrollTop;
    capture();
    return delta;
  };
  const restore = () => {
    const el = scroller.current;
    if (el && !pinned.current && !pausedNow.current && !pending.current) apply(el);
  };
  const preparePrepend = (seq: number | undefined) => {
    const el = scroller.current;
    if (el && seq !== undefined && !pausedNow.current) pending.current = {
      threadId, seq, anchor: captureReadingAnchor(el), fromBottom: el.scrollHeight - el.scrollTop,
    };
  };

  // Compensate only after the older page commits to the DOM so rAF never reads a stale height.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el || pinned.current || paused) { anchor.current = null; pending.current = null; return; }
    const page = pending.current;
    if (page) {
      if (page.threadId !== threadId) { pending.current = null; return; }
      if (!events.some(event => event.seq === page.seq)) return;
      if (apply(el, page.anchor) === null) {
        el.scrollTop = el.scrollHeight - page.fromBottom;
        restored.current = el.scrollTop;
        capture();
      }
      pending.current = null;
    } else restore();
  }, [events, threadId, resetKey, paused]); // eslint-disable-line react-hooks/exhaustive-deps

  const didRestore = () => {
    const el = scroller.current;
    if (!el) return false;
    const top = restored.current;
    restored.current = null;
    if (top !== null && Math.abs(el.scrollTop - top) <= 1) return true;
    // Content shrinking can also change scrollTop; programmatic restoration must not count as user scrolling.
    if (!pinned.current && !pausedNow.current && anchor.current && anchor.current.height !== el.scrollHeight && !pending.current) {
      restore();
      return true;
    }
    return false;
  };

  return { capture, restore, preparePrepend, didRestore };
}
