import { useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type TouchEvent } from 'react';
import { APPEARANCE_KEY, parseAppearance, resolveTheme, type Appearance, type Theme } from './appearance';

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const onChange = () => setMatches(mq.matches);
    onChange();
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  return matches;
}

/** True on phone-width screens, where menus are bottom sheets instead of floating panels. */
export function useIsNarrow(): boolean {
  return useMediaQuery('(max-width: 640px)');
}

/** True on touch screens, where typing brings up an on-screen keyboard over the bottom of the page. */
export function useIsTouch(): boolean {
  return useMediaQuery('(hover: none)');
}

/** True when the installed app draws into the window's title bar (window-controls-overlay). */
export function useInTitlebar(): boolean {
  return useMediaQuery('(display-mode: window-controls-overlay)');
}

/** True where the thread list is a layer under the conversation, not a column beside it. The stylesheet draws the line at the same width. */
export function useIsCompact(): boolean {
  return !useMediaQuery('(min-width: 900px)');
}

/** How close to the left edge a swipe has to start to open the thread list. */
const EDGE_PX = 28;
/** How far sideways a finger travels before the swipe counts. */
const SWIPE_PX = 56;
/** How far it may wander up or down meanwhile; further than this and it is a scroll. */
const SWIPE_DRIFT_PX = 40;

/**
 * Swipes that move the phone's thread list: in from the left edge opens it, leftwards closes it.
 * Only on the conversation and the list themselves, never on a sheet or dialog lying over them.
 */
export function useListSwipe(enabled: boolean, open: boolean, setOpen: (open: boolean) => void) {
  useEffect(() => {
    if (!enabled) return;
    let start: { x: number; y: number } | null = null;
    const onStart = (e: globalThis.TouchEvent) => {
      start = null;
      const t = e.touches[0];
      const el = e.target instanceof Element ? e.target : null;
      if (e.touches.length !== 1 || !el?.closest('.main, .sidebar') || el.closest('.pop-panel, .pop-scrim, .command-menu, .lightbox')) return;
      if (open || t.clientX <= EDGE_PX) start = { x: t.clientX, y: t.clientY };
    };
    const onMove = (e: globalThis.TouchEvent) => {
      const t = e.touches[0];
      if (!start || !t) return;
      const dx = t.clientX - start.x;
      if (Math.abs(t.clientY - start.y) > SWIPE_DRIFT_PX) start = null;
      else if (open ? dx < -SWIPE_PX : dx > SWIPE_PX) {
        start = null;
        setOpen(!open);
      }
    };
    const onEnd = () => {
      start = null;
    };
    document.addEventListener('touchstart', onStart, { passive: true });
    document.addEventListener('touchmove', onMove, { passive: true });
    document.addEventListener('touchend', onEnd, { passive: true });
    document.addEventListener('touchcancel', onEnd, { passive: true });
    return () => {
      document.removeEventListener('touchstart', onStart);
      document.removeEventListener('touchmove', onMove);
      document.removeEventListener('touchend', onEnd);
      document.removeEventListener('touchcancel', onEnd);
    };
    // setOpen is a state setter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, open]);
}

/** How long a finger rests on something before it counts as a long press. */
const LONG_PRESS_MS = 450;
/** How far it may drift meanwhile; further than this and it is a scroll. */
const LONG_PRESS_SLOP = 10;

/**
 * Long press, for touch screens: there is no right click there, and iOS sends no contextmenu
 * event. Returns a function that makes the handlers for one target. The press that opened
 * something is not also a tap, so the click that would follow it is swallowed.
 */
export function useLongPress<T>(onLongPress: (target: T, x: number, y: number) => void) {
  const press = useRef({ timer: undefined as ReturnType<typeof setTimeout> | undefined, x: 0, y: 0, fired: false });
  const cancel = () => {
    clearTimeout(press.current.timer);
    press.current.timer = undefined;
  };
  useEffect(() => cancel, []);

  return (target: T) => ({
    onTouchStart: (e: TouchEvent) => {
      cancel();
      if (e.touches.length !== 1) return;
      const { clientX: x, clientY: y } = e.touches[0];
      press.current = {
        x,
        y,
        fired: false,
        timer: setTimeout(() => {
          press.current.fired = true;
          onLongPress(target, x, y);
        }, LONG_PRESS_MS),
      };
    },
    onTouchMove: (e: TouchEvent) => {
      const t = e.touches[0];
      if (t && Math.hypot(t.clientX - press.current.x, t.clientY - press.current.y) > LONG_PRESS_SLOP) cancel();
    },
    onTouchEnd: (e: TouchEvent) => {
      cancel();
      if (!press.current.fired) return;
      e.preventDefault(); // no click for the finger that is being lifted
      setTimeout(() => (press.current.fired = false), 400);
    },
    onTouchCancel: cancel,
    onClickCapture: (e: MouseEvent) => {
      if (!press.current.fired) return;
      press.current.fired = false;
      e.preventDefault();
      e.stopPropagation();
    },
  });
}

/** A set of string keys kept in localStorage, for things like which sidebar groups are collapsed. */
export function useStoredSet(key: string): [Set<string>, (value: string) => void] {
  const [set, setSet] = useState<Set<string>>(() => {
    try {
      const raw = localStorage.getItem(key);
      return new Set(raw ? (JSON.parse(raw) as string[]) : []);
    } catch {
      return new Set();
    }
  });
  const toggle = (value: string) => {
    setSet((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      try {
        localStorage.setItem(key, JSON.stringify([...next]));
      } catch {
        // Storage unavailable: the choice lasts until the page reloads.
      }
      return next;
    });
  };
  return [set, toggle];
}

/** A switch kept in localStorage, for a choice about how this device shows things. */
export function useStoredFlag(key: string): [boolean, (on: boolean) => void] {
  const [on, setOn] = useState(() => {
    try {
      return localStorage.getItem(key) === '1';
    } catch {
      return false;
    }
  });
  const save = (next: boolean) => {
    setOn(next);
    try {
      if (next) localStorage.setItem(key, '1');
      else localStorage.removeItem(key);
    } catch {
      // Storage unavailable: the choice lasts until the page reloads.
    }
  };
  return [on, save];
}

/**
 * The look chosen on this device, kept in localStorage and put on the page as two attributes of
 * <html>: `data-theme` (light or dark, already resolved against the system) and `data-palette`.
 * The stylesheet does the rest. Returns the resolved theme too, for what has to follow it.
 */
export function useAppearance(storageKey = APPEARANCE_KEY, forced?: Appearance): [Appearance, (next: Appearance) => void, Theme] {
  const [saved, setAppearance] = useState<Appearance>(() => {
    try {
      return parseAppearance(localStorage.getItem(storageKey));
    } catch {
      return parseAppearance(null);
    }
  });
  const appearance = forced ?? saved;
  const theme = resolveTheme(appearance.mode, useMediaQuery('(prefers-color-scheme: dark)'));

  useLayoutEffect(() => {
    const root = document.documentElement;
    root.setAttribute('data-theme', theme);
    root.setAttribute('data-palette', appearance.palette);
  }, [theme, appearance.palette]);

  const save = (next: Appearance) => {
    setAppearance(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      // Storage unavailable: the choice lasts until the page reloads.
    }
  };
  return [appearance, save, theme];
}
