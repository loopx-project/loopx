import { ApiError, type WritingStage } from './api';
import { t } from './i18n';
import { parentDir, resolveRelative } from './files';

/** Writing mode's stages, in the order they come. */
export const WRITING_STAGES: readonly WritingStage[] = ['draft', 'second', 'third', 'figures', 'polish'];

export function stageLabel(stage: WritingStage): string {
  return t(`writing.stage.${stage}`);
}

/** The body's name without its folder, for the stage bar. */
export function bodyName(file: string): string {
  return file.split('/').pop() ?? file;
}

/** Pictures the file viewer shows from beside the Markdown file. */
const PICTURE = /\.(png|jpe?g|gif|webp|svg)$/i;

/**
 * Where a relative picture in a Markdown file lives: resolved from the file's own folder.
 * Null for one from the web, a data address, or anything not a picture.
 */
export function relativePicture(markdownPath: string, src: string | undefined): string | null {
  if (!src) return null;
  const path = resolveRelative(parentDir(markdownPath), decodeSafely(src));
  return path && PICTURE.test(path) ? path : null;
}

function decodeSafely(src: string): string {
  try {
    return decodeURI(src);
  } catch {
    return src;
  }
}

/** The hash on disk now, when a save was refused because the file changed under it. */
export function conflictHash(error: unknown): string | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null;
  const hash = error.details.hash;
  return typeof hash === 'string' ? hash : null;
}

/** Unsaved words in a body being edited, kept on this device per thread and file. */
export interface BodyDraft {
  text: string;
  /** The hash the text was started from, so a reload still knows when the file moved on. */
  baseHash: string;
  at: number;
}

export const BODY_DRAFTS_KEY = 'calli.writing.drafts';
/** Bodies are long; a few of them are plenty. */
export const MAX_BODY_DRAFTS = 10;

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>;

const draftKey = (threadId: string, path: string) => `${threadId}:${path}`;

function readAll(storage: Storage): Record<string, BodyDraft> {
  try {
    const parsed = JSON.parse(storage.getItem(BODY_DRAFTS_KEY) ?? '{}') as unknown;
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    const out: Record<string, BodyDraft> = {};
    for (const [key, value] of Object.entries(parsed as Record<string, Partial<BodyDraft>>)) {
      if (value && typeof value.text === 'string' && typeof value.baseHash === 'string') out[key] = { text: value.text, baseHash: value.baseHash, at: Number(value.at) || 0 };
    }
    return out;
  } catch {
    return {};
  }
}

export function readBodyDraft(storage: Storage, threadId: string, path: string): BodyDraft | null {
  return readAll(storage)[draftKey(threadId, path)] ?? null;
}

/** Keeps the draft, or forgets it with null. The oldest go first once there are too many. */
export function writeBodyDraft(storage: Storage, threadId: string, path: string, draft: { text: string; baseHash: string } | null, now = Date.now()): void {
  const all = readAll(storage);
  const key = draftKey(threadId, path);
  if (draft) all[key] = { text: draft.text, baseHash: draft.baseHash, at: now };
  else if (key in all) delete all[key];
  else return;
  const kept = Object.entries(all).sort((a, b) => b[1].at - a[1].at).slice(0, MAX_BODY_DRAFTS);
  try {
    storage.setItem(BODY_DRAFTS_KEY, JSON.stringify(Object.fromEntries(kept)));
  } catch {
    // Storage full or unavailable: the text still lasts until the page reloads.
  }
}
