import { createHmac, timingSafeEqual } from 'node:crypto';
import { readFileSync, statSync } from 'node:fs';
import { HttpError } from './errors.js';

/**
 * Serving a thread's files for an <iframe> to render: pages an agent wrote, with the CSS and
 * pictures they reference next to them. Two properties shape this file:
 *
 * - The iframe gets its own port, so a real origin of its own. A sandboxed frame without
 *   same-origin is opaque, and the browser then refuses its subresource loads outright
 *   (opaque-response blocking) — a page could never load its own stylesheet. On a separate
 *   origin the page loads its siblings as same-origin, while staying cross-origin to the app
 *   itself: its scripts reach neither the app's storage nor its API.
 *
 * - Entry is a cookie, not a signature in the URL: relative references the page makes carry no
 *   query string, so a per-path signature could never reach them. A cookie rides along on its
 *   own, and `Path` scopes it to this thread's files alone — nothing else on either port will
 *   ever be sent it.
 */

/** How long a preview grant works. Reading a page someone's agent just wrote is a short visit. */
export const PREVIEW_TTL_S = 30 * 60;

/** How big a served file may be. Previews are pages and their assets, not videos. */
const MAX_BYTES = 8 * 1024 * 1024;

export const PREVIEW_COOKIE = 'calli-preview';
/** Its name before the rename to Calli; a grant set then still works until it expires. */
export const LEGACY_PREVIEW_COOKIE = 'rah-preview';

/** The cookie value: a grant for one thread's files, valid until it expires. */
export function makeGrant(secret: string, threadId: string, now = Date.now()): { value: string; maxAge: number } {
  const exp = Math.floor((now + PREVIEW_TTL_S * 1000) / 1000);
  return { value: `${exp}.${grantSignature(secret, threadId, exp)}`, maxAge: PREVIEW_TTL_S };
}

/** Whether this cookie value still opens the thread's files. */
export function checkGrant(secret: string, threadId: string, value: string | undefined, now = Date.now()): void {
  if (!value) throw new HttpError(403, 'this preview has expired; open it again from the thread');
  const dot = value.indexOf('.');
  if (dot <= 0) throw new HttpError(403, 'this preview has expired; open it again from the thread');
  const exp = value.slice(0, dot);
  const sig = value.slice(dot + 1);
  if (!/^\d+$/.test(exp) || Number(exp) * 1000 < now) throw new HttpError(403, 'this preview has expired; open it again from the thread');
  const want = grantSignature(secret, threadId, Number(exp));
  const a = Buffer.from(want, 'utf8');
  const b = Buffer.from(sig, 'utf8');
  if (a.length !== b.length || !timingSafeEqual(a, b)) throw new HttpError(403, 'this preview has expired; open it again from the thread');
}

/** Reads one cookie out of a Cookie header, the way node serves it. */
export function cookieValue(header: string | undefined, name: string): string | undefined {
  if (!header) return undefined;
  for (const part of header.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    if (part.slice(0, eq).trim() === name) return part.slice(eq + 1).trim();
  }
  return undefined;
}

/** Content types a preview may carry; anything else is not something an iframe should run. */
const CONTENT_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.htm': 'text/html; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.avif': 'image/avif',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

/** A served file: its bytes and the content type to send them as. */
export function readPreviewFile(real: string): { body: Buffer; contentType: string } {
  let size = 0;
  try {
    size = statSync(real).size;
  } catch {
    throw new HttpError(404, 'no such file in this thread');
  }
  if (size > MAX_BYTES) throw new HttpError(413, 'this file is too large to preview');
  const dot = real.lastIndexOf('.');
  const contentType = CONTENT_TYPES[dot < 0 ? '' : real.slice(dot).toLowerCase()] ?? 'application/octet-stream';
  return { body: readFileSync(real), contentType };
}

function grantSignature(secret: string, threadId: string, exp: number): string {
  return createHmac('sha256', secret).update(`${threadId}\n${exp}`).digest('hex');
}
