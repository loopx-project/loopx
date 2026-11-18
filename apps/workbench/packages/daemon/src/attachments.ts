import { randomUUID } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { HttpError } from './errors.js';
import { cleanFileName, FILE_TYPE, MAX_UPLOADS, MAX_VIDEOS, storeFile, storeVideo, VIDEO_EXTENSIONS } from './videos.js';

export const MAX_IMAGES = 6;
export const MAX_IMAGE_BYTES = 5 * 1024 * 1024;

const EXTENSIONS: Record<string, string> = {
  'image/png': 'png',
  'image/jpeg': 'jpg',
  'image/gif': 'gif',
  'image/webp': 'webp',
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** An image as sent to an agent: base64 data plus the type found in the bytes. */
export interface ImageInput {
  data: string;
  mimeType: string;
  /**
   * Where the hub keeps the file, for agents that are given a path rather than the bytes.
   * Set for images sent in a turn; absent for images read back from an agent's history.
   */
  path?: string;
}

/** The lines that tell an agent where the pictures of a message are, for agents that read them from disk. */
export function imagePathNote(images: ImageInput[]): string {
  const paths = images.map((img) => img.path).filter((p): p is string => Boolean(p));
  if (paths.length === 0) return '';
  return `\n\n${paths.length === 1 ? 'The user attached an image' : `The user attached ${paths.length} images`}. Look at ${paths.length === 1 ? 'it' : 'them'} before answering:\n${paths.map((p) => `- ${p}`).join('\n')}`;
}

/** What the event log keeps about an image. The bytes live on disk. */
export interface StoredAttachment {
  id: string;
  mimeType: string;
  size: number;
}

/**
 * An uploaded file: a video (`video/*`) or any other file (`application/octet-stream`). Messages keep
 * both in their `videos` list, the name it had before other files could be attached.
 */
export interface StoredVideo extends StoredAttachment { name: string }

export const isVideoType = (mimeType: string): boolean => Boolean(VIDEO_EXTENSIONS[mimeType]);

/**
 * Finds the image type from the first bytes of the data. The type the client claims is ignored,
 * so nothing other than a real PNG, JPEG, GIF or WebP can be stored and served back as an image.
 */
export function sniffImageType(buf: Buffer): string | null {
  if (buf.length >= 8 && buf.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
    return 'image/png';
  }
  if (buf.length >= 3 && buf[0] === 0xff && buf[1] === 0xd8 && buf[2] === 0xff) return 'image/jpeg';
  const head = buf.subarray(0, 12).toString('latin1');
  if (head.startsWith('GIF87a') || head.startsWith('GIF89a')) return 'image/gif';
  if (buf.length >= 12 && head.startsWith('RIFF') && head.slice(8, 12) === 'WEBP') return 'image/webp';
  return null;
}

/**
 * Whether a file's first bytes are the start of an SVG document: `<svg` after nothing but a byte
 * order mark, an XML declaration, comments and a doctype. Kept apart from `sniffImageType`: an
 * SVG is text that can carry scripts, so it is only ever served for viewing (see `getThreadImage`),
 * never accepted as an attachment.
 */
export function sniffSvg(head: Buffer): boolean {
  let text = head.toString('utf8').replace(/^\uFEFF/, '');
  for (;;) {
    const skipped = text.replace(/^\s+/, '').replace(/^<\?xml[^>]*\?>|^<!--[\s\S]*?-->|^<!DOCTYPE[^>[]*(?:\[[^\]]*\])?\s*>/i, '');
    if (skipped.length === text.length) break;
    text = skipped;
  }
  return /^<svg[\s>]/i.test(text);
}

/** Image files attached to turns, kept under `<dataDir>/attachments/<threadId>/`. */
export class AttachmentStore {
  private readonly root: string;

  constructor(dataDir: string) {
    this.root = join(dataDir, 'attachments');
  }

  async saveVideo(threadId: string, source: AsyncIterable<Uint8Array>, name: string): Promise<StoredVideo> {
    if (!UUID.test(threadId)) throw new HttpError(400, 'invalid thread id');
    const dir = join(this.root, threadId);
    mkdirSync(dir, { recursive: true });
    const id = randomUUID();
    return { id, ...await storeVideo(join(dir, id), source, name) };
  }

  async saveFile(threadId: string, source: AsyncIterable<Uint8Array>, name: string): Promise<StoredVideo> {
    if (!UUID.test(threadId)) throw new HttpError(400, 'invalid thread id');
    const dir = join(this.root, threadId);
    mkdirSync(dir, { recursive: true });
    const id = randomUUID();
    return { id, ...await storeFile(join(dir, id), source, name) };
  }

  /** One stored image by id, for reusing it in an edited message. */
  image(threadId: string, id: string): { stored: StoredAttachment; image: ImageInput } | null {
    const found = this.find(threadId, id);
    if (!found || isVideoType(found.mimeType) || found.mimeType === FILE_TYPE) return null;
    const bytes = readFileSync(found.path);
    return { stored: { id, mimeType: found.mimeType, size: bytes.length }, image: { data: bytes.toString('base64'), mimeType: found.mimeType, path: found.path } };
  }

  /** Only ids uploaded to this thread can be sent, never a client-supplied local path. */
  videos(threadId: string, raw: unknown): StoredVideo[] {
    if (raw === undefined) return [];
    if (!Array.isArray(raw) || raw.length > MAX_UPLOADS) throw new HttpError(400, `一条消息最多 ${MAX_UPLOADS} 个文件`);
    const ids = raw.map(value => typeof value === 'string' ? value : value?.id);
    if (new Set(ids).size !== ids.length) throw new HttpError(400, '文件不能重复');
    const found = ids.map(id => {
      if (typeof id !== 'string' || !UUID.test(id)) throw new HttpError(400, 'invalid file id');
      const file = this.find(threadId, id);
      if (!file || !(isVideoType(file.mimeType) || file.mimeType === FILE_TYPE)) throw new HttpError(404, 'file not found in this thread');
      return { id, name: this.uploadName(threadId, id, file.mimeType), mimeType: file.mimeType, size: file.size };
    });
    if (found.filter(f => isVideoType(f.mimeType)).length > MAX_VIDEOS) throw new HttpError(400, `一条消息最多 ${MAX_VIDEOS} 个视频`);
    return found;
  }

  private meta(threadId: string, id: string): { name?: string } | null {
    try { return JSON.parse(readFileSync(join(this.root, threadId, `${id}.json`), 'utf8')); } catch { return null; }
  }

  private uploadName(threadId: string, id: string, mimeType: string): string {
    const fallback = isVideoType(mimeType) ? `视频.${VIDEO_EXTENSIONS[mimeType]}` : 'file';
    return this.meta(threadId, id)?.name || fallback; // older video metadata may be missing
  }

  /** The lines that tell the agent where the uploaded files of a message are. */
  videoNote(threadId: string, videos: StoredVideo[]): string {
    const list = (items: StoredVideo[]) => items.map(v => `- ${JSON.stringify(v.name)}: ${this.find(threadId, v.id)!.path}`).join('\n');
    const clips = videos.filter(v => isVideoType(v.mimeType));
    const files = videos.filter(v => !isVideoType(v.mimeType));
    let note = '';
    if (clips.length) note += `\n\nThe user attached ${clips.length} video file(s). Use your available tools to inspect the original files; if you cannot inspect them, say so rather than guessing their contents.\n${list(clips)}`;
    if (files.length) note += `\n\nThe user attached ${files.length} file(s). Read them with your tools before answering; if you cannot open one, say so rather than guessing its contents.\n${list(files)}`;
    return note;
  }

  /**
   * Checks every image, then writes them all. Nothing is written if any one is rejected.
   * Returns the records for the event log and the images in the form adapters send to agents.
   */
  save(threadId: string, raw: unknown[]): { stored: StoredAttachment[]; images: ImageInput[] } {
    if (!UUID.test(threadId)) throw new HttpError(400, 'invalid thread id');
    const checked = this.check(raw);

    const dir = join(this.root, threadId);
    if (checked.length) mkdirSync(dir, { recursive: true });
    const stored: StoredAttachment[] = [];
    const images: ImageInput[] = [];
    for (const { bytes, mimeType } of checked) {
      const id = randomUUID();
      const path = join(dir, `${id}.${EXTENSIONS[mimeType]}`);
      writeFileSync(path, bytes);
      stored.push({ id, mimeType, size: bytes.length });
      images.push({ data: bytes.toString('base64'), mimeType, path });
    }
    return { stored, images };
  }

  /** Decodes and checks images without storing them. Throws on the first one that is not acceptable. */
  check(raw: unknown[]): Array<{ bytes: Buffer; mimeType: string }> {
    if (raw.length > MAX_IMAGES) throw new HttpError(400, `at most ${MAX_IMAGES} images per message`);
    return raw.map((item, i) => {
      const value = (item as { data?: unknown } | null)?.data;
      if (typeof value !== 'string' || !value) throw new HttpError(400, `image ${i + 1} has no data`);
      // Accept a data URL as well as bare base64.
      const base64 = value.startsWith('data:') ? value.slice(value.indexOf(',') + 1) : value;
      const bytes = Buffer.from(base64, 'base64');
      if (bytes.length === 0) throw new HttpError(400, `image ${i + 1} is empty`);
      if (bytes.length > MAX_IMAGE_BYTES) {
        throw new HttpError(413, `image ${i + 1} is larger than ${MAX_IMAGE_BYTES / 1024 / 1024} MB`);
      }
      const mimeType = sniffImageType(bytes);
      if (!mimeType) throw new HttpError(400, `image ${i + 1} is not a PNG, JPEG, GIF or WebP file`);
      return { bytes, mimeType };
    });
  }

  /** Path and type of a stored attachment, or null. Ids cannot escape the directory. */
  find(threadId: string, id: string): { path: string; mimeType: string; size: number } | null {
    if (!UUID.test(threadId) || !UUID.test(id)) return null;
    for (const [mimeType, ext] of Object.entries({ ...EXTENSIONS, ...VIDEO_EXTENSIONS })) {
      const path = join(this.root, threadId, `${id}.${ext}`);
      if (existsSync(path)) return { path, mimeType, size: statSync(path).size };
    }
    const name = this.meta(threadId, id)?.name;
    const path = name && name === cleanFileName(name) && join(this.root, threadId, id, name);
    if (path && existsSync(path)) return { path, mimeType: FILE_TYPE, size: statSync(path).size };
    return null;
  }

  /** Gives a forked thread its own copy of the images, so deleting either thread leaves the other intact. */
  copyThread(fromThreadId: string, toThreadId: string): void {
    if (!UUID.test(fromThreadId) || !UUID.test(toThreadId)) return;
    const from = join(this.root, fromThreadId);
    if (existsSync(from)) cpSync(from, join(this.root, toThreadId), { recursive: true });
  }

  removeThread(threadId: string): void {
    if (!UUID.test(threadId)) return;
    rmSync(join(this.root, threadId), { recursive: true, force: true });
  }
}
