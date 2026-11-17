import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import { mkdir, rename, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

/** One issued link token, as the settings page lists it. The token itself is kept only at issue
 * time — what is stored is its hash, so the file at rest cannot be used to log in. */
export interface LinkTokenRecord {
  id: string;
  createdAt: string;
  expiresAt: string;
  revokedAt: string | null;
  lastUsedAt: string | null;
}

interface StoredToken extends LinkTokenRecord {
  /** sha256 hex of the token string; compared the same constant-time way as the hub's own token. */
  hash: string;
}

export type LinkCheck =
  | { ok: true; id: string; expiresAt: string }
  | { ok: false; reason: 'expired' | 'revoked' }
  | null;

const DAY_MS = 24 * 60 * 60 * 1000;

function sameHex(a: string, b: string): boolean {
  const ba = Buffer.from(a, 'utf8');
  const bb = Buffer.from(b, 'utf8');
  return ba.length === bb.length && timingSafeEqual(ba, bb);
}

function hashToken(token: string): string {
  return createHash('sha256').update(token).digest('hex');
}

/**
 * The daemon's link tokens: temporary credentials for opening a link (`#/token=<token>`) that can read
 * and write this Workbench and nothing else. They expire (7 days by default), can be revoked one
 * by one, never reach the permanent token, and live in the data directory so a restart keeps them.
 *
 * The token strings look like `wl_…` so logs and error messages cannot confuse them with the hub's
 * own token.
 */
export class LinkTokens {
  private file: string;
  private now: () => Date;
  private entries: StoredToken[] = [];
  private writing: Promise<void> = Promise.resolve();

  constructor(dataDir: string, now: () => Date = () => new Date()) {
    this.file = join(dataDir, 'link-tokens.json');
    this.now = now;
  }

  /** Reads what was persisted. Missing or unreadable file starts empty, as a fresh install would. */
  async load(): Promise<void> {
    let raw = '';
    try {
      raw = (await import('node:fs')).readFileSync(this.file, 'utf8');
    } catch {
      this.entries = [];
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // A damaged file is not a reason to lock the person out; links can be reissued.
      this.entries = [];
      return;
    }
    const list = Array.isArray(parsed) ? parsed : [];
    this.entries = list.filter((e): e is StoredToken =>
      typeof e === 'object' && e !== null
      && typeof (e as StoredToken).id === 'string'
      && typeof (e as StoredToken).hash === 'string'
      && typeof (e as StoredToken).createdAt === 'string'
      && typeof (e as StoredToken).expiresAt === 'string');
  }

  /** Issues a new link token and returns it with its record. The token string exists only here. */
  async issue(ttlDays = 7): Promise<{ token: string; record: LinkTokenRecord }> {
    const token = `wl_${randomBytes(24).toString('hex')}`;
    const at = this.now();
    const entry: StoredToken = {
      id: randomBytes(8).toString('hex'),
      hash: hashToken(token),
      createdAt: at.toISOString(),
      expiresAt: new Date(at.getTime() + Math.max(1, Math.min(90, ttlDays)) * DAY_MS).toISOString(),
      revokedAt: null,
      lastUsedAt: null,
    };
    this.entries.push(entry);
    await this.enqueueSave();
    const { hash, ...record } = entry;
    void hash;
    return { token, record };
  }

  /** Is this bearer one of ours? `null` when it is not a link token at all (the hub's own check decides those). */
  check(token: string | undefined): LinkCheck {
    if (!token || !token.startsWith('wl_')) return null;
    const at = this.now().getTime();
    for (const entry of this.entries) {
      if (!sameHex(entry.hash, hashToken(token))) continue;
      if (entry.revokedAt) return { ok: false, reason: 'revoked' };
      if (Date.parse(entry.expiresAt) <= at) return { ok: false, reason: 'expired' };
      const used = new Date().toISOString();
      entry.lastUsedAt = used;
      // Best-effort persistence of the usage stamp; a lost stamp costs the settings page a date, nothing more.
      void this.enqueueSave().catch(() => {});
      return { ok: true, id: entry.id, expiresAt: entry.expiresAt };
    }
    return null;
  }

  list(): LinkTokenRecord[] {
    return this.entries.map(({ hash, ...record }) => { void hash; return record; });
  }

  /** Takes one link out of service. Unknown ids are said so, not thrown: the caller shows the list anyway. */
  async revoke(id: string): Promise<boolean> {
    const entry = this.entries.find(e => e.id === id);
    if (!entry || entry.revokedAt) return false;
    entry.revokedAt = this.now().toISOString();
    await this.enqueueSave();
    return true;
  }

  /** Every write goes through one queue: a usage stamp landing while a revoke saves must not
   * fight it over the temp file. Callers still see their own failure; the queue itself never breaks. */
  private enqueueSave(): Promise<void> {
    const run = this.writing.then(() => this.save());
    this.writing = run.catch(() => {});
    return run;
  }

  private async save(): Promise<void> {
    const body = JSON.stringify(this.entries, null, 2) + '\n';
    const dir = dirname(this.file);
    await mkdir(dir, { recursive: true });
    const tmp = `${this.file}.tmp`;
    await writeFile(tmp, body, { mode: 0o600 });
    await rename(tmp, this.file);
  }
}
