import { createReadStream, existsSync, mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import type { ServerResponse } from 'node:http';
import { join } from 'node:path';
import { homeDirectory } from './config.js';
import { HttpError } from './errors.js';

/**
 * The desk's companion. Its frames are someone else's artwork (MIT, see LICENSE.txt next to them), far
 * too large to ship in this repository, so they are fetched once into the Calli home directory from a
 * pinned commit and served from there. Only the clips the desk plays are kept, every other frame.
 */
export const PET = {
  id: 'dafeiyu',
  repo: 'QCYTSN/dsh-dafeiyu',
  commit: '084323845ab82b68d5388d5c638bec09d8544a8a',
  /** Frames per clip in the source; the desk keeps the odd ones and plays them at 12 fps. */
  clips: { idle: 241, thinking: 241, success: 241, poke: 241, head_pat: 241, idle_yawn: 241 } as Record<string, number>,
  license: 'assets/dsh-pet-LICENSE.txt',
};

export type PetClip = keyof typeof PET.clips;
export interface PetStatus { id: string; commit: string; installed: boolean; installing: boolean; error: string | null; clips: Record<string, number>; frameMs: number }

const FRAME = /^\/pets\/([a-z0-9_-]+)\/([a-z_]+)\/([a-z_]+_\d{3})\.webp$/;

/** Frames kept for a clip: the source's odd-numbered ones. */
export const keptFrames = (count: number): number => Math.ceil(count / 2);
const frameName = (clip: string, index: number) => `${clip}_${String(index * 2 + 1).padStart(3, '0')}.webp`;

export class PetService {
  private installing: Promise<void> | null = null;
  private error: string | null = null;
  constructor(
    private readonly root = join(homeDirectory(), 'pets'),
    private readonly download: (url: string) => Promise<Uint8Array> = fetchBytes,
  ) {}

  private dir(): string { return join(this.root, PET.id); }
  private marker(): string { return join(this.dir(), 'INSTALLED'); }

  status(): PetStatus {
    return {
      id: PET.id,
      commit: PET.commit,
      installed: existsSync(this.marker()),
      installing: Boolean(this.installing),
      error: this.error,
      clips: Object.fromEntries(Object.entries(PET.clips).map(([clip, count]) => [clip, keptFrames(count)])),
      frameMs: 84,
    };
  }

  /** Starts the one-time download; a second call while it runs joins it. Resolves when it is done. */
  install(): Promise<void> {
    if (existsSync(this.marker())) return Promise.resolve();
    this.installing ??= this.fetchAll().then(
      () => { this.error = null; },
      (e: unknown) => { this.error = e instanceof Error ? e.message : String(e); throw e; },
    ).finally(() => { this.installing = null; });
    return this.installing;
  }

  private async fetchAll(): Promise<void> {
    const base = `https://raw.githubusercontent.com/${PET.repo}/${PET.commit}`;
    // Built beside the final directory and moved in whole, so a half-finished download never shows.
    const staging = `${this.dir()}.partial`;
    rmSync(staging, { recursive: true, force: true });
    mkdirSync(staging, { recursive: true });
    writeFileSync(join(staging, 'LICENSE.txt'), await this.download(`${base}/${PET.license}`));
    writeFileSync(join(staging, 'SOURCE'), `https://github.com/${PET.repo}/tree/${PET.commit}/assets/pet\nMIT, Copyright (c) 2026 PC2005-cloud\n`);
    for (const [clip, count] of Object.entries(PET.clips)) {
      mkdirSync(join(staging, clip));
      const names = Array.from({ length: keptFrames(count) }, (_, i) => frameName(clip, i));
      for (let i = 0; i < names.length; i += 12) {
        await Promise.all(names.slice(i, i + 12).map(async (name) => {
          writeFileSync(join(staging, clip, name), await this.download(`${base}/assets/pet/${clip}/${name}`));
        }));
      }
    }
    writeFileSync(join(staging, 'INSTALLED'), `${PET.commit}\n`);
    rmSync(this.dir(), { recursive: true, force: true });
    renameSync(staging, this.dir());
  }

  /** Serves `/pets/<id>/<clip>/<frame>.webp`. Public like the web build: it is artwork, not user data. */
  serve(path: string, res: ServerResponse): boolean {
    const match = FRAME.exec(path);
    if (!match) return false;
    const [, id, clip, name] = match;
    const file = join(this.dir(), clip, `${name}.webp`);
    if (id !== PET.id || !(clip in PET.clips) || !name.startsWith(`${clip}_`) || !existsSync(file) || !statSync(file).isFile()) {
      res.writeHead(404).end();
      return true;
    }
    res.writeHead(200, { 'Content-Type': 'image/webp', 'Cache-Control': 'public, max-age=31536000, immutable' });
    createReadStream(file).pipe(res);
    return true;
  }

  /** The license that came with the frames, for the about line. */
  license(): string | null {
    const file = join(this.dir(), 'LICENSE.txt');
    return existsSync(file) ? readFileSync(file, 'utf8') : null;
  }
}

async function fetchBytes(url: string): Promise<Uint8Array> {
  const response = await fetch(url, { signal: AbortSignal.timeout(30_000) });
  if (!response.ok) throw new HttpError(502, `pet download failed: ${response.status} ${url}`);
  return new Uint8Array(await response.arrayBuffer());
}
