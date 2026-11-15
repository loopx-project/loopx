import { readdirSync, statSync } from 'node:fs';
import { readdir, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { basename, join } from 'node:path';
import { configDirectory } from './session-location.js';

/**
 * Token usage off the transcript files the agents themselves write (persona, docs/server/machines.md
 * 「Token usage」): Claude Code puts each reply's usage on its assistant lines — main and subagent
 * transcripts in every config directory — and Codex (and its compatible CLIs) a token_count event
 * per request in its rollouts. Everything here reads files only, never starts an agent.
 */

/** One reply's tokens, in the four kinds the charts stack. */
export interface TranscriptUsage {
  /** Identifies the reply across files, so a fork's copy of it is not counted again. */
  reply: string;
  /** Epoch milliseconds, when the agent says. */
  at: number;
  model: string;
  input: number;
  cacheWrite: number;
  cacheRead: number;
  output: number;
}

/** A Claude Code assistant line with usage. Null for anything else (user lines, thoughts, errors). */
export function parseClaudeUsageLine(line: string): TranscriptUsage | null {
  // Cheap gate before JSON.parse: usage lines are small but pictures make some lines megabytes.
  if (!line.includes('"usage"') || !line.includes('"assistant"')) return null;
  let parsed: any;
  try {
    parsed = JSON.parse(line);
  } catch {
    return null;
  }
  if (parsed?.type !== 'assistant' || !parsed.message) return null;
  const usage = parsed.message.usage;
  const id = typeof parsed.message.id === 'string' ? parsed.message.id : '';
  if (!usage) return null;
  const at = Date.parse(parsed.timestamp ?? '');
  if (!Number.isFinite(at)) return null;
  return {
    // An empty id cannot tell a copy from the original; the meter counts it as it comes.
    reply: id ? `claude:${id}` : '',
    at,
    model: typeof parsed.message.model === 'string' ? parsed.message.model : '',
    input: num(usage.input_tokens),
    cacheWrite: num(usage.cache_creation_input_tokens),
    cacheRead: num(usage.cache_read_input_tokens),
    output: num(usage.output_tokens),
  };
}

/** A Codex token_count event, its request's tokens taken from `last_token_usage`. */
export function parseCodexTokenCount(line: string): TranscriptUsage | null {
  if (!line.includes('"token_count"')) return null;
  let parsed: any;
  try {
    parsed = JSON.parse(line);
  } catch {
    return null;
  }
  const info = parsed?.payload?.info;
  if (parsed?.payload?.type !== 'token_count' || !info?.last_token_usage) return null;
  const at = Date.parse(parsed.timestamp ?? '');
  if (!Number.isFinite(at)) return null;
  const last = info.last_token_usage;
  // Codex's input includes the cached part; that part is cache read, the rest is input. Codex
  // writes no cache of its own, so cache write stays 0.
  const cached = num(last.cached_input_tokens);
  return {
    reply: '',
    at,
    model: '',
    input: Math.max(num(last.input_tokens) - cached, 0),
    cacheWrite: 0,
    cacheRead: cached,
    output: num(last.output_tokens),
  };
}

/** The model a Codex rollout is running, from its latest turn_context line. */
export function parseCodexModel(line: string): string | null {
  if (!line.includes('"turn_context"')) return null;
  try {
    const parsed = JSON.parse(line);
    const model = parsed?.payload?.model;
    return typeof model === 'string' && model ? model : null;
  } catch {
    return null;
  }
}

/** The total_tokens Codex is running at, so a token_count repeated for a rate-limit update (same
 *  total) is not a second request. Null when the line carries none. */
export function parseCodexTotal(line: string): number | null {
  if (!line.includes('"token_count"')) return null;
  try {
    const parsed = JSON.parse(line);
    const total = parsed?.payload?.info?.total_token_usage?.total_tokens;
    return typeof total === 'number' && Number.isFinite(total) ? total : null;
  } catch {
    return null;
  }
}

function num(value: unknown): number {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0;
}

/**
 * Claude Code config directories that may hold transcripts: every `~/.claude`-style directory a
 * second login filled in, plus any an instance pins through its env (hub-created accounts live in
 * the data dir, not HOME). The regex is the same one account discovery uses, so the two agree on
 * what a config directory is.
 */
export function claudeConfigDirs(home: string, instanceEnvs: Array<Record<string, string>>): string[] {
  const dirs = new Set<string>();
  try {
    for (const name of readdirSync(home)) {
      if (/^\.claude(?=$|[0-9]|[-_.])/.test(name)) {
        const dir = join(home, name);
        try {
          if (statSync(dir).isDirectory()) dirs.add(dir);
        } catch { /* unreadable: not ours to read */ }
      }
    }
  } catch {
    // No home to look at: only the instances' own directories remain.
  }
  for (const env of instanceEnvs) {
    const dir = env.CLAUDE_CONFIG_DIR;
    if (dir) dirs.add(configDirectory(env, 'CLAUDE_CONFIG_DIR', ['.claude'], process.cwd()));
  }
  return [...dirs];
}

/** Codex homes: the instances' own CODEX_HOME (TRAE pins CALLI_TRAEX_HOME), and the default one. */
export function codexHomes(instanceEnvs: Array<Record<string, string>>): string[] {
  const homes = new Set<string>();
  for (const env of instanceEnvs) {
    if (env.CALLI_TRAEX_HOME || env.CODEX_HOME) {
      homes.add(configDirectory(env, 'CODEX_HOME', ['.codex'], process.cwd()));
    }
  }
  homes.add(configDirectory({}, 'CODEX_HOME', ['.codex'], process.cwd()));
  return [...homes];
}

/** Claude transcripts that changed since the cutoff: every config directory's projects tree,
 *  subagent sessions included. A munged directory holds the main `<sessionId>.jsonl` files and,
 *  one level deeper, `<sessionId>/subagents/*.jsonl`; both are counted under their session.
 *
 *  The walk is async and yields between directories: a machine with hundreds of transcripts must
 *  not hold the hub's event loop over them. */
export async function claudeTranscriptFiles(configDirs: string[], cutoffMs: number): Promise<string[]> {
  const out: string[] = [];
  const readJsonl = async (dir: string, name: string): Promise<void> => {
    const path = join(dir, name);
    try {
      if ((await stat(path)).mtimeMs >= cutoffMs) out.push(path);
    } catch { /* gone between listing and stat */ }
  };
  for (const dir of configDirs) {
    const projects = join(dir, 'projects');
    let munged;
    try {
      munged = await readdir(projects, { withFileTypes: true });
    } catch {
      continue; // not installed, or no conversations yet
    }
    for (const folder of munged) {
      if (!folder.isDirectory()) continue;
      const cwdDir = join(projects, folder.name);
      let entries;
      try {
        entries = await readdir(cwdDir, { withFileTypes: true });
      } catch {
        continue;
      }
      for (const entry of entries) {
        if (entry.isFile() && entry.name.endsWith('.jsonl')) {
          await readJsonl(cwdDir, entry.name);
        } else if (entry.isDirectory()) {
          // Subagent transcripts of the session this directory is named for.
          const subagents = join(cwdDir, entry.name, 'subagents');
          let subs;
          try {
            subs = await readdir(subagents, { withFileTypes: true });
          } catch {
            continue;
          }
          for (const sub of subs) {
            if (sub.isFile() && sub.name.endsWith('.jsonl')) await readJsonl(subagents, sub.name);
          }
        }
      }
    }
  }
  return out;
}

/** Codex rollouts that changed since the cutoff, from `sessions/YYYY/MM/DD/` and
 *  `archived_sessions/`; async like the Claude walk above. */
export async function codexTranscriptFiles(homes: string[], cutoffMs: number): Promise<string[]> {
  const out: string[] = [];
  for (const home of homes) {
    const sessions = join(home, 'sessions');
    try {
      for (const year of await readdir(sessions, { withFileTypes: true })) {
        if (!year.isDirectory() || !/^\d{4}$/.test(year.name)) continue;
        for (const month of await readdir(join(sessions, year.name), { withFileTypes: true })) {
          if (!month.isDirectory() || !/^\d{2}$/.test(month.name)) continue;
          for (const day of await readdir(join(sessions, year.name, month.name), { withFileTypes: true })) {
            if (!day.isDirectory() || !/^\d{2}$/.test(day.name)) continue;
            for (const file of await readdir(join(sessions, year.name, month.name, day.name), { withFileTypes: true })) {
              if (file.isFile() && file.name.startsWith('rollout-') && file.name.endsWith('.jsonl')) {
                const path = join(sessions, year.name, month.name, day.name, file.name);
                try {
                  if ((await stat(path)).mtimeMs >= cutoffMs) out.push(path);
                } catch { /* gone between listing and stat */ }
              }
            }
          }
        }
      }
    } catch {
      // Not installed or no sessions; archived rollouts may still exist.
    }
    const archived = join(home, 'archived_sessions');
    try {
      for (const file of await readdir(archived, { withFileTypes: true })) {
        if (file.isFile() && file.name.startsWith('rollout-') && file.name.endsWith('.jsonl')) {
          const path = join(archived, file.name);
          try {
            if ((await stat(path)).mtimeMs >= cutoffMs) out.push(path);
          } catch { /* gone between listing and stat */ }
        }
      }
    } catch { /* none */ }
  }
  return out;
}

/**
 * The session a transcript belongs to, how the hub knows a thread by it: a Claude Code
 * transcript's own name (`<id>.jsonl`), the session folder over a subagent's, the thread id at
 * the end of a Codex rollout's name.
 */
export function transcriptSession(path: string): string {
  const name = basename(path).replace(/\.jsonl$/, '');
  const parts = path.split('/');
  if (parts.at(-2) === 'subagents') return parts.at(-3) ?? '';
  if (name.startsWith('rollout-') && name.length >= 36) return name.slice(-36);
  return name;
}


export const transcriptHome = () => process.env.HOME ?? homedir();
