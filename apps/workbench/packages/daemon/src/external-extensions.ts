import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { lstat, mkdir, readFile, readdir, copyFile, realpath } from 'node:fs/promises';
import { dirname, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { t as listTar, x as extractTar } from 'tar';
import type { ExtensionConfiguration, ExtensionDetection, ExtensionManifest, ExtensionPackagePreview, ExtensionState, WorkBinding, WorkProjection, WorkSnapshot } from '@calli/extension-api';
import { checkManifest, exactGrant } from './extensions.js';
import { HttpError } from './errors.js';
import type { HostSource } from './extension-host.js';
import { ExtensionProcess, runExtensionCommand } from './extension-process.js';

interface Artifact { manifest: ExtensionManifest; dir: string; origin: string; digest: string }
interface Installed extends Artifact { enabled: boolean; granted: string[]; installedAt: string; updatedAt: string; config: ExtensionConfiguration; previous?: Artifact }
const MAX_PACKAGE = 20 * 1024 * 1024;
const keyPattern = /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/;
const fail = (message: string): never => { throw new HttpError(400, message); };
const safeRelative = (path: string) => path.length > 0 && !isAbsolute(path) && !path.includes('\\') && !path.split('/').includes('..');
const strings = (value: unknown): value is Record<string, string> => Boolean(value) && typeof value === 'object' && !Array.isArray(value) && Object.values(value!).every(v => typeof v === 'string' && v.length <= 4096);

/** Discovery reads manifests only; it never installs or starts a supported package. */
export function loadSupportedExternalCatalog(): { manifest: ExtensionManifest; origin: string }[] {
  return ['ext-loopx'].flatMap(name => {
    const origin = fileURLToPath(new URL(`../../${name}/`, import.meta.url));
    try { return [{ manifest: externalManifest(JSON.parse(readFileSync(join(origin, 'manifest.json'), 'utf8'))), origin }]; }
    catch { console.warn(`Supported extension package unavailable: ${name}`); return []; }
  });
}

export function externalManifest(value: unknown): ExtensionManifest {
  const m = checkManifest(value);
  if (!/^\d+\.\d+\.\d+(?:-[\w.-]+)?$/.test(m.version)) fail('version must be semver');
  if (!m.process || !safeRelative(m.process.entry) || !m.process.entry.endsWith('.mjs')) fail('process.entry must name a relative .mjs file');
  if (!m.contributes.workSource || m.contributes.views?.length) fail('external v1 packages contribute a workSource, not browser code');
  if (!Array.isArray(m.answers) || m.answers.some(h => !['extension.detect', 'extension.validateSettings', 'worksource.snapshot'].includes(h))) fail('unsupported request hook');
  if (!m.answers!.includes('worksource.snapshot') || !m.answers!.includes('extension.validateSettings')) fail('snapshot and settings validation hooks are required');
  if (m.subscribes?.some(h => !['extension.activated', 'extension.deactivating', 'extension.settingsChanged'].includes(h))) fail('external event subscriptions are not supported yet');
  if (m.permissions.some(p => !['commands.run', 'worksource.read'].includes(p))) fail('unsupported external permission');
  if (!m.permissions.includes('commands.run') || !m.permissions.includes('worksource.read')) fail('commands.run and worksource.read are required');
  const keys = new Set<string>();
  if (!Array.isArray(m.settings) || !Array.isArray(m.commands) || m.commands.length > 20) fail('settings and commands are required');
  for (const field of m.settings!) {
    if (!field || !keyPattern.test(field.key) || keys.has(field.key) || !['installation', 'binding'].includes(field.scope) || !field.label || !['zh', 'en', 'ja'].every(k => typeof (field.label as any)[k] === 'string')) fail('invalid setting');
    if (field.kind && !['text', 'path'].includes(field.kind)) fail('invalid setting kind');
    if (field.default !== undefined && typeof field.default !== 'string') fail('invalid setting default');
    keys.add(field.key);
  }
  const commands = new Set<string>();
  for (const c of m.commands!) {
    if (!c || !keyPattern.test(c.id) || commands.has(c.id) || !['installation', 'binding'].includes(c.scope) || !['read', 'runtime'].includes(c.effect)) fail('invalid command');
    commands.add(c.id);
    const executable = c.executable;
    if (typeof executable !== 'string' && (!executable || !m.settings!.some(f => f.key === executable.setting && f.scope === 'installation'))) fail('invalid executable');
    if (!Array.isArray(c.args) || c.args.length > 100 || c.args.some(a => typeof a !== 'string' && (!a || !('setting' in a && keys.has(a.setting)) && !('binding' in a && a.binding === 'externalId')))) fail('invalid arguments');
    if (c.env && (!strings(c.env) || Object.keys(c.env).some(k => !/^[A-Z][A-Z0-9_]*$/.test(k) || /^(CALLI_|RAH_|LIMI_|NODE_OPTIONS$|LD_|DYLD_)/.test(k)))) fail('invalid command environment');
    if (c.timeoutMs !== undefined && (!Number.isInteger(c.timeoutMs) || c.timeoutMs < 100 || c.timeoutMs > 20000)) fail('invalid timeout');
  }
  return m;
}

function cleanSnapshot(value: any): WorkSnapshot {
  if (!value || typeof value.title !== 'string' || typeof value.status !== 'string' || typeof value.complete !== 'boolean' || !Array.isArray(value.items) || value.items.length > 500) throw new Error('invalid_snapshot');
  const text = (v: unknown, max = 8000) => typeof v === 'string' ? v.slice(0, max) : undefined;
  const ids = new Set<string>();
  const items = value.items.map((i: any) => {
    if (!i || typeof i.id !== 'string' || !i.id || i.id.length > 200 || ids.has(i.id) || typeof i.text !== 'string' || typeof i.status !== 'string') throw new Error('invalid_work_item');
    ids.add(i.id);
    return { id: i.id.slice(0, 200), text: text(i.text)!, status: text(i.status, 200)! };
  });
  return { title: text(value.title)!, status: text(value.status, 200)!, ...(value.statusLabel && ['zh', 'en', 'ja'].every(k => typeof value.statusLabel[k] === 'string') ? { statusLabel: { zh: text(value.statusLabel.zh, 200)!, en: text(value.statusLabel.en, 200)!, ja: text(value.statusLabel.ja, 200)! } } : {}), items, complete: value.complete, summary: text(value.summary), evidence: text(value.evidence), ...(typeof value.updatedAt === 'string' && Number.isFinite(Date.parse(value.updatedAt)) ? { updatedAt: value.updatedAt } : {}) };
}

/** Optional process extensions. All persisted state lives in the hub's own data directory. */
export class ExternalExtensions {
  private records = new Map<string, Installed>();
  private previews = new Map<string, { artifact: Artifact; expires: number }>();
  private processes = new Map<string, ExtensionProcess>();
  private snapshots = new Map<string, WorkProjection>();
  private pending = new Map<string, Promise<void>>();
  private revisions = new Map<string, number>();
  private lastAttempt = new Map<string, number>();
  private refreshTails = new Map<string, Promise<void>>();
  private off: (() => void) | null = null;
  private closed = false;
  constructor(private root: string, private source: HostSource, private generation: string, private changed: () => void, private builtinIds: () => string[]) {
    mkdirSync(root, { recursive: true, mode: 0o700 });
    try {
      const rows = JSON.parse(readFileSync(join(root, 'installed.json'), 'utf8')) as [string, Installed][];
      for (const [id, r] of rows) {
        externalManifest(r.manifest);
        if (typeof r.enabled !== 'boolean' || !exactGrant(r.manifest, r.granted) || !r.config || !strings(r.config.settings) || !Array.isArray(r.config.bindings)) continue;
        if (id !== r.manifest.id || !this.owned(r.dir) || !existsSync(join(r.dir, r.manifest.process!.entry))) continue;
        this.records.set(id, r);
      }
    } catch (e) { if (existsSync(join(root, 'installed.json'))) console.warn('External extension registry could not be read'); }
  }
  private owned(path: string): boolean { return resolve(path).startsWith(resolve(this.root, 'packages') + sep); }
  private save(): void {
    const path = join(this.root, 'installed.json');
    writeFileSync(path + '.tmp', JSON.stringify([...this.records]), { mode: 0o600 });
    renameSync(path + '.tmp', path);
  }
  start(): void {
    this.closed = false;
    this.off = this.source.subscribe(event => {
      if (!['turn.done', 'turn.error', 'thread.updated'].includes(event.type)) return;
      for (const [id, record] of this.records) for (const binding of record.config.bindings) {
        if (binding.threadIds.includes(event.threadId) && record.enabled) void this.refresh(id, binding.id, false);
      }
    });
    for (const [id, record] of this.records) if (record.enabled) for (const b of record.config.bindings) void this.refresh(id, b.id, false);
  }
  dispose(): void { this.closed = true; this.off?.(); for (const id of this.records.keys()) this.stop(id); }
  has(id: string): boolean { return this.records.has(id); }
  list(): ExtensionState[] { return [...this.records.values()].map(r => this.state(r)); }
  private state(r: Installed): ExtensionState {
    return { manifest: r.manifest, source: 'external', installed: true, enabled: r.enabled, granted: r.granted as ExtensionState['granted'], installedAt: r.installedAt, updatedAt: r.updatedAt, origin: r.origin, digest: r.digest, previousVersion: r.previous?.manifest.version };
  }
  private record(id: string): Installed { const r = this.records.get(id); if (!r) throw new HttpError(404, 'extension_not_found'); return r; }
  private stop(id: string): void {
    this.revisions.set(id, (this.revisions.get(id) ?? 0) + 1);
    this.refreshTails.delete(id);
    this.processes.get(id)?.notify('extension.deactivating', { extensionId: id });
    this.processes.get(id)?.stop(); this.processes.delete(id);
    for (const key of this.pending.keys()) if (key.startsWith(id + '/')) this.pending.delete(key);
    for (const key of this.lastAttempt.keys()) if (key.startsWith(id + '/')) this.lastAttempt.delete(key);
    for (const [key, value] of this.snapshots) if (value.sourceId === id) this.snapshots.set(key, { ...value, freshness: 'unavailable' });
  }
  async prepare(origin: string, expectedDigest?: string): Promise<ExtensionPackagePreview> {
    if (typeof origin !== 'string' || !origin.trim()) fail('package source required');
    for (const [token, p] of this.previews) if (p.expires < Date.now()) { rmSync(p.artifact.dir, { recursive: true, force: true }); this.previews.delete(token); }
    if (this.previews.size >= 10) fail('too many pending installs');
    const token = randomUUID(), dir = join(this.root, 'packages', token);
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    try {
      if (/^https:\/\//.test(origin)) {
        if (!/^[a-f0-9]{64}$/i.test(expectedDigest ?? '')) fail('SHA-256 required for archive URL');
        const url = new URL(origin);
        if (url.username || url.password || url.search) fail('archive URL must not include credentials or query parameters');
        const response = await fetch(url, { signal: AbortSignal.timeout(30000) });
        if (!response.ok || !response.body) fail('archive download failed');
        const chunks: Uint8Array[] = []; let size = 0;
        for await (const chunk of response.body as any) { size += chunk.length; if (size > MAX_PACKAGE) fail('package too large'); chunks.push(chunk); }
        const bytes = Buffer.concat(chunks);
        if (createHash('sha256').update(bytes).digest('hex') !== expectedDigest!.toLowerCase()) fail('archive checksum mismatch');
        const archive = join(dir, 'download.tgz'); writeFileSync(archive, bytes);
        let total = 0, count = 0, invalid = false;
        await listTar({ file: archive, onReadEntry: e => {
          if (!['File', 'Directory'].includes(e.type) || !safeRelative(e.path) || (total += e.size) > MAX_PACKAGE || ++count > 1000) invalid = true;
        } });
        if (invalid) fail('unsafe archive entry');
        await extractTar({ file: archive, cwd: dir, strict: true, noChmod: true });
        rmSync(archive);
        if (existsSync(join(dir, 'package', 'manifest.json'))) {
          for (const name of readdirSync(join(dir, 'package'))) renameSync(join(dir, 'package', name), join(dir, name));
          rmSync(join(dir, 'package'), { recursive: true });
        }
      } else {
        if (!isAbsolute(origin)) fail('local package path must be absolute on the daemon machine');
        const from = await realpath(origin); let total = 0, count = 0;
        const copy = async (src: string, dst: string): Promise<void> => {
          for (const name of await readdir(src)) {
            if (['node_modules', '.git'].includes(name)) continue;
            const a = join(src, name), b = join(dst, name), st = await lstat(a);
            if (++count > 1000 || st.isSymbolicLink()) fail('package contains links or too many files');
            if (st.isDirectory()) { await mkdir(b); await copy(a, b); }
            else if (st.isFile()) { total += st.size; if (total > MAX_PACKAGE) fail('package too large'); await copyFile(a, b); }
            else fail('package contains special files');
          }
        };
        await copy(from, dir);
      }
      const manifest = externalManifest(JSON.parse(await readFile(join(dir, 'manifest.json'), 'utf8')));
      if (this.builtinIds().includes(manifest.id)) fail('cannot replace a built-in extension');
      if (!(await lstat(join(dir, manifest.process!.entry))).isFile()) fail('entry missing');
      const hash = createHash('sha256');
      const hashFiles = async (folder: string): Promise<void> => { for (const name of (await readdir(folder)).sort()) { const path = join(folder, name); if ((await lstat(path)).isDirectory()) await hashFiles(path); else { hash.update(relative(dir, path)); hash.update(await readFile(path)); } } };
      await hashFiles(dir);
      const artifact = { manifest, dir, origin, digest: hash.digest('hex') };
      this.previews.set(token, { artifact, expires: Date.now() + 600000 });
      return { token, manifest, origin, digest: artifact.digest };
    } catch (e) { rmSync(dir, { recursive: true, force: true }); throw e; }
  }
  async install(token: string, granted: unknown): Promise<ExtensionState> {
    const p = this.previews.get(token);
    if (!p || p.expires < Date.now()) fail('package preview expired');
    const artifact = p!.artifact, m = artifact.manifest;
    const grant = exactGrant(m, granted); if (!grant) fail('permissions must match the preview');
    const old = this.records.get(m.id), now = new Date().toISOString();
    const candidateConfig = this.validateConfig({ ...artifact, config: old?.config ?? { settings: {}, bindings: [] } } as Installed, old?.config ?? { settings: {}, bindings: [] });
    this.previews.delete(token);
    const probe = new ExtensionProcess(artifact.dir, m.process!.entry, async () => { throw new Error('commands_unavailable_during_install'); });
    try {
      const result = await probe.request('extension.validateSettings', { settings: candidateConfig.settings });
      if (!result || !result.errors || typeof result.errors !== 'object' || Object.keys(result.errors).length) fail('extension validation failed');
      if (this.records.get(m.id) !== old) fail('extension changed during install; inspect again');
    } catch (e) { rmSync(artifact.dir, { recursive: true, force: true }); throw e; }
    finally { probe.stop(); }
    this.stop(m.id);
    const next: Installed = { ...artifact, enabled: true, granted: grant!, installedAt: old?.installedAt ?? now, updatedAt: now, config: old?.config ?? { settings: {}, bindings: [] }, previous: old ? { manifest: old.manifest, dir: old.dir, origin: old.origin, digest: old.digest } : undefined };
    next.config = candidateConfig;
    this.records.set(m.id, next); this.save(); this.previews.delete(token); this.changed();
    if (old?.previous && this.owned(old.previous.dir)) rmSync(old.previous.dir, { recursive: true, force: true });
    for (const b of next.config.bindings) void this.refresh(m.id, b.id, false);
    return this.state(next);
  }
  rollback(id: string): ExtensionState {
    const old = this.record(id); if (!old.previous) fail('no previous version');
    // A rollback may restore a broader permission set: explicit install confirmation is required then.
    if (!exactGrant(old.previous!.manifest, old.granted)) fail('rollback requires reinstalling the previous package and confirming permissions');
    const previous = old.previous!;
    const next = { ...old, ...previous, previous: { manifest: old.manifest, dir: old.dir, origin: old.origin, digest: old.digest }, updatedAt: new Date().toISOString() };
    next.config = this.validateConfig(next, next.config);
    this.stop(id); this.records.set(id, next); this.save(); this.changed(); return this.state(next);
  }
  setEnabled(id: string, enabled: unknown): ExtensionState {
    if (typeof enabled !== 'boolean') fail('enabled must be boolean');
    const r = this.record(id); this.stop(id); r.enabled = enabled as boolean; r.updatedAt = new Date().toISOString(); this.save(); this.changed();
    if (r.enabled) for (const b of r.config.bindings) void this.refresh(id, b.id, false);
    return this.state(r);
  }
  uninstall(id: string): void {
    const r = this.record(id); this.stop(id); this.records.delete(id); this.save();
    for (const dir of [r.dir, r.previous?.dir]) if (dir && this.owned(dir)) rmSync(dir, { recursive: true, force: true });
    for (const [key, p] of this.snapshots) if (p.sourceId === id) this.snapshots.delete(key);
    this.changed();
  }
  configuration(id: string): ExtensionConfiguration { return structuredClone(this.record(id).config); }
  private validateConfig(record: Installed, input: any): ExtensionConfiguration {
    if (!input || !strings(input.settings) || !Array.isArray(input.bindings) || input.bindings.length > 100) fail('invalid configuration');
    const checkSettings = (settings: Record<string, string>, scope: string) => {
      for (const key of Object.keys(settings)) if (!record.manifest.settings!.some(f => f.key === key && f.scope === scope)) fail('unknown setting');
      for (const f of record.manifest.settings!.filter(f => f.scope === scope)) {
        const value = settings[f.key] || f.default;
        if (f.required && !value) fail('required setting missing');
        if (value && f.kind === 'path' && !isAbsolute(value)) fail('setting requires an absolute path');
      }
    };
    checkSettings(input.settings, 'installation');
    const ids = new Set<string>();
    for (const b of input.bindings as WorkBinding[]) {
      if (!b || typeof b.id !== 'string' || !keyPattern.test(b.id) || ids.has(b.id) || typeof b.externalId !== 'string' || !b.externalId.trim() || b.externalId.length > 200 || !strings(b.settings) || !Array.isArray(b.threadIds) || !b.threadIds.length || !Array.isArray(b.consumers) || b.consumers.some(c => typeof c !== 'string' || !this.builtinIds().includes(c))) fail('invalid binding');
      ids.add(b.id); checkSettings(b.settings, 'binding');
      for (const id of b.threadIds) { if (typeof id !== 'string') fail('invalid thread'); try { this.source.threadView(id); } catch { fail('thread not found'); } }
      if (b.url) { let url: URL; try { url = new URL(b.url); } catch { fail('invalid URL'); } if (!['http:', 'https:'].includes(url!.protocol) || url!.username || url!.password) fail('invalid URL'); }
    }
    return structuredClone(input);
  }
  async configure(id: string, input: unknown): Promise<ExtensionConfiguration> {
    const r = this.record(id), next = this.validateConfig(r, input);
    if (r.enabled && r.manifest.answers?.includes('extension.validateSettings')) {
      const result = await this.process(id).request('extension.validateSettings', { settings: next.settings });
      if (!result || typeof result.errors !== 'object' || Object.keys(result.errors).length) fail('extension settings invalid');
    }
    this.stop(id); r.config = next; r.updatedAt = new Date().toISOString();
    for (const [key, p] of this.snapshots) if (p.sourceId === id) this.snapshots.delete(key);
    this.save(); this.changed();
    if (r.enabled) { this.process(id).notify('extension.settingsChanged', { extensionId: id }); for (const b of next.bindings) void this.refresh(id, b.id, false); }
    return structuredClone(next);
  }
  private process(id: string): ExtensionProcess {
    const r = this.record(id); if (this.closed || !r.enabled) throw new HttpError(409, 'extension_disabled');
    let p = this.processes.get(id);
    if (!p) {
      p = new ExtensionProcess(r.dir, r.manifest.process!.entry, async (_method, params, signal) => {
        const current = this.record(id);
        if (!current.enabled || !current.granted.includes('commands.run')) throw new Error('not_authorized');
        const c = current.manifest.commands!.find(c => c.id === params?.command); if (!c) throw new Error('command_not_declared');
        const binding = current.config.bindings.find(b => b.id === params?.bindingId);
        if (c.scope === 'binding' && !binding) throw new Error('binding_required');
        const settings = Object.fromEntries(current.manifest.settings!.map(f => [f.key, (f.scope === 'installation' ? current.config.settings[f.key] : binding?.settings[f.key]) ?? f.default ?? '']));
        const executable = typeof c.executable === 'string' ? c.executable : settings[c.executable.setting];
        if (!executable || executable.includes('\0')) throw new Error('invalid_executable');
        const args = c.args.map(a => typeof a === 'string' ? a : 'setting' in a ? settings[a.setting] : binding!.externalId);
        if (args.some(a => typeof a !== 'string' || a.includes('\0'))) throw new Error('invalid_argument');
        const cwd = binding ? this.source.threadView(binding.threadIds[0]!).cwd : current.dir;
        return runExtensionCommand(executable, args, cwd, c.env ?? {}, signal, c.timeoutMs);
      });
      this.processes.set(id, p);
      p.notify('extension.activated', { extensionId: id, generation: this.generation });
    }
    return p;
  }
  async detect(id: string): Promise<ExtensionDetection> {
    const r = this.record(id); if (!r.manifest.answers?.includes('extension.detect')) return { ok: false, missing: 'detection_not_supported' };
    const result = await this.process(id).request('extension.detect', {});
    if (!result || typeof result.ok !== 'boolean') throw new HttpError(502, 'invalid_detection');
    return { ok: result.ok, version: typeof result.version === 'string' ? result.version.slice(0, 100) : undefined, missing: typeof result.missing === 'string' ? result.missing.slice(0, 200) : undefined };
  }
  private key(id: string, bindingId: string): string { return id + '/' + bindingId; }
  private refresh(id: string, bindingId: string, force: boolean): Promise<void> {
    const key = this.key(id, bindingId), record = this.record(id), binding = record.config.bindings.find(b => b.id === bindingId);
    if (!record.enabled || !binding || this.closed) return Promise.resolve();
    const prior = this.snapshots.get(key);
    if (this.pending.has(key)) return this.pending.get(key)!;
    if (!force && Date.now() - (this.lastAttempt.get(key) ?? 0) < 5000) return Promise.resolve();
    this.lastAttempt.set(key, Date.now());
    const revision = this.revisions.get(id) ?? 0;
    const base: WorkProjection = { sourceId: id, sourceName: record.manifest.name, binding, snapshot: prior?.snapshot ?? null, freshness: 'unknown', observedAt: prior?.observedAt ?? null, generation: this.generation, revision: (prior?.revision ?? 0) + 1 };
    const work = (this.refreshTails.get(id) ?? Promise.resolve()).catch(() => {}).then(async () => {
      if ((this.revisions.get(id) ?? 0) !== revision || this.closed) return;
      try {
        const result = await this.process(id).request('worksource.snapshot', { binding });
        const snapshot = cleanSnapshot(result);
        if ((this.revisions.get(id) ?? 0) !== revision || this.closed) return;
        this.snapshots.set(key, { ...base, snapshot, freshness: 'fresh', observedAt: new Date().toISOString() });
      } catch (e) {
        if ((this.revisions.get(id) ?? 0) !== revision || this.closed) return;
        this.snapshots.set(key, { ...base, freshness: prior?.snapshot ? 'stale' : 'unknown', error: e instanceof Error ? e.message.slice(0, 200) : 'source_unavailable' });
      }
    });
    this.refreshTails.set(id, work);
    this.pending.set(key, work);
    void work.finally(() => { if (this.pending.get(key) === work) this.pending.delete(key); });
    return work;
  }
  async projections(consumer?: string, threadId?: string, refresh = false): Promise<WorkProjection[]> {
    if (refresh) {
      const jobs: Promise<void>[] = [];
      for (const [id, r] of this.records) for (const b of r.config.bindings) {
        if ((!threadId || b.threadIds.includes(threadId)) && (!consumer || b.consumers.includes(consumer))) jobs.push(this.refresh(id, b.id, true));
      }
      await Promise.all(jobs);
    }
    const out: WorkProjection[] = [];
    for (const [id, r] of this.records) for (const b of r.config.bindings) {
      if (threadId && !b.threadIds.includes(threadId)) continue;
      if (consumer && !b.consumers.includes(consumer)) continue;
      if (b.threadIds.some(id => { try { this.source.threadView(id); return false; } catch { return true; } })) continue;
      const p = this.snapshots.get(this.key(id, b.id));
      const binding = structuredClone(b);
      // Views receive scope and display URL, never executable/registry configuration.
      binding.settings = {}; binding.consumers = [];
      out.push(p ? { ...p, binding, freshness: !r.enabled ? 'unavailable' : p.freshness === 'fresh' && Date.now() - Date.parse(p.observedAt!) > 90000 ? 'stale' : p.freshness } : { sourceId: id, sourceName: r.manifest.name, binding, snapshot: null, freshness: r.enabled ? 'unknown' : 'unavailable', observedAt: null, generation: this.generation, revision: 0 });
    }
    return out;
  }
}
