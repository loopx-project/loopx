import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { DatabaseSync } from 'node:sqlite';
import type { ExtensionManifest, Permission } from '@calli/extension-api';
import { checkExtensionRequirements } from './extension-dependencies.js';

/**
 * Calli extensions on this machine: which ones ship with Calli (the catalog), and which of them the
 * user installed here, with the permissions they confirmed. See docs/extensions-design.md.
 * v1 only knows built-in extensions; their code ships with the hub, so installing one stores a
 * decision, it fetches nothing.
 */

/** One installed extension, as stored. */
export interface ExtensionRecord {
  id: string;
  /** The version the user confirmed the permissions for. */
  version: string;
  enabled: boolean;
  granted: Permission[];
  installedAt: string;
  updatedAt: string;
}

/** Installation state in the hub's own database. */
export class ExtensionStore {
  constructor(private readonly db: DatabaseSync) {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS extensions (
        id TEXT PRIMARY KEY,
        version TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        granted TEXT NOT NULL DEFAULT '[]',
        installed_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
    `);
  }

  list(): ExtensionRecord[] {
    const rows = this.db.prepare('SELECT id, version, enabled, granted, installed_at, updated_at FROM extensions ORDER BY id').all() as Array<{
      id: string; version: string; enabled: number; granted: string; installed_at: string; updated_at: string;
    }>;
    return rows.map((row) => ({
      id: row.id,
      version: row.version,
      enabled: row.enabled !== 0,
      granted: parseGrant(row.granted),
      installedAt: row.installed_at,
      updatedAt: row.updated_at,
    }));
  }

  get(id: string): ExtensionRecord | null {
    return this.list().find((r) => r.id === id) ?? null;
  }

  /** Installs, or confirms the permissions again over an earlier install. Either way it ends up enabled. */
  install(id: string, version: string, granted: Permission[]): ExtensionRecord {
    const now = new Date().toISOString();
    this.db
      .prepare('INSERT INTO extensions (id, version, enabled, granted, installed_at, updated_at) VALUES (?, ?, 1, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET version = excluded.version, enabled = 1, granted = excluded.granted, updated_at = excluded.updated_at')
      .run(id, version, JSON.stringify(granted), now, now);
    return this.get(id)!;
  }

  setEnabled(id: string, enabled: boolean): ExtensionRecord | null {
    this.db.prepare('UPDATE extensions SET enabled = ?, updated_at = ? WHERE id = ?').run(enabled ? 1 : 0, new Date().toISOString(), id);
    return this.get(id);
  }

  remove(id: string): boolean {
    return this.db.prepare('DELETE FROM extensions WHERE id = ?').run(id).changes > 0;
  }
}

function parseGrant(json: string): Permission[] {
  try {
    const value = JSON.parse(json);
    return Array.isArray(value) ? value.filter((p): p is Permission => typeof p === 'string' && PERMISSIONS.has(p as Permission)) : [];
  } catch {
    return [];
  }
}

/** Every permission name in the contract. Kept in step with packages/extension-api/permissions.d.ts. */
export const PERMISSIONS: ReadonlySet<Permission> = new Set<Permission>([
  'assistant.ask',
  'memory.read',
  'events.summary',
  'progress.read',
  'progress.report',
  'worksource.read',
  'files.read',
  'commands.run',
  'notify',
  'transcript.read',
  'toolresult.read',
  'task.control',
  'voice.control',
  'threads.rename',
  'agents.ask',
]);

/** Routes the hub's own pages use; an extension's alias may not take one. */
const CORE_ROUTES = new Set(['#/todos', '#/schedules', '#/activity', '#/skills', '#/mcp', '#/extensions']);
const ID = /^[a-z0-9][a-z0-9-]{0,39}$/;

/**
 * The manifests of the extensions that ship with Calli, by package directory under packages/.
 * Resolved from this file, so the source run (src/) and the build (dist/) find the same files.
 */
const BUILTIN_PACKAGES = ['ext-board', 'ext-memory', 'ext-voice', 'ext-autotitle'];

export function builtinManifestPaths(): string[] {
  return BUILTIN_PACKAGES.map((dir) => fileURLToPath(new URL(`../../${dir}/manifest.json`, import.meta.url)));
}

/**
 * Reads the built-in catalog. A manifest that is missing or malformed is left out with a warning:
 * one broken extension must not keep the hub from starting.
 */
export function loadCatalog(paths: string[] = builtinManifestPaths(), warn: (message: string) => void = (m) => console.warn(m)): ExtensionManifest[] {
  const out: ExtensionManifest[] = [];
  for (const path of paths) {
    try {
      const manifest = checkManifest(JSON.parse(readFileSync(path, 'utf8')));
      if (out.some((m) => m.id === manifest.id)) throw new Error(`duplicate id ${manifest.id}`);
      out.push(manifest);
    } catch (err) {
      warn(`warning: extension manifest ${path} skipped: ${err instanceof Error ? err.message : err}`);
    }
  }
  return out;
}

const isText = (v: unknown): boolean => {
  const t = v as Record<string, unknown> | null;
  return Boolean(t) && typeof t === 'object' && ['zh', 'en', 'ja'].every((k) => typeof t![k] === 'string' && (t![k] as string).length > 0);
};

/** The shape checks the host relies on. Throws with what is wrong. */
export function checkManifest(value: unknown): ExtensionManifest {
  const m = value as Partial<ExtensionManifest> | null;
  if (!m || typeof m !== 'object') throw new Error('not an object');
  if (typeof m.id !== 'string' || !ID.test(m.id)) throw new Error('id must be lowercase letters, digits and -');
  if (typeof m.version !== 'string' || !m.version) throw new Error('version missing');
  if (m.apiVersion !== 1) throw new Error(`apiVersion ${String(m.apiVersion)} is not supported`);
  if (!isText(m.name) || !isText(m.description)) throw new Error('name and description need zh, en and ja');
  if (!Array.isArray(m.permissions) || m.permissions.some((p) => !PERMISSIONS.has(p))) throw new Error('unknown permission');
  if (new Set(m.permissions).size !== m.permissions.length) throw new Error('permission listed twice');
  const views = m.contributes?.views ?? [];
  if (!m.contributes || typeof m.contributes !== 'object' || !Array.isArray(views)) throw new Error('contributes missing');
  const seen = new Set<string>();
  for (const v of views) {
    if (!v || typeof v.id !== 'string' || !ID.test(v.id) || seen.has(v.id)) throw new Error('view id missing or repeated');
    seen.add(v.id);
    if (!isText(v.title) || typeof v.icon !== 'string') throw new Error(`view ${v.id}: title and icon needed`);
    if (v.route !== undefined && (typeof v.route !== 'string' || !/^#\/[a-z0-9][a-z0-9/-]*$/.test(v.route) || CORE_ROUTES.has(v.route) || v.route.startsWith('#/ext/'))) {
      throw new Error(`view ${v.id}: route ${String(v.route)} is not allowed`);
    }
  }
  if (m.contributes.composerActions !== undefined) {
    if (!Array.isArray(m.contributes.composerActions)) throw new Error('composerActions must be a list');
    const seenActions = new Set<string>();
    for (const a of m.contributes.composerActions) {
      if (!a || typeof a.id !== 'string' || !ID.test(a.id) || seenActions.has(a.id)) throw new Error('composerAction id missing or repeated');
      seenActions.add(a.id);
      if (!isText(a.title) || typeof a.icon !== 'string') throw new Error(`composerAction ${a.id}: title and icon needed`);
    }
  }
  if (m.subscribes !== undefined && !Array.isArray(m.subscribes)) throw new Error('subscribes must be a list');
  if (m.requires !== undefined) {
    checkExtensionRequirements(m.requires);
  }
  return m as ExtensionManifest;
}

/** The permissions a request grants, when they are exactly what the manifest asks for; else null. */
export function exactGrant(manifest: ExtensionManifest, granted: unknown): Permission[] | null {
  if (!Array.isArray(granted) || granted.some((p) => typeof p !== 'string')) return null;
  const given = new Set(granted as string[]);
  if (given.size !== granted.length || given.size !== manifest.permissions.length) return null;
  return manifest.permissions.every((p) => given.has(p)) ? [...manifest.permissions] : null;
}

export * from './extension-dependencies.js';

/** Only the built-in board may defer its new assistant permission until the first handoff. */
export function activeBuiltinGrant(manifest: ExtensionManifest, granted: unknown): boolean {
  if (exactGrant(manifest, granted)) return true;
  return manifest.id === 'board' && manifest.permissions.includes('assistant.ask')
    && Boolean(exactGrant({ ...manifest, permissions: manifest.permissions.filter(p => p !== 'assistant.ask') }, granted));
}
