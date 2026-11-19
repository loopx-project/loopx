import { createHash } from 'node:crypto';
import { closeSync, existsSync, lstatSync, mkdirSync, mkdtempSync, openSync, readSync, readdirSync, readFileSync, realpathSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, normalize, resolve, relative, isAbsolute } from 'node:path';
import type { AgentAdapter } from './adapters/types.js';
import { HttpError } from './errors.js';

/**
 * Skills, managed the way every agent that follows the Agent Skills specification stores them:
 * a skill is a folder with a `SKILL.md`, sitting in folders each agent reads. The hub only reads
 * those folders; the one thing it writes is a copy of a skill into another agent's folder, and
 * only when asked to. Sessions, config and everything else an agent owns are never touched.
 *
 * Which folders an agent reads is the adapter's own knowledge (`skillDirs()`), so a second account
 * with its own config directory gets its own skills, and the shared `~/.agents/skills` shows the
 * agents that read it. Syncing between machines is not done here: the page drives it, fetching a
 * skill from one machine's daemon and handing it to another's.
 */

/** What one location (a folder of skills) says about itself. */
export interface SkillLocationInfo {
  path: string;
  scope: 'personal' | 'shared';
  /** The agent instances that read this folder. */
  agents: string[];
}

/** One skill as it stands in one place, on this machine. */
export interface SkillInPlace {
  /** The folder the skill sits in (a location), not the skill's own folder. */
  path: string;
  mtime: string;
  sizeBytes: number;
  fileCount: number;
  /** sha256 of all file contents, paths and executable bits, so two machines can tell whether they hold the same. */
  digest: string;
}

/** A skill, merged across the places it sits on this machine. */
export interface SkillSummary extends SkillInPlace {
  name: string;
  /** From the SKILL.md frontmatter; empty when it says nothing. */
  description: string;
  /** Every place it sits here, newest first, and which agents see it through each. */
  locations: Array<SkillInPlace & { agents: string[] }>;
  /** Union of the agents that see it somewhere. */
  agents: string[];
}

export interface SkillsListing {
  locations: SkillLocationInfo[];
  skills: SkillSummary[];
}

export interface SkillDetail extends SkillSummary {
  /** The SKILL.md itself, for the page to read. */
  skillMd: string;
}

/** A file of a skill, ready to travel: base64 so the payload stays JSON. */
export interface SkillFilePart {
  path: string;
  /** 0o755 when the source file was executable; the page does not invent permissions. */
  mode?: number;
  data: string;
}

/** A whole skill packed for the road, between two machines' daemons. */
export interface SkillPayload {
  name: string;
  description?: string;
  files: SkillFilePart[];
  sizeBytes: number;
  /** The same digest the listings carry, so both ends can say whether they hold the same skill. */
  digest: string;
}

export type SkillInstallStatus = 'installed' | 'exists' | 'same' | 'no_dir' | 'error';

export interface SkillInstallOutcome {
  agent: string;
  dir: string;
  status: SkillInstallStatus;
  error?: string;
}

/** The same skill everywhere: enough to keep, small enough to carry. */
const MAX_FILES = 400;
const MAX_TOTAL_BYTES = 16 * 1024 * 1024;
const MAX_FILE_BYTES = 4 * 1024 * 1024;
/** Only the SKILL.md is read for the page; instructions that long nobody reads anyway. */
const MAX_SKILL_MD = 256 * 1024;

/** What a skill folder may be called: what every agent accepts as a slash command name. */
const SKILL_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

/**
 * `name` and `description` out of a SKILL.md's frontmatter. Most skills keep it to these two, so
 * this reads just them — plain, quoted, and folded (`>`) or literal (`|`) block scalars — and
 * leaves the rest of YAML alone. Anything it cannot make out is simply not reported.
 */
export function parseFrontmatter(text: string): { name: string | null; description: string | null } {
  const match = /^\uFEFF?---\r?\n([\s\S]*?)(?:\r?\n)?---(?:\r?\n|$)/.exec(text);
  if (!match) return { name: null, description: null };
  const lines = match[1].split(/\r?\n/);
  const out: { name: string | null; description: string | null } = { name: null, description: null };
  for (let i = 0; i < lines.length; i++) {
    const top = /^([A-Za-z][\w-]*):(?:[ \t]+(.*))?$/.exec(lines[i]);
    if (!top) continue;
    const [, key, raw] = top;
    let value = (raw ?? '').trim();
    if (/^[>|][+-]?$/.test(value)) {
      // A block scalar: everything indented under it, folded to spaces (`>`) or kept as lines (`|`).
      const parts: string[] = [];
      for (i = i + 1; i < lines.length; i++) {
        if (/^\s+\S/.test(lines[i])) parts.push(lines[i].replace(/^\s+/, ''));
        else if (lines[i].trim() === '' && i + 1 < lines.length) parts.push('');
        else break;
      }
      i -= 1;
      value = (raw ?? '').startsWith('>') ? parts.join(' ') : parts.join('\n');
    } else if (value.length > 1 && ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'")))) {
      value = value.slice(1, -1);
    }
    if (key === 'name' && !out.name) out.name = value.trim();
    if (key === 'description' && out.description === null) out.description = value.trim();
  }
  return out;
}

/** One file of a skill folder: what it is, not what it holds (the payload reads the bytes). */
interface FileFact {
  rel: string;
  size: number;
  mtimeMs: number;
  executable: boolean;
}

/**
 * List regular files and links within the skill root, excluding `.git` and respecting the cap.
 * `limit` may be one above the cap, so a caller can tell "at the cap" from "past it".
 */
function walkSkill(root: string, limit = MAX_FILES, strict = false): FileFact[] {
  const out: FileFact[] = [];
  const rootPath = realpathSync(root);
  const ancestors = new Set<string>();
  const visit = (dir: string, rel: string): void => {
    const actual = realpathSync(dir);
    if (ancestors.has(actual)) throw new HttpError(400, '技能目录存在循环链接，无法同步');
    ancestors.add(actual);
    try {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (out.length >= limit) return;
        if (entry.name === '.git') continue;
        const full = join(dir, entry.name);
        const relPath = rel ? `${rel}/${entry.name}` : entry.name;
        try {
          const resolved = realpathSync(full);
          const inside = relative(rootPath, resolved);
          if (inside === '..' || inside.startsWith('../') || isAbsolute(inside)) {
            throw new HttpError(400, `技能文件链接指向目录外：${relPath}`);
          }
          const stat = statSync(full);
          if (stat.isDirectory()) visit(full, relPath);
          else if (stat.isFile()) out.push({ rel: relPath, size: stat.size, mtimeMs: stat.mtimeMs, executable: (stat.mode & 0o111) !== 0 });
          else throw new HttpError(400, `技能包含非常规文件：${relPath}`);
        } catch (err) {
          if (strict) throw err;
        }
      }
    } finally {
      ancestors.delete(actual);
    }
  };
  visit(root, '');
  return out.sort((a, b) => a.rel.localeCompare(b.rel));
}

/** Hash every byte; equally sized script edits must still count as a different version. */
function digestOnDisk(root: string, files: FileFact[]): string {
  const hash = createHash('sha256');
  const buffer = Buffer.alloc(64 * 1024);
  for (const file of files) {
    hash.update(`${file.rel}\0${file.size}\0${file.executable ? 'x' : '-'}\0`);
    const fd = openSync(join(root, file.rel), 'r');
    try {
      let count: number;
      while ((count = readSync(fd, buffer)) > 0) hash.update(buffer.subarray(0, count));
    } finally {
      closeSync(fd);
    }
  }
  return hash.digest('hex');
}

function digestOfPayload(files: SkillFilePart[]): string {
  const hash = createHash('sha256');
  for (const file of [...files].sort((a, b) => a.path.localeCompare(b.path))) {
    const bytes = Buffer.from(file.data, 'base64');
    hash.update(`${file.path}\0${bytes.length}\0${file.mode === 0o755 ? 'x' : '-'}\0`);
    hash.update(bytes);
  }
  return hash.digest('hex');
}

function placeOf(where: string, root: string, files: FileFact[]): SkillInPlace {
  const newest = files.reduce((t, f) => Math.max(t, f.mtimeMs), 0);
  return {
    path: where,
    mtime: new Date(newest).toISOString(),
    sizeBytes: files.reduce((t, f) => t + f.size, 0),
    fileCount: files.length,
    digest: digestOnDisk(root, files),
  };
}

/** A folder that holds skills, de-duplicated by absolute path: agents that share one meet in it. */
interface LocationIndexEntry extends SkillLocationInfo {
  /** Skill folders by name, as they stand in this folder. */
  skills: Map<string, SkillInPlace>;
}

export class SkillsManager {
  private cache: { at: number; value: SkillsListing } | null = null;

  constructor(private readonly adapters: Map<string, AgentAdapter>) {}

  /** Every location and every skill on this machine. Skills an agent cannot see are still listed. */
  listing(): SkillsListing {
    // A short cache keeps several devices from re-reading and hashing the
    // same folders within one breath. Anything that writes clears it.
    if (this.cache && Date.now() - this.cache.at < 3_000) return this.cache.value;
    const locations = new Map<string, LocationIndexEntry>();
    for (const [id, adapter] of this.adapters) {
      for (const dir of adapter.skillDirs?.() ?? []) {
        const path = resolve(dir.path);
        let entry = locations.get(path);
        if (!entry) {
          entry = { path, scope: dir.scope, agents: [], skills: new Map() };
          locations.set(path, entry);
        }
        if (!entry.agents.includes(id)) entry.agents.push(id);
      }
    }
    for (const entry of locations.values()) {
      let children: string[] = [];
      try {
        children = readdirSync(entry.path);
      } catch {
        continue; // not there yet: the location still shows, as a place to install into
      }
      for (const name of children) {
        if (!SKILL_NAME.test(name)) continue;
        const dir = join(entry.path, name);
        let stat;
        try {
          stat = statSync(dir, { throwIfNoEntry: false });
        } catch {
          continue;
        }
        if (!stat?.isDirectory()) continue;
        if (!existsSync(join(dir, 'SKILL.md'))) continue;
        try {
          const files = walkSkill(dir);
          if (files.some((file) => file.rel === 'SKILL.md')) entry.skills.set(name, placeOf(entry.path, dir, files));
        } catch {
          // A removed or unreadable skill must not hide every other skill on the machine.
        }
      }
    }
    const skills = new Map<string, SkillSummary>();
    for (const entry of locations.values()) {
      for (const [name, place] of entry.skills) {
        let skill = skills.get(name);
        if (!skill) {
          skill = { name, description: this.readMeta(join(entry.path, name)).description, ...place, locations: [], agents: [] };
          skills.set(name, skill);
        }
        skill.locations.push({ ...place, agents: [...entry.agents] });
        for (const id of entry.agents) if (!skill.agents.includes(id)) skill.agents.push(id);
      }
    }
    // What the skill is here is its newest copy; the mismatch the page points at is older ones.
    // The agents it reaches stay the union: a newer copy in one folder does not unsee the others.
    for (const skill of skills.values()) {
      skill.locations.sort((a, b) => (a.mtime < b.mtime ? 1 : -1));
      const { agents: _reachedThrough, ...newest } = skill.locations[0];
      Object.assign(skill, newest);
      skill.description = this.readMeta(join(skill.path, skill.name)).description;
    }
    const value: SkillsListing = {
      locations: [...locations.values()].map(({ path, scope, agents }) => ({ path, scope, agents: [...agents] })),
      skills: [...skills.values()].sort((a, b) => a.name.localeCompare(b.name)),
    };
    this.cache = { at: Date.now(), value };
    return value;
  }

  /** The SKILL.md of one skill, for reading on the page. */
  detail(name: string, path?: string | null): SkillDetail {
    const skill = this.find(name, path);
    // `find` narrowed the skill to the copy asked about (`path`), so that is the one to read.
    return { ...skill, skillMd: this.readMeta(join(skill.path, skill.name), MAX_SKILL_MD).text };
  }

  /**
   * A skill packed for moving to another machine. What the folders hold is what travels: the
   * payload carries no paths beyond the skill's own files, so nothing of one machine's layout
   * reaches another's.
   */
  payload(name: string, path?: string | null): SkillPayload {
    const skill = this.find(name, path);
    const root = join(skill.path, skill.name);
    const files = walkSkill(root, MAX_FILES + 1, true);
    const total = files.reduce((t, f) => t + f.size, 0);
    if (files.length > MAX_FILES) throw new HttpError(413, `技能 ${skill.name} 的文件超过 ${MAX_FILES} 个，不同步`);
    if (total > MAX_TOTAL_BYTES) throw new HttpError(413, `技能 ${skill.name} 有 ${(total / 1024 / 1024).toFixed(1)} MB，超过 16 MB，不同步`);
    const parts: SkillFilePart[] = [];
    for (const f of files) {
      if (f.size > MAX_FILE_BYTES) throw new HttpError(413, `${skill.name} 里的 ${f.rel} 超过 4 MB，不同步`);
      parts.push({
        path: f.rel,
        ...(f.executable ? { mode: 0o755 } : {}),
        data: readFileSync(join(root, f.rel)).toString('base64'),
      });
    }
    return { name: skill.name, ...(skill.description ? { description: skill.description } : {}), files: parts, sizeBytes: total, digest: digestOfPayload(parts) };
  }

  /**
   * Copies a skill into the folders of agent instances: from another folder on this machine, or
   * from a payload another machine's daemon packed. Existing skills are never touched unless
   * `overwrite` says so; a copy of what is already there is reported, not redone.
   */
  async install(input: {
    from?: { name?: unknown; path?: unknown } | null;
    skill?: { name?: unknown; description?: unknown; files?: unknown } | null;
    targets?: unknown;
    overwrite?: unknown;
  }): Promise<SkillInstallOutcome[]> {
    if (!input || typeof input !== 'object') throw new HttpError(400, '缺少同步参数');
    const source = input.from
      ? this.payload(String(input.from.name ?? ''), input.from.path ? String(input.from.path) : null)
      : validatePayload(input.skill);
    const targets = Array.isArray(input.targets) ? input.targets.map((t) => String(t)) : [];
    if (targets.length === 0) throw new HttpError(400, '要选至少一个 agent');
    const overwrite = input.overwrite === true;
    const out: SkillInstallOutcome[] = [];
    for (const agent of targets) {
      const adapter = this.adapters.get(agent);
      const dir = adapter?.skillDirs?.().find((d) => d.scope === 'personal');
      if (!dir) {
        out.push({ agent, dir: '', status: 'no_dir', error: `${agent} 的技能目录还不支持` });
        continue;
      }
      const target = join(dir.path, source.name);
      out.push(this.writeSkill(agent, target, source, overwrite));
    }
    this.cache = null;
    return out;
  }

  /** Takes a skill out of one folder. The folder itself, and everything else in it, stays. */
  remove(name: string, path: string | null): void {
    if (typeof name !== 'string' || !SKILL_NAME.test(name)) throw new HttpError(400, '不是技能名');
    const entry = this.listing().locations.find((l) => l.path === resolve(path ?? ''));
    if (!entry) throw new HttpError(404, '这个目录不在管理范围内');
    const dir = join(entry.path, name);
    if (!existsSync(join(dir, 'SKILL.md'))) throw new HttpError(404, `这里没有技能 ${name}`);
    rmSync(dir, { recursive: true, force: true });
    this.cache = null;
  }

  private writeSkill(agent: string, target: string, source: SkillPayload, overwrite: boolean): SkillInstallOutcome {
    let stage: string | undefined;
    let backup: string | undefined;
    try {
      const existing = lstatSync(target, { throwIfNoEntry: false });
      if (existing) {
        if (existsSync(target) && digestOnDisk(target, walkSkill(target, MAX_FILES + 1, true)) === source.digest) return { agent, dir: target, status: 'same' };
        if (!overwrite) return { agent, dir: target, status: 'exists' };
      }
      mkdirSync(dirname(target), { recursive: true });
      stage = mkdtempSync(join(dirname(target), '.skill-stage-'));
      const next = join(stage, 'next');
      mkdirSync(next);
      for (const file of source.files) {
        const full = join(next, file.path);
        mkdirSync(dirname(full), { recursive: true });
        writeFileSync(full, Buffer.from(file.data, 'base64'), { mode: file.mode === 0o755 ? 0o755 : 0o644 });
      }
      if (existing) {
        backup = join(stage, 'previous');
        renameSync(target, backup);
      }
      try {
        renameSync(next, target);
      } catch (err) {
        if (backup) renameSync(backup, target);
        throw err;
      }
      return { agent, dir: target, status: 'installed' };
    } catch (err) {
      return { agent, dir: target, status: 'error', error: err instanceof Error ? err.message : String(err) };
    } finally {
      if (stage && (!backup || existsSync(target))) rmSync(stage, { recursive: true, force: true });
    }
  }

  private find(name: string, path?: string | null): SkillSummary {
    if (typeof name !== 'string' || !SKILL_NAME.test(name)) throw new HttpError(400, '不是技能名');
    const skill = this.listing().skills.find((s) => s.name === name);
    if (!skill) throw new HttpError(404, `没有技能 ${name}`);
    if (path) {
      const wanted = resolve(path);
      const place = skill.locations.find((l) => resolve(l.path) === wanted);
      if (!place) throw new HttpError(404, `${name} 不在 ${path}`);
      // The named copy is the one asked about; the summary keeps every place it sits.
      return { ...skill, ...place };
    }
    return skill;
  }

  /** The frontmatter of one copy, read where it stands. */
  private readMeta(dir: string, maxBytes = 64 * 1024): { description: string; text: string } {
    try {
      const text = readFileSync(join(dir, 'SKILL.md')).subarray(0, maxBytes).toString('utf8');
      return { description: parseFrontmatter(text).description ?? '', text };
    } catch {
      return { description: '', text: '' };
    }
  }
}

/** A payload that arrived over the network is checked like it was written by a stranger. */
function validatePayload(input: { name?: unknown; description?: unknown; files?: unknown } | null | undefined): SkillPayload {
  if (!input || typeof input !== 'object') throw new HttpError(400, '缺少技能内容');
  const name = String(input.name ?? '');
  if (!SKILL_NAME.test(name)) throw new HttpError(400, `不是技能名: ${name || '(空)'}`);
  const description = typeof input.description === 'string' ? input.description.slice(0, 2000) : undefined;
  if (!Array.isArray(input.files) || input.files.length === 0) throw new HttpError(400, `${name} 没有文件`);
  if (input.files.length > MAX_FILES) throw new HttpError(413, `${name} 的文件超过 ${MAX_FILES} 个`);
  const files: SkillFilePart[] = [];
  const seen = new Set<string>();
  let total = 0;
  for (const raw of input.files) {
    if (!raw || typeof raw !== 'object') throw new HttpError(400, `${name} 的文件列表不对`);
    const given = String((raw as { path?: unknown }).path ?? '');
    if (!given || given.length > 200 || given.startsWith('/') || given.includes('\\') || given.split('/').some((part) => part === '.git' || part === '.' || part === '..' || part === '')) {
      throw new HttpError(400, `文件路径不对: ${given || '(空)'}`);
    }
    const rel = normalize(given);
    if (rel.startsWith('..') || rel.split('/').includes('..') || rel !== given) {
      throw new HttpError(400, `文件路径不对: ${given}`);
    }
    if (seen.has(rel)) throw new HttpError(400, `${name} 里有两个 ${rel}`);
    seen.add(rel);
    const data = String((raw as { data?: unknown }).data ?? '');
    const size = Buffer.byteLength(data, 'base64');
    if (size > MAX_FILE_BYTES) throw new HttpError(413, `${name} 里的 ${rel} 超过 4 MB`);
    if (Buffer.from(data, 'base64').toString('base64') !== data) throw new HttpError(400, `文件编码不对：${rel}`);
    total += size;
    if (total > MAX_TOTAL_BYTES) throw new HttpError(413, `${name} 超过 16 MB，不安装`);
    files.push({ path: rel, ...((raw as { mode?: unknown }).mode === 0o755 ? { mode: 0o755 } : {}), data });
  }
  if (!seen.has('SKILL.md')) throw new HttpError(400, '技能缺少 SKILL.md');
  for (const path of seen) {
    const parts = path.split('/');
    for (let i = 1; i < parts.length; i++) {
      if (seen.has(parts.slice(0, i).join('/'))) throw new HttpError(400, '文件与目录路径冲突');
    }
  }
  return { name, ...(description ? { description } : {}), files, sizeBytes: total, digest: digestOfPayload(files) };
}
