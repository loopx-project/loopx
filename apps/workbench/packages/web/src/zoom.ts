/** Looking closer at a picture shown at full size: how far in, and which part. Pure, so it can be tested directly. */

export interface View {
  /** 1 is the picture as it fits the screen. */
  scale: number;
  /** How far the picture's centre is moved from where it rests, in screen pixels. */
  x: number;
  y: number;
}

export const REST: View = { scale: 1, x: 0, y: 0 };
export const MAX_ZOOM = 8;

interface Size {
  width: number;
  height: number;
}

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

/** Keeps the picture over the place it rests in: no edge is dragged past where the opposite one started. */
function held(view: View, size: Size): View {
  if (view.scale <= 1) return REST;
  const maxX = (size.width * (view.scale - 1)) / 2;
  const maxY = (size.height * (view.scale - 1)) / 2;
  return { scale: view.scale, x: clamp(view.x, -maxX, maxX), y: clamp(view.y, -maxY, maxY) };
}

/**
 * Zooms by `factor`, keeping the spot under `point` where it is. `point` is measured from where
 * the picture's centre rests; `size` is the picture's size at rest.
 */
export function zoomAt(view: View, factor: number, point: { x: number; y: number }, size: Size, maximum = MAX_ZOOM): View {
  const scale = clamp(view.scale * factor, 1, maximum);
  const k = scale / view.scale;
  return held({ scale, x: point.x - (point.x - view.x) * k, y: point.y - (point.y - view.y) * k }, size);
}

/** Drags the picture. At rest there is nothing to drag. */
export function panBy(view: View, dx: number, dy: number, size: Size): View {
  return held({ scale: view.scale, x: view.x + dx, y: view.y + dy }, size);
}

/**
 * The size a picture rests at in the room there is. A bitmap is never drawn larger than it is; a
 * drawing has no pixels to spare, so it takes all the room its shape allows.
 */
export function fitted(natural: Size, room: Size, vector: boolean): Size {
  if (natural.width <= 0 || natural.height <= 0 || room.width <= 0 || room.height <= 0) return { width: 0, height: 0 };
  const k = Math.min(room.width / natural.width, room.height / natural.height);
  const scale = vector ? k : Math.min(1, k);
  return { width: Math.round(natural.width * scale), height: Math.round(natural.height * scale) };
}
