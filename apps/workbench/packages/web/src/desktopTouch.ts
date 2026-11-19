// The arithmetic of the desktop on a touch screen: how big the picture is at a zoom, where the
// view scrolls to so a pinch stays under the fingers, and how a finger on the touchpad becomes
// pointer motion. Kept apart from the panel so it can be tested without a browser.

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 6;

export interface Size {
  w: number;
  h: number;
}

export const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

/** The desktop scaled to fit the box whole, keeping its shape. */
export function fitSize(desktop: Size, box: Size): Size {
  if (!desktop.w || !desktop.h || !box.w || !box.h) return { w: box.w, h: box.h };
  const s = Math.min(box.w / desktop.w, box.h / desktop.h);
  return { w: desktop.w * s, h: desktop.h * s };
}

/** The zoom at which the picture fills the box's height (a wide desktop on an upright phone). */
export function fillZoom(desktop: Size, box: Size): number {
  const fit = fitSize(desktop, box);
  return clampZoom(Math.max(box.w / fit.w, box.h / fit.h));
}

/**
 * Where to scroll so the point under `focal` (relative to the box) stays there while the zoom
 * goes from z0 to z1. `offset` is how far the picture sits from the box's start when it is
 * smaller than the box and centred.
 */
export function scrollForZoom(scroll: number, focal: number, z0: number, z1: number, offset0 = 0, offset1 = 0): number {
  const content = (scroll + focal - offset0) / z0;
  return Math.max(0, content * z1 + offset1 - focal);
}

/** How far the pointer moves on the desktop for a finger's move: slow is precise, fast covers ground. */
export function pointerDelta(fingerPx: number, ms: number, desktopPerScreenPx: number): number {
  const speed = ms > 0 ? Math.abs(fingerPx) / ms : 0;
  // From half a desktop pixel per screen pixel at a crawl to the picture's own scale and a bit when flicked.
  const gain = Math.min(1.4, 0.5 + speed * 0.9);
  return fingerPx * desktopPerScreenPx * gain;
}

/** A touch that ended without travelling far, quickly enough to be a tap. */
export const TAP_MS = 250;
export const TAP_SLOP_PX = 10;
export const LONG_PRESS_MS = 450;

export function isTap(ms: number, travelled: number): boolean {
  return ms < TAP_MS && travelled < TAP_SLOP_PX;
}

/** Two fingers: spreading or pinching zooms; moving together scrolls. Undecided until one is clear. */
export function twoFingerKind(startDistance: number, distance: number, midMoved: number): 'zoom' | 'scroll' | null {
  if (Math.abs(distance - startDistance) > 30) return 'zoom';
  if (midMoved > 12) return 'scroll';
  return null;
}

/**
 * What changed in the hidden field the phone keyboard types into: how many characters were rubbed
 * out before the caret, and what was put in their place. Matched from both ends, so text put in
 * the middle (the caret moved with the space bar) is still only what was added.
 */
export function textEdit(before: string, after: string): { erased: number; typed: string } {
  let head = 0;
  while (head < before.length && head < after.length && before[head] === after[head]) head++;
  let tail = 0;
  while (tail < before.length - head && tail < after.length - head && before[before.length - 1 - tail] === after[after.length - 1 - tail]) tail++;
  return { erased: before.length - head - tail, typed: after.slice(head, after.length - tail) };
}

/** Which way two fingers scroll, decided by their first clear move, so a slight slant does not scroll sideways too. */
export function scrollAxis(dx: number, dy: number): 'x' | 'y' {
  return Math.abs(dx) > Math.abs(dy) * 1.5 ? 'x' : 'y';
}

/**
 * Whether the on-screen keyboard covers the window: while a text field keeps focus on iOS, the
 * system hands neither touches nor scrolls to the rest of the page, so gestures on the desktop
 * picture die until the field gives up focus. Toolbars shrink the visual viewport by far less
 * than a keyboard does, so only a gap this deep counts as the keyboard being up.
 */
export function softwareKeyboardUp(layoutHeight: number, visualHeight: number | null): boolean {
  return visualHeight !== null && layoutHeight - visualHeight > 200;
}
