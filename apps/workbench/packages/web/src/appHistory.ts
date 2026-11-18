import { useEffect, type MutableRefObject } from 'react';

/**
 * The hub's own part of the browser history is always two entries: a floor, and the entry the app
 * lives on. Pages, settings levels and the thread list are app state, not history, so the trail
 * cannot pile up or leave old pages behind for a back swipe to land on. A back swipe (or the
 * browser's back button) that reaches the floor is the hub's own "back": it runs what the top-left
 * button would, and the app entry is put back on top as soon as the page has shown the result. Panels over the page (files, agents,
 * trace) still push their own entries above the app entry, as before.
 */
const FLOOR = 'rahFloor';
const APP = 'rahApp';

/** How long after a touch swipe already did the job a landing on the floor is the same gesture. */
const SAME_GESTURE_MS = 800;
/** The hashchange that comes with a landing on the floor arrives after the bounce; it is no navigation. */
const BOUNCE_MS = 300;

let lastEdgeSwipe = 0;
let bouncedAt = 0;

/** The app's own edge swipe moved the thread list; iOS fires the history back for the same finger too. */
export const noteEdgeSwipe = () => { lastEdgeSwipe = Date.now(); };
/** True while the address change belongs to a bounce off the floor. */
export const isBounce = () => Date.now() - bouncedAt < BOUNCE_MS;

const marked = (key: string) => Boolean((window.history.state as Record<string, unknown> | null)?.[key]);
const here = () => window.location.pathname + window.location.search + window.location.hash;

/** Runs after the page has painted what it shows: iOS keeps a picture of an entry the moment it is left. */
const afterPaint = (run: () => void) => requestAnimationFrame(() => requestAnimationFrame(run));

const pushApp = (href: string) => window.history.pushState({ [APP]: true }, '', href);

/**
 * Puts the floor under the entry the app was opened on, once per tab. The app entry goes on top
 * only after the first paint: iOS takes the picture it shows under a back swipe when the entry is
 * left, and a floor left before anything was drawn is a white screen.
 */
function ensureFloor() {
  if (marked(APP)) return;
  if (!marked(FLOOR)) window.history.replaceState({ [FLOOR]: true }, '', here());
  afterPaint(() => { if (marked(FLOOR) && !marked(APP)) pushApp(here()); });
}

/**
 * `back` is the hub's "back": it returns true when it did something. When it has nothing left to
 * undo (a desktop at the top of a page) the browser is let through to go on back.
 * `appHref` is kept up to date by the address effect: it is where the app entry goes back to.
 */
export function useAppHistory(back: MutableRefObject<() => boolean>, appHref: MutableRefObject<string>) {
  useEffect(() => {
    appHref.current = here();
    ensureFloor();
    const onPop = () => {
      if (marked(FLOOR)) {
        const sameGesture = Date.now() - lastEdgeSwipe < SAME_GESTURE_MS;
        if (!sameGesture && !back.current()) { window.history.go(-1); return; }
        bouncedAt = Date.now();
        // Not inside the popstate: a second navigation while the system's swipe is still landing
        // keeps iOS showing its picture of the old page (a white screen for seconds). Let the page
        // show the result first, then put the app entry back.
        afterPaint(() => { if (marked(FLOOR)) pushApp(appHref.current); });
      } else if (marked(APP)) {
        appHref.current = here();
      }
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
    // The refs are stable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
