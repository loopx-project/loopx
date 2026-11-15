import { mkdir, open, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { HttpError } from './errors.js';

export const MAX_VIDEOS = 3;
export const MAX_VIDEO_BYTES = 100 * 1024 * 1024;
/** How many uploaded files, videos included, one message may carry. */
export const MAX_UPLOADS = 10;
export const MAX_FILE_BYTES = 100 * 1024 * 1024;
/** The stored type of an attached file that is not a video: bytes to save, never to render. */
export const FILE_TYPE = 'application/octet-stream';
export const VIDEO_EXTENSIONS: Record<string, string> = { 'video/mp4': 'mp4', 'video/quicktime': 'mov', 'video/webm': 'webm' };

/** Container signatures, not the browser's claimed MIME type or file extension. */
export function sniffVideoType(head: Buffer): string | null {
  if (head.length >= 16 && head.toString('ascii', 4, 8) === 'ftyp') {
    const end = Math.min(head.readUInt32BE(0), head.length);
    if (end < 16) return null;
    const brands = [head.toString('ascii', 8, 12)];
    for (let i = 16; i + 4 <= end; i += 4) brands.push(head.toString('ascii', i, i + 4));
    if (brands.some(b => /^(avif|avis|heic|heix|mif1|msf1)$/.test(b))) return null;
    if (brands.includes('qt  ')) return 'video/quicktime';
    if (brands.some(b => /^(isom|iso[2-9]|mp4[12]|avc1|M4V |dash)$/.test(b))) return 'video/mp4';
  }
  if (head.length >= 8 && head.readUInt32BE(0) === 0x1a45dfa3 && head.includes(Buffer.from('webm'))) return 'video/webm';
  return null;
}

/** Stream to a private partial file, then publish only a validated, complete upload. */
export async function storeVideo(base: string, source: AsyncIterable<Uint8Array>, name: string): Promise<{ mimeType: string; size: number; name: string }> {
  const partial = `${base}.part`;
  const file = await open(partial, 'wx', 0o600);
  let size = 0;
  let head = Buffer.alloc(0);
  let destination: string | undefined;
  try {
    for await (const chunk of source) {
      size += chunk.length;
      if (size > MAX_VIDEO_BYTES) throw new HttpError(413, '视频不能超过 100 MB');
      if (head.length < 4096) head = Buffer.concat([head, chunk.subarray(0, 4096 - head.length)]);
      await file.writeFile(chunk);
    }
    const mimeType = sniffVideoType(head);
    if (!mimeType) throw new HttpError(400, '请选择 MP4、MOV 或 WebM 视频');
    const info = { mimeType, size, name: cleanFileName(name) || `视频.${VIDEO_EXTENSIONS[mimeType]}` };
    destination = `${base}.${VIDEO_EXTENSIONS[mimeType]}`;
    await file.close();
    await rename(partial, destination);
    await writeFile(`${base}.json`, JSON.stringify(info), { mode: 0o600 });
    return info;
  } catch (error) {
    await file.close().catch(() => undefined);
    await rm(partial, { force: true });
    if (destination) await rm(destination, { force: true });
    await rm(`${base}.json`, { force: true });
    throw error;
  }
}

/** The last path segment of a client-supplied name, without control characters; empty if nothing usable is left. */
export function cleanFileName(name: string): string {
  const clean = name.split(/[\\/]/).pop()?.replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 200) ?? '';
  return clean === '.' || clean === '..' ? '' : clean;
}

/**
 * Any other file the user attaches. It keeps its own name, inside a folder named by its id, so the
 * agent's tools see a real extension. Its type is never taken from the client: it is served back
 * as octet-stream, only ever for downloading.
 */
export async function storeFile(base: string, source: AsyncIterable<Uint8Array>, name: string): Promise<{ mimeType: string; size: number; name: string }> {
  const partial = `${base}.part`;
  const file = await open(partial, 'wx', 0o600);
  let size = 0;
  try {
    for await (const chunk of source) {
      size += chunk.length;
      if (size > MAX_FILE_BYTES) throw new HttpError(413, '文件不能超过 100 MB');
      await file.writeFile(chunk);
    }
    await file.close();
    if (!size) throw new HttpError(400, '文件是空的');
    const info = { mimeType: FILE_TYPE, size, name: cleanFileName(name) || 'file' };
    await mkdir(base, { mode: 0o700 });
    await rename(partial, join(base, info.name));
    await writeFile(`${base}.json`, JSON.stringify(info), { mode: 0o600 });
    return info;
  } catch (error) {
    await file.close().catch(() => undefined);
    await rm(partial, { force: true });
    await rm(base, { recursive: true, force: true });
    await rm(`${base}.json`, { force: true });
    throw error;
  }
}
