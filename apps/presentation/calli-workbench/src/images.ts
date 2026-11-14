/** Preparing pictures for sending: checks, downscaling, base64. */
import { t } from './i18n';

export const MAX_IMAGES = 6;
/** Longest edge after downscaling. Larger pictures gain nothing for a model and cost upload time. */
const MAX_EDGE = 2048;
/** Files at or under this size and edge limit are sent untouched. */
const PASS_THROUGH_BYTES = 1.5 * 1024 * 1024;
/** The daemon refuses images over 5 MB; stay clearly under it. */
const MAX_SEND_BYTES = 4.5 * 1024 * 1024;
const PASS_THROUGH_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];

export interface PreparedImage {
  /** Local id, for React keys and removal. */
  id: string;
  name: string;
  data: string;
  mimeType: string;
  /** Object URL for the thumbnail. The owner revokes it when the image is removed or sent. */
  previewUrl: string;
}

/** The size a picture is drawn at so that neither edge exceeds `max`, keeping its shape. */
export function fitWithin(width: number, height: number, max: number): { width: number; height: number } {
  const longest = Math.max(width, height);
  if (longest <= max) return { width, height };
  const scale = max / longest;
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) };
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(t('images.unreadable')));
    img.src = url;
  });
}

function toBase64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result);
      resolve(url.slice(url.indexOf(',') + 1));
    };
    reader.onerror = () => reject(new Error(t('images.unreadable')));
    reader.readAsDataURL(blob);
  });
}

function canvasToJpeg(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error(t('images.uncompressible')))), 'image/jpeg', quality);
  });
}

/**
 * Reads a picked, pasted or dropped file. Small pictures go as they are. Large ones (a phone photo
 * is often 3-8 MB) are scaled down and re-encoded as JPEG so they upload quickly over a VPN.
 */
export async function prepareImage(file: File): Promise<PreparedImage> {
  if (!file.type.startsWith('image/')) throw new Error(t('images.notImage', file.name || t('images.thisFile')));
  const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const name = file.name || t('images.image');
  const originalUrl = URL.createObjectURL(file);

  let img: HTMLImageElement;
  try {
    img = await loadImage(originalUrl);
  } catch (err) {
    URL.revokeObjectURL(originalUrl);
    throw err;
  }

  const small = Math.max(img.naturalWidth, img.naturalHeight) <= MAX_EDGE && file.size <= PASS_THROUGH_BYTES;
  if (small && PASS_THROUGH_TYPES.includes(file.type)) {
    return { id, name, data: await toBase64(file), mimeType: file.type, previewUrl: originalUrl };
  }

  const { width, height } = fitWithin(img.naturalWidth, img.naturalHeight, MAX_EDGE);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) {
    URL.revokeObjectURL(originalUrl);
    throw new Error(t('images.uncompressible'));
  }
  // JPEG has no transparency; without a fill, transparent areas of a screenshot would turn black.
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, width, height);
  ctx.drawImage(img, 0, 0, width, height);
  URL.revokeObjectURL(originalUrl);

  for (const quality of [0.85, 0.7, 0.5]) {
    const blob = await canvasToJpeg(canvas, quality);
    if (blob.size <= MAX_SEND_BYTES) {
      return { id, name, data: await toBase64(blob), mimeType: 'image/jpeg', previewUrl: URL.createObjectURL(blob) };
    }
  }
  throw new Error(t('images.tooLarge', name));
}

/** A picture that was already sent (a queued message taken back to edit), back in the box. */
export function restoreImage(img: { data: string; mimeType: string }, index: number): PreparedImage {
  const bytes = Uint8Array.from(atob(img.data), (c) => c.charCodeAt(0));
  const blob = new Blob([bytes], { type: img.mimeType });
  return {
    id: `${Date.now()}-${index}-${Math.random().toString(36).slice(2)}`,
    name: t('images.image'),
    data: img.data,
    mimeType: img.mimeType,
    previewUrl: URL.createObjectURL(blob),
  };
}

/**
 * An address an <img> can show a fetched picture from. A bitmap gets a blob: address, for the
 * caller to revoke when it is done with it. An SVG gets a data: address: it is a document that can
 * carry scripts, and while an <img> runs none, a blob: address opened in a tab of its own ("open
 * image in new tab") belongs to this page's origin and would run them with the hub's token in
 * reach. A data: document belongs to no origin.
 */
export async function pictureUrl(blob: Blob): Promise<string> {
  if (blob.type !== 'image/svg+xml') return URL.createObjectURL(blob);
  return `data:image/svg+xml;base64,${await toBase64(blob)}`;
}

/** Past this much source a code block is left as code: a drawing is far smaller. */
const MAX_SVG_SOURCE = 2_000_000;

/**
 * The picture a code block draws, as an address for an <img>, when what it holds is one whole SVG
 * document; null when it holds anything else, or an SVG still being written. Agents often paste the
 * source of a drawing instead of, or as well as, saving it.
 *
 * An <img> reads its source as XML and gives up on the first undeclared prefix, where markup
 * written for a web page gets away with leaving the namespaces out; they are put back here.
 */
export function svgPicture(code: string): string | null {
  const text = code.trim();
  if (text.length > MAX_SVG_SOURCE || !/<\/svg\s*>$/i.test(text)) return null;
  const opening = /^(?:<\?xml[^>]*\?>\s*)?(?:<!--[\s\S]*?-->\s*)*(?:<!DOCTYPE[^>[]*(?:\[[^\]]*\])?\s*>\s*)?(?:<!--[\s\S]*?-->\s*)*<svg(?=[\s>])[^>]*>/i.exec(text);
  if (!opening) return null;
  const tag = opening[0];
  let declared = '';
  if (!/\sxmlns\s*=/.test(tag)) declared += ' xmlns="http://www.w3.org/2000/svg"';
  if (/\sxlink:/.test(text) && !/\sxmlns:xlink\s*=/.test(tag)) declared += ' xmlns:xlink="http://www.w3.org/1999/xlink"';
  const at = tag.toLowerCase().lastIndexOf('<svg') + '<svg'.length;
  const source = declared ? text.slice(0, at) + declared + text.slice(at) : text;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(source)}`;
}

const PICTURE_EXTENSIONS: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
  'image/svg+xml': 'svg',
};

/** The name a picture is saved under: the one it has, with the ending its type calls for when it has none. */
export function pictureFileName(name: string | undefined, mimeType: string): string {
  const base = (name ?? '').split(/[\\/]/).pop()?.trim() || 'image';
  return /\.[a-z0-9]{2,5}$/i.test(base) ? base : `${base}.${PICTURE_EXTENSIONS[mimeType] ?? 'png'}`;
}

/** The same name for the picture saved as a PNG. */
export function pngFileName(name: string): string {
  return `${name.replace(/\.[a-z0-9]{2,5}$/i, '')}.png`;
}

/** Hands a file to the browser to save. */
export function saveAs(href: string, name: string): void {
  const link = document.createElement('a');
  link.href = href;
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
}

/** Saves a made-up file (a plan as Markdown) under the name it gets. */
export function downloadBlob(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  saveAs(href, name);
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

/** Saves a picture shown from `url` as the file it is. The name gets an ending if it lacks one. */
export async function savePicture(url: string, name?: string): Promise<void> {
  const blob = await (await fetch(url)).blob();
  // Always from a blob: address: some browsers open a data: link instead of saving it.
  const href = URL.createObjectURL(blob);
  saveAs(href, pictureFileName(name, blob.type));
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}

/** The longest edge a drawing is rendered at when saved as a bitmap. */
const PNG_EDGE = 2048;

/**
 * Saves a drawing as a PNG. A vector has no pixels of its own, so it is drawn large: its longest
 * edge at `PNG_EDGE`, or at its own size where that is larger still. What it leaves blank stays clear.
 */
export async function savePictureAsPng(url: string, name?: string): Promise<void> {
  const img = await loadImage(url);
  const natural = { width: img.naturalWidth || 300, height: img.naturalHeight || 150 };
  const k = Math.max(1, PNG_EDGE / Math.max(natural.width, natural.height));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(natural.width * k);
  canvas.height = Math.round(natural.height * k);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error(t('images.unexportable'));
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob((made) => (made ? resolve(made) : reject(new Error(t('images.unexportable')))), 'image/png');
  });
  const href = URL.createObjectURL(blob);
  saveAs(href, pngFileName(pictureFileName(name, 'image/svg+xml')));
  setTimeout(() => URL.revokeObjectURL(href), 10_000);
}
