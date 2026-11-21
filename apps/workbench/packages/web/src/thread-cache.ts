import type { AgentInfo, Thread } from './api';

const KEY = 'calli.threads.v1';
/** Enough to fill the sidebar at once; the live list replaces it a moment later anyway. */
const MAX_STORED = 200;
const MAX_HOSTS = 8;

interface Stored { at: string; threads: Thread[] }

/** This page's lists, whole, so switching machines back and forth needs no storage at all. */
const memory = new Map<string, Stored>();

const hostKey = (hostId: string | null) => hostId ?? '';

function stored(): Record<string, Stored> {
  try {
    const data = JSON.parse(localStorage.getItem(KEY) ?? '{}');
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
    return Object.fromEntries(Object.entries(data).filter(([, v]) => {
      const s = v as Stored | null;
      return s && typeof s.at === 'string' && Array.isArray(s.threads) && s.threads.every(t => t && typeof t.id === 'string');
    })) as Record<string, Stored>;
  } catch {
    return {};
  }
}

/** The list last read from a machine (null is the hub's own), and when; null if never read on this device. */
export function cachedThreads(hostId: string | null): Stored | null {
  return memory.get(hostKey(hostId)) ?? stored()[hostKey(hostId)] ?? null;
}

export function rememberThreads(hostId: string | null, threads: Thread[], at: string): void {
  memory.set(hostKey(hostId), { at, threads });
  try {
    const recent = [...threads].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).slice(0, MAX_STORED);
    const entries = Object.entries({ ...stored(), [hostKey(hostId)]: { at, threads: recent } });
    entries.sort((a, b) => b[1].at.localeCompare(a[1].at));
    localStorage.setItem(KEY, JSON.stringify(Object.fromEntries(entries.slice(0, MAX_HOSTS))));
  } catch {
    // Storage full or disabled: this page still keeps the list in memory.
  }
}

/** What a row needs to show which agent runs a thread: its mark, or its name when it has none. */
export type AgentMarkInfo = Pick<AgentInfo, 'id' | 'label' | 'icon'>;

const MARKS_KEY = 'calli.agentMarks.v1';
const marksMemory = new Map<string, AgentMarkInfo[]>();

function storedMarks(): Record<string, AgentMarkInfo[]> {
  try {
    const data = JSON.parse(localStorage.getItem(MARKS_KEY) ?? '{}');
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
    return Object.fromEntries(Object.entries(data).filter(([, v]) => Array.isArray(v) && v.every(a => a && typeof a.id === 'string' && typeof a.label === 'string'))) as Record<string, AgentMarkInfo[]>;
  } catch {
    return {};
  }
}

/** A machine's agents as last read on this device, so its rows keep their marks while the live list loads. */
export function cachedAgentMarks(hostId: string | null): AgentMarkInfo[] | null {
  return marksMemory.get(hostKey(hostId)) ?? storedMarks()[hostKey(hostId)] ?? null;
}

export function rememberAgentMarks(hostId: string | null, agents: AgentMarkInfo[]): void {
  const marks = agents.map(({ id, label, icon }) => (icon ? { id, label, icon } : { id, label }));
  const before = cachedAgentMarks(hostId);
  marksMemory.set(hostKey(hostId), marks);
  if (before && JSON.stringify(before) === JSON.stringify(marks)) return;
  try {
    const entries = Object.entries({ ...storedMarks(), [hostKey(hostId)]: marks });
    localStorage.setItem(MARKS_KEY, JSON.stringify(Object.fromEntries(entries.slice(-MAX_HOSTS))));
  } catch {
    // Storage full or disabled: this page still keeps them in memory.
  }
}

/** Another hub, or signed out: what was read from the last one is not this one's. */
export function clearThreadCache(): void {
  memory.clear();
  marksMemory.clear();
  try { localStorage.removeItem(KEY); localStorage.removeItem(MARKS_KEY); } catch { /* Storage may be disabled. */ }
}
