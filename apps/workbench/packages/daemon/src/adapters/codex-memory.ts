import { constants } from 'node:fs';
import { lstat, open, opendir, realpath } from 'node:fs/promises';
import { basename, join, resolve, sep } from 'node:path';
import type { MemoryDocument, MemoryFile, MemoryIndex, MemoryKind } from '@calli/extension-api';
import { HttpError } from '../errors.js';
import type { MemoryReader } from './types.js';

const MAX_FILES = 500;
const MAX_ENTRIES = 2000;
const MAX_DOCUMENT = 512 * 1024;
const ROOT_FILES = new Map<string, MemoryKind>([
  ['memory_summary.md', 'summary'], ['MEMORY.md', 'durable'], ['raw_memories.md', 'raw'],
]);
const FOLDERS = new Map<string, MemoryKind>([
  ['rollout_summaries', 'evidence'], ['raw_memories', 'raw'], ['skills', 'skill'],
]);
const missing = (error: unknown) => ['ENOENT', 'ENOTDIR'].includes((error as NodeJS.ErrnoException).code ?? '');

/** Codex owns this layout. Never create directories, launch Codex or read its database/config. */
export class CodexMemoryReader implements MemoryReader {
  constructor(private readonly directory: string) {}

  async root(): Promise<string> {
    return realpath(this.directory).catch(() => resolve(this.directory));
  }

  private kind(id: string): MemoryKind | null {
    if (!id || id.length > 1024 || id.includes('\\') || id.includes('\0')) return null;
    const parts = id.split('/');
    if (parts.some(p => !p || p === '.' || p === '..') || parts.length > 4) return null;
    if (parts.length === 1) return ROOT_FILES.get(id) ?? null;
    return id.endsWith('.md') ? FOLDERS.get(parts[0]) ?? null : null;
  }

  /** Refuse symlink components and verify the opened descriptor before consuming bounded text. */
  private async text(id: string, limit: number): Promise<{ text: string; bytes: number; modifiedAt: string; truncated: boolean }> {
    if (!this.kind(id)) throw new HttpError(404, 'Memory document not found');
    const root = await realpath(this.directory);
    let path = root;
    for (const part of id.split('/')) {
      path = join(path, part);
      if ((await lstat(path)).isSymbolicLink()) throw new HttpError(404, 'Memory document not found');
    }
    const actual = await realpath(path);
    if (!actual.startsWith(root + sep)) throw new HttpError(404, 'Memory document not found');
    const file = await open(actual, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    try {
      const stat = await file.stat();
      if (!stat.isFile()) throw new HttpError(404, 'Memory document not found');
      // Linux descriptor validation also closes a parent-directory replacement race.
      if (process.platform === 'linux') {
        const opened = await realpath(`/proc/self/fd/${file.fd}`);
        if (!opened.startsWith(root + sep)) throw new HttpError(404, 'Memory document not found');
      }
      const buffer = Buffer.alloc(Math.min(limit + 1, stat.size));
      const { bytesRead } = await file.read(buffer, 0, buffer.length, 0);
      const data = buffer.subarray(0, Math.min(limit, bytesRead));
      if (data.includes(0)) throw new HttpError(415, 'Unsupported memory document');
      const truncated = stat.size > limit;
      let text = data.toString('utf8');
      if (truncated) {
        const newline = text.lastIndexOf('\n');
        text = newline >= 0 ? text.slice(0, newline) : text.replace(/\uFFFD$/, '');
      }
      return { text, bytes: stat.size, modifiedAt: stat.mtime.toISOString(), truncated };
    } finally { await file.close(); }
  }

  async read(id: string): Promise<MemoryDocument> {
    try {
      const { text, modifiedAt, truncated } = await this.text(id, MAX_DOCUMENT);
      return { id, text, modifiedAt, truncated };
    } catch (error) {
      if (error instanceof HttpError) throw error;
      throw new HttpError(missing(error) ? 404 : 403, 'Memory document unavailable');
    }
  }

  async list(): Promise<MemoryIndex> {
    const result: MemoryIndex = { state: 'available', files: [], checkedAt: new Date().toISOString(), partial: false };
    let entries = 0;
    let foundAny = false;
    const visit = async (folder: string, depth: number): Promise<void> => {
      const dir = await opendir(join(this.directory, folder));
      for await (const entry of dir) {
        foundAny = true;
        if (++entries > MAX_ENTRIES || result.files.length >= MAX_FILES) { result.partial = true; break; }
        const id = folder ? `${folder}/${entry.name}` : entry.name;
        if (entry.isSymbolicLink()) { result.partial = true; continue; }
        if (entry.isDirectory() && depth < 3 && (folder || FOLDERS.has(entry.name))) {
          try { await visit(id, depth + 1); } catch { result.partial = true; }
        } else if (entry.isFile() && this.kind(id)) {
          try {
            const data = await this.text(id, 4096);
            const lines = data.text.split('\n');
            const title = lines.find(line => /^#{1,6}\s+/.test(line))?.replace(/^#+\s+/, '').slice(0, 160) || basename(id, '.md');
            const excerpt = lines.filter(line => line.trim() && !/^#/.test(line)).join(' ').slice(0, 220);
            result.files.push({ id, path: id, kind: this.kind(id)!, title, excerpt, bytes: data.bytes, modifiedAt: data.modifiedAt });
          } catch { result.partial = true; }
        }
      }
    };
    try {
      await visit('', 0);
      if (!result.files.length) result.state = result.partial ? 'unreadable' : foundAny ? 'unsupported' : 'empty';
    } catch (error) { result.state = missing(error) ? 'missing' : 'unreadable'; }
    const order: Record<MemoryKind, number> = { summary: 0, durable: 1, raw: 2, evidence: 3, skill: 4, other: 5 };
    result.files.sort((a: MemoryFile, b: MemoryFile) => order[a.kind] - order[b.kind] || b.modifiedAt.localeCompare(a.modifiedAt) || a.path.localeCompare(b.path));
    return result;
  }
}
