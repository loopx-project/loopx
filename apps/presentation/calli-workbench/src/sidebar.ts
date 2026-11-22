/** How threads are arranged in the sidebar. Pure, so it can be tested directly. */
import type { Attention } from './attention';
import type { AgentInfo, Section, Thread } from './api';
import { folderName } from './paths';
import { t } from './i18n';

/** Desktop sidebar bounds keep the thread list useful without squeezing the conversation away. */
export const SIDEBAR_MIN_WIDTH = 240;
export const SIDEBAR_MAX_WIDTH = 520;
/** The width nobody chose: a quarter of the window, between these two. */
export const SIDEBAR_DEFAULT_MIN = 288;
export const SIDEBAR_DEFAULT_MAX = 360;

export function clampSidebarWidth(width: number): number {
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(width)));
}

/**
 * Until the list is dragged, its width follows the window: a wide screen has room for whole thread
 * titles, a laptop keeps the conversation its space.
 */
export function defaultSidebarWidth(viewport: number): number {
  return Math.min(SIDEBAR_DEFAULT_MAX, Math.max(SIDEBAR_DEFAULT_MIN, Math.round(viewport / 4)));
}

/** A width the user dragged to, or null when there is none (or what is stored is not a width). */
export function savedSidebarWidth(value: string | null): number | null {
  if (value === null) return null;
  const width = Number(value);
  return Number.isFinite(width) ? clampSidebarWidth(width) : null;
}

/**
 * Which agents need their name next to their mark: a mark several agents share is only
 * ambiguous for the agents after its first one, which is the original built-in.
 */
export function taggedAgents(agents: Array<Pick<AgentInfo, 'icon'> & { id?: string }>): Set<string> {
  const firstWithIcon = new Map<string, string>();
  const tagged = new Set<string>();
  for (const { id, icon } of agents) {
    if (!icon) continue;
    const first = firstWithIcon.get(icon);
    if (first === undefined) firstWithIcon.set(icon, id ?? '');
    else if (id && id !== first) tagged.add(id);
  }
  return tagged;
}

/** A page the sidebar lists behind its "more" button: whether the main area shows it now, and whether it carries a state worth a glance (a desktop still connected). */
export interface OverflowPage {
  active: boolean;
  live?: boolean;
}

/** The pages after "New thread" and "Plans" sit behind a "more" button — except the one on screen, or one with a state to keep an eye on, which stays a row of its own. */
export function navOverflow<T extends OverflowPage>(pages: T[]): { shown: T[]; hidden: T[] } {
  const own = (p: T) => p.active || p.live === true;
  return { shown: pages.filter(own), hidden: pages.filter((p) => !own(p)) };
}

/**
 * The directory a thread counts under. A fork in a git worktree stands in a directory of its own,
 * but it is still work on the project it was forked in, so that is where it is listed.
 */
export function projectDir(t: Pick<Thread, 'cwd' | 'cwdReal' | 'worktree'>): { cwd: string; real: string } {
  return t.worktree ? { cwd: t.worktree.of, real: t.worktree.ofReal || t.worktree.of } : { cwd: t.cwd, real: t.cwdReal || t.cwd };
}

/** The threads a search leaves: those whose title or directory has the words in it. All of them when nothing is typed. */
function matching(threads: Thread[], search: string): Thread[] {
  const query = search.trim().toLowerCase();
  return query ? threads.filter((t) => t.title.toLowerCase().includes(query) || t.cwd.toLowerCase().includes(query)) : threads;
}

export interface SidebarModel {
  pinned: Thread[];
  sections: Array<{ section: Section; threads: Thread[] }>;
  /** `key` identifies the project (and its collapsed state); `cwd` is the path to show. */
  projects: Array<{ key: string; cwd: string; name: string; threads: Thread[] }>;
  /** Threads in no project (each in a scratch folder of its own), together after the projects. */
  scratch: Thread[];
  /** Number of threads shown after filtering. */
  count: number;
}

/**
 * A thread appears exactly once: under "pinned" if pinned, else under its section if it has one,
 * else under its project (working directory). Threads keep the order they arrive in.
 *
 * Projects are told apart by the directory with symlinks resolved: the same folder reached by two
 * spellings is one project. The group shows the spelling of its most recent thread.
 */
export function buildSidebar(threads: Thread[], sections: Section[], search: string): SidebarModel {
  const query = search.trim().toLowerCase();
  const visible = matching(threads, search);

  const sectionIds = new Set(sections.map((s) => s.id));
  const pinned: Thread[] = [];
  const bySection = new Map<string, Thread[]>();
  const byProject = new Map<string, Thread[]>();
  const scratch: Thread[] = [];

  for (const t of visible) {
    if (t.pinned) {
      pinned.push(t);
    } else if (t.sectionId && sectionIds.has(t.sectionId)) {
      bySection.set(t.sectionId, [...(bySection.get(t.sectionId) ?? []), t]);
    } else if (t.scratch) {
      scratch.push(t);
    } else {
      const key = projectDir(t).real;
      byProject.set(key, [...(byProject.get(key) ?? []), t]);
    }
  }

  return {
    pinned,
    // While searching, empty sections are noise. Otherwise they stay visible so threads can be filed into them.
    sections: [...sections]
      .sort((a, b) => a.position - b.position)
      .map((section) => ({ section, threads: bySection.get(section.id) ?? [] }))
      .filter((s) => !query || s.threads.length > 0),
    // Projects follow their most recent thread, which is first in each list.
    projects: [...byProject.entries()]
      .map(([key, list]) => ({ key, cwd: projectDir(list[0]).cwd, name: folderName(projectDir(list[0]).cwd), threads: list }))
      .sort((a, b) => (a.threads[0].updatedAt < b.threads[0].updatedAt ? 1 : -1)),
    scratch,
    count: visible.length,
  };
}

export interface ActivityModel {
  /** In the order shown; a group with nothing in it is left out. */
  groups: Array<{ key: string; label: string; threads: Thread[] }>;
  /** Number of threads shown after filtering. */
  count: number;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();

/**
 * What a thread wants from a person, most pressing first: an approval, a question, a plan to
 * confirm, then a result nobody has looked at, a failed one named as such. Null when it wants
 * nothing. The sidebar, its dots and the summaries extensions read all go by this, so a thread is
 * in the same state everywhere. The thread on screen is being read right now: it is never unread.
 */
export function threadAttention(t: Pick<Thread, 'id' | 'waiting' | 'waitingReason' | 'unread' | 'status'>, selectedId: string | null): Attention | null {
  // Older daemons do not say what is awaited; approvals were all there was.
  if (t.waiting) return { kind: t.waitingReason ?? 'approval' };
  if (t.unread && t.id !== selectedId) return { kind: t.status === 'error' ? 'error' : 'unread' };
  return null;
}

/** Whether an attention is a person being waited on, as against a result to look at. */
export const isWait = (a: Attention | null): boolean => a !== null && a.kind !== 'unread' && a.kind !== 'error';

/** What a thread wants, most pressing first; null when it wants nothing. */
function urgency(t: Thread, selectedId: string | null): number | null {
  const attention = threadAttention(t, selectedId);
  if (attention) return isWait(attention) ? 0 : 1;
  if (t.status === 'running') return 2;
  return null;
}

/**
 * The list by what is going on, not by where a thread is filed. First the threads that want the
 * user or are at work: waiting for a response, then finished and not yet read, then running. After
 * them every other thread, newest first, under the day it last moved. Pins, sections and projects
 * play no part here.
 */
export function buildActivity(threads: Thread[], search: string, now: Date, selectedId: string | null): ActivityModel {
  const visible = matching(threads, search);
  const newestFirst = (a: Thread, b: Thread) => (a.updatedAt < b.updatedAt ? 1 : a.updatedAt > b.updatedAt ? -1 : 0);

  const priority = visible
    .filter((t) => urgency(t, selectedId) !== null)
    .sort((a, b) => urgency(a, selectedId)! - urgency(b, selectedId)! || newestFirst(a, b));

  const today = startOfDay(now);
  const ages: Array<{ key: string; label: string; within: number }> = [
    { key: 'today', label: t('sidebar.group.today'), within: 0 },
    { key: 'yesterday', label: t('sidebar.group.yesterday'), within: 1 },
    { key: 'week', label: t('sidebar.group.week'), within: 7 },
    { key: 'month', label: t('sidebar.group.month'), within: 30 },
    { key: 'older', label: t('sidebar.group.older'), within: Infinity },
  ];
  const byAge = new Map<string, Thread[]>();
  for (const t of visible.filter((t) => urgency(t, selectedId) === null).sort(newestFirst)) {
    // Rounded: a day that a clock change made 23 or 25 hours long is still one day.
    const days = Math.round((today - startOfDay(new Date(t.updatedAt))) / DAY_MS);
    const { key } = ages.find((a) => days <= a.within)!;
    byAge.set(key, [...(byAge.get(key) ?? []), t]);
  }

  return {
    groups: [{ key: 'priority', label: t('sidebar.group.priority'), threads: priority }, ...ages.map((a) => ({ key: a.key, label: a.label, threads: byAge.get(a.key) ?? [] }))].filter(
      (g) => g.threads.length > 0,
    ),
    count: visible.length,
  };
}

/** Group explicit handovers only, within the caller's existing section or project. */
export function handoverGroups(threads: Thread[]): Array<{ key: string; latest: Thread; members: Thread[] }> {
  const byId = new Map(threads.map((t) => [t.id, t]));
  const root = (t: Thread): string => {
    const seen = new Set<string>();
    while (t.handover && !t.handover.detached && !seen.has(t.id)) {
      seen.add(t.id);
      const parent = byId.get(t.handover.sourceId);
      if (!parent) return t.handover.sourceId;
      t = parent;
    }
    return seen.has(t.id) ? [...seen].sort()[0] : t.id;
  };
  const groups = new Map<string, Thread[]>();
  for (const t of threads) {
    const key = root(t);
    groups.set(key, [...(groups.get(key) ?? []), t]);
  }
  return [...groups].map(([key, members]) => {
    members.sort((a, b) => b.createdAt.localeCompare(a.createdAt) || b.id.localeCompare(a.id));
    const parents = new Set(members.map((t) => t.handover?.sourceId));
    return { key, latest: members.find((t) => !parents.has(t.id)) ?? members[0], members };
  }).sort((a, b) => {
    const newest = (members: Thread[]) => members.reduce((time, t) => t.updatedAt > time ? t.updatedAt : time, '');
    return newest(b.members).localeCompare(newest(a.members));
  });
}

/** A search needs this much typed before conversations are looked through; a single letter is in all of them. */
export const SEARCH_MIN = 2;

/** The words of a search, as the daemon takes them: split at spaces, case put aside. */
export function searchTerms(search: string): string[] {
  return [...new Set(search.toLowerCase().split(/\s+/).filter(Boolean))];
}

/** A found passage cut where the searched words are, so they can be marked. Nothing is marked for an empty search. */
export function markTerms(text: string, search: string): Array<{ text: string; hit: boolean }> {
  const terms = searchTerms(search).sort((a, b) => b.length - a.length);
  if (!terms.length) return [{ text, hit: false }];
  const lower = text.toLowerCase();
  const parts: Array<{ text: string; hit: boolean }> = [];
  let at = 0;
  while (at < text.length) {
    let next = -1;
    let len = 0;
    for (const term of terms) {
      const i = lower.indexOf(term, at);
      if (i >= 0 && (next < 0 || i < next)) { next = i; len = term.length; }
    }
    if (next < 0) break;
    if (next > at) parts.push({ text: text.slice(at, next), hit: false });
    parts.push({ text: text.slice(next, next + len), hit: true });
    at = next + len;
  }
  if (at < text.length) parts.push({ text: text.slice(at), hit: false });
  return parts;
}

/**
 * One machine's part of the merged list. `live`: read just now; `cached`: what was last read on
 * this device, a fresh read under way; `loading`: never read here, reading; `failed`: the read
 * failed, the last list (if any) stands in; `offline`: the machine cannot be reached.
 */
export interface HostSource {
  hostId: string | null;
  label: string;
  state: 'live' | 'cached' | 'loading' | 'failed' | 'offline';
  /** When `threads` was read; null when nothing was. */
  at: string | null;
  threads: Thread[];
}

export interface MergedModel {
  groups: Array<{ key: string; label: string; items: Array<{ thread: Thread; hostId: string | null }> }>;
  count: number;
  /** Machines not read yet, unreachable or failing, in the order given: the list says so rather than leaving them out. */
  notes: HostSource[];
}

/**
 * Every machine's threads in one list, by what is going on, as the activity view orders them.
 * Team members stay under their lead, as in each machine's own list. Thread ids are UUIDs, so the
 * one open on screen cannot be mistaken for a thread of another machine.
 */
export function buildMerged(sources: HostSource[], search: string, now: Date, selectedId: string | null): MergedModel {
  const owner = new Map<Thread, string | null>();
  const all: Thread[] = [];
  for (const source of sources) {
    const ids = new Set(source.threads.map((t) => t.id));
    for (const thread of source.threads) {
      if (thread.coordination && thread.coordination.rootId !== thread.id && ids.has(thread.coordination.rootId)) continue;
      owner.set(thread, source.hostId);
      all.push(thread);
    }
  }
  const activity = buildActivity(all, search, now, selectedId);
  return {
    groups: activity.groups.map((g) => ({ key: g.key, label: g.label, items: g.threads.map((thread) => ({ thread, hostId: owner.get(thread) ?? null })) })),
    count: activity.count,
    // A remembered list being refreshed reads as it is; one that cannot be refreshed says so.
    notes: sources.filter((s) => s.state === 'loading' || s.state === 'failed' || s.state === 'offline'),
  };
}
