import { HttpError } from './errors.js';
import { effectiveHandoverMode, handoverSourceMoved, type HandoverLink } from './handover-mode.js';
import { mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { hasDiffParts, replaceMessageTail, toolText, type EventType, type StoredEvent } from './events.js';
import { McpRegistry } from './mcp.js';
import { ExtensionStore } from './extensions.js';
import { Telemetry } from './telemetry.js';
import { Activity } from './activity.js';
import { askedText } from './util.js';
import { referencesFile } from './artifacts.js';
import type { ProgressReport } from './progress-report.js';
import type { WritingState } from './writing.js';
import { HistoryStore } from './history.js';
import type { Answer, DiffEvent } from './outputs.js';
import { ToolJobs } from './tool-jobs.js';
import { MessageEdits } from './message-edits.js';
import type { MeetingReference, SignalMaterial, SignalMaterialRead } from './channels/lark-material.js';

/** Storage cap for one tool snapshot's output; see docs/tool-output-storage-design.md. */
const MAX_OUTPUT_CHARS = 128 * 1024;
const OUTPUT_HEAD_CHARS = 96 * 1024;
const OUTPUT_TAIL_CHARS = 32 * 1024;

export type TitleBy = 'default' | 'user' | 'auto';

export interface SignalDisposition {
  outcome: 'filtered' | 'delivered' | 'router';
  reason?: string;
  threadId?: string;
  threadIds?: string[];
  matterIds?: string[];
  deliveries?: Array<{ matterId: string; threadId: string }>;
  /** Delivery is durable before the informational Router notice is accepted. */
  pending?: boolean;
}
export interface SignalRecord {
  key: string; at: string; payload: unknown; disposition: SignalDisposition; updatedAt: string;
  material?: SignalMaterial;
}
type SignalRow = { key: string; at: string; payload: string; disposition: string; updated_at: string; material?: string | null };
const toSignal = (row: SignalRow): SignalRecord => ({ key: row.key, at: row.at, payload: JSON.parse(row.payload), disposition: JSON.parse(row.disposition), updatedAt: row.updated_at,
  ...(row.material ? { material: JSON.parse(row.material) as SignalMaterial } : {}) });

export interface ThreadRecord {
  id: string;
  agent: string;
  cwd: string;
  title: string;
  agentSessionId: string | null;
  status: 'idle' | 'running' | 'error';
  pinned: boolean;
  speed?: string | null;
  workMode?: 'execute' | 'plan';
  permissionMode: string;
  model: string | null;
  effort: string | null;
  /** Set when the thread is archived: hidden from the sidebar, kept on disk, restorable. */
  archivedAt: string | null;
  /** The user-made section this thread is filed under, if any. */
  sectionId: string | null;
  /** The thread this one was forked from. Kept even if that thread is later deleted. */
  forkedFrom: string | null;
  handover?: HandoverLink | null;
  /** Set when the thread runs in a git worktree the hub made for it, or shares one with the thread it was forked from. */
  worktree: ThreadWorktree | null;
  /** A turn finished and nobody has looked at the result yet. */
  unread: boolean;
  /** When the hub last started a turn here. Compared with `externalAt` for the terminal warning. */
  hubTurnAt: string | null;
  /** When syncing last found turns the terminal added to this session. */
  externalAt: string | null;
  /** The newest thing the user asked, kept as the sidebar's second line. Null until the first ask. */
  preview: string | null;
  /** The schedule that started this thread, if any: what ran it, for the sidebar's badge. */
  schedule: { id: string; name: string } | null;
  /** When the person marked the thread done. Cleared when they send it new work. */
  doneAt: string | null;
  /** The agent's `[进度]` line from its last turn that was asked for one. */
  report: ProgressReport | null;
  /** Writing mode: the stage and the article. Null when the thread is not in writing mode. */
  writing: WritingState | null;
  createdAt: string;
  updatedAt: string;
}

/** A git worktree a thread stands in. See worktrees.ts. */
export interface ThreadWorktree {
  /** The root of the checkout. The thread's `cwd` is this, or a directory inside it. */
  root: string;
  branch: string;
  /** The directory the thread would be in without the worktree: the one it is listed under. */
  of: string;
}

export interface SectionRecord {
  id: string;
  name: string;
  position: number;
}

/** A recurring task: a prompt an agent runs in a directory on a cron schedule. See scheduler.ts. */
export interface ScheduleRecord {
  id: string;
  name: string;
  agent: string;
  cwd: string;
  prompt: string;
  /** Five-field cron expression, matched in the schedule's timezone (or daemon local when null). */
  cron: string;
  /** `new`: a fresh thread each run. `same`: one thread, context builds up. */
  mode: 'new' | 'same';
  /** The bound thread for `same` mode. Null until its first run created one, or when rebound. */
  threadId: string | null;
  permissionMode: string;
  enabled: boolean;
  /** IANA timezone for cron matching, e.g. "Asia/Shanghai". Null = daemon local time. */
  timezone: string | null;
  /** When true: if runs were missed while the daemon was down, fire once on restart. */
  catchUp: boolean;
  /** When a scheduled fire last went out, for de-duplicating ticks in one minute. */
  lastFiredAt: string | null;
  createdAt: string;
  updatedAt: string;
}

/** One firing of a schedule: what happened, and where to look at it. */
export interface ScheduleRunRecord {
  id: string;
  scheduleId: string | null;
  todoId?: string | null;
  newThread?: boolean;
  status: 'running' | 'done' | 'quiet' | 'error' | 'skipped' | 'missed';
  threadId: string | null;
  turnId: string | null;
  startedAt: string;
  finishedAt: string | null;
  message: string | null;
}

export type TodoLane = 'you' | 'doing' | 'wait' | 'later';
export const TODO_LANES: TodoLane[] = ['you', 'doing', 'wait', 'later'];
export type TodoImportance = 'high' | 'normal' | 'low';
export const TODO_IMPORTANCES: TodoImportance[] = ['high', 'normal', 'low'];
export type TodoSourceType = 'lark-task' | 'lark-message' | 'lark-flag' | 'lark-calendar' | 'manual';
export const TODO_SOURCE_TYPES: TodoSourceType[] = ['lark-task', 'lark-message', 'lark-flag', 'lark-calendar', 'manual'];
export const TODO_SUBSCRIPTION_KINDS = ['lark-thread', 'lark-task', 'lark-calendar'] as const;
export interface TodoThread { kind: typeof TODO_SUBSCRIPTION_KINDS[number]; ref: string }
export interface TodoSubscription extends TodoThread { at: string }
export function todoThread(value: unknown): TodoThread {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new HttpError(400, 'subscription must be a kind/ref object');
  const { kind, ref } = value as TodoThread;
  if (!TODO_SUBSCRIPTION_KINDS.includes(kind) || typeof ref !== 'string' || !ref || ref.length > 200 || /\s/.test(ref)) {
    throw new HttpError(400, 'subscription requires lark-thread, lark-task or lark-calendar and a nonempty ref (at most 200 characters, no whitespace)');
  }
  return { kind, ref };
}
function todoSubscriptions(value: TodoSubscription[]): TodoSubscription[] {
  if (!Array.isArray(value)) throw new HttpError(400, 'subscriptions must be an array');
  const unique = new Map<string, TodoSubscription>();
  for (const item of value) {
    const thread = todoThread(item);
    if (typeof item.at !== 'string' || !Number.isFinite(Date.parse(item.at))) throw new HttpError(400, 'subscription.at must be a timestamp');
    const key = JSON.stringify(thread);
    if (!unique.has(key)) unique.set(key, { ...thread, at: new Date(item.at).toISOString() });
  }
  if (unique.size > 20) throw new HttpError(400, 'at most 20 subscriptions are allowed');
  return [...unique.values()];
}
/** Where a todo came from, when it arrived through a channel; null for hand-made ones. */
export interface TodoSource {
  type: TodoSourceType;
  ref: string;
  url?: string;
  from?: string;
  at?: string;
  thread?: TodoThread;
}

export interface TodoUrgency { urgent: boolean; reason: 'overdue' | 'due-soon' | 'pinned' | 'meeting-today' | null }
/**
 * Derived, never stored: an overdue or soon-due item is urgent, a pin keeps the item urgent
 * until it expires, and a done item is never urgent no matter what. Reasons are checked in the
 * listed order, so an overdue pin still reads as overdue.
 */
export function urgencyOf(todo: Pick<TodoRecord, 'done' | 'state' | 'dueAt' | 'urgentUntil'>, now = new Date()): TodoUrgency {
  if (todo.done || todo.state === 'done') return { urgent: false, reason: null };
  const at = now.getTime();
  const due = todo.dueAt ? Date.parse(todo.dueAt) : Number.NaN;
  if (Number.isFinite(due)) {
    if (due <= at) return { urgent: true, reason: 'overdue' };
    if (due - at <= 48 * 60 * 60 * 1000) return { urgent: true, reason: 'due-soon' };
  }
  const pinned = todo.urgentUntil ? Date.parse(todo.urgentUntil) : Number.NaN;
  if (Number.isFinite(pinned) && pinned > at) return { urgent: true, reason: 'pinned' };
  return { urgent: false, reason: null };
}

/** One entry on the plan page. `parentId` is only ever a top-level todo: one level of nesting. */
export interface TodoNote { text: string; updatedAt: string | null; threadId: string | null }
export interface TodoProposal { id: string; todoId: string; kind: 'add' | 'done'; evidence: string; threadId: string; createdAt: string; state: 'open' | 'accepted' | 'dismissed' }
export type TodoPatch = Partial<Pick<TodoRecord, 'text' | 'done' | 'owner' | 'state' | 'lane' | 'importance' | 'horizon' | 'urgentUntil' | 'source' | 'subscriptions' | 'waitingOn' | 'dueAt' | 'followUpAt' | 'followUpNoProgress' | 'followUpPaused' | 'proposed'>>;
export interface TodoRecord {
  horizon: 'week' | 'month' | null;
  owner: 'me' | 'agent';
  state: 'open' | 'waiting' | 'done';
  /** Explicit group; null lets the page infer it from the due date and waiting state. */
  lane: TodoLane | null;
  /** The person's own weight for the item; null is unspecified. */
  importance: TodoImportance | null;
  /** A human-set pin that keeps the item urgent until it expires. */
  urgentUntil: string | null;
  /** Where the todo came from, when it arrived through a channel. */
  source: TodoSource | null;
  subscriptions: TodoSubscription[];
  waitingOn: string | null;
  dueAt: string | null;
  followUpAt: string | null;
  followUpNoProgress: number;
  followUpPaused: boolean;
  rev: number;
  proposed: boolean;
  hasNote: boolean;
  notePreview: string | null;
  noteUpdatedAt: string | null;
  id: string;
  text: string;
  done: boolean;
  parentId: string | null;
  position: number;
  createdAt: string;
  /** When the person or an agent last did anything to this item; the desk's last-activity signal. */
  updatedAt: string;
  doneAt: string | null;
}

interface TodoRow {
  subscriptions: string;
  owner: 'me' | 'agent'; state: 'open' | 'waiting' | 'done'; lane: string | null; importance: string | null; urgent_until: string | null; source: string | null; waiting_on: string | null;
  horizon: 'week' | 'month' | null; due_at: string | null; follow_up_at: string | null; rev: number; proposed: number;
  follow_up_no_progress: number; follow_up_paused: number;
  has_note: number; note_preview: string | null; note_updated_at: string | null;
  id: string;
  text: string;
  done: number;
  parent_id: string | null;
  position: number;
  created_at: string;
  updated_at: string | null;
  done_at: string | null;
}

interface ScheduleRow {
  id: string;
  name: string;
  agent: string;
  cwd: string;
  prompt: string;
  cron: string;
  mode: string;
  thread_id: string | null;
  permission_mode: string;
  enabled: number;
  timezone: string | null;
  catch_up: number;
  last_fired_at: string | null;
  created_at: string;
  updated_at: string;
}

interface ScheduleRunRow {
  id: string;
  schedule_id: string | null;
  todo_id: string | null;
  new_thread: number;
  status: string;
  thread_id: string | null;
  turn_id: string | null;
  started_at: string;
  finished_at: string | null;
  message: string | null;
}

const toSchedule = (r: ScheduleRow): ScheduleRecord => ({
  id: r.id,
  name: r.name,
  agent: r.agent,
  cwd: r.cwd,
  prompt: r.prompt,
  cron: r.cron,
  mode: r.mode === 'same' ? 'same' : 'new',
  threadId: r.thread_id,
  permissionMode: r.permission_mode,
  enabled: r.enabled === 1,
  timezone: r.timezone ?? null,
  catchUp: r.catch_up === 1,
  lastFiredAt: r.last_fired_at,
  createdAt: r.created_at,
  updatedAt: r.updated_at,
});

const toScheduleRun = (r: ScheduleRunRow): ScheduleRunRecord => ({
  id: r.id,
  scheduleId: r.schedule_id,
  ...(r.todo_id ? { todoId: r.todo_id, newThread: r.new_thread === 1 } : {}),
  status: r.status as ScheduleRunRecord['status'],
  threadId: r.thread_id,
  turnId: r.turn_id,
  startedAt: r.started_at,
  finishedAt: r.finished_at,
  message: r.message,
});

/** How many runs of one schedule are kept. History is for looking at, not bookkeeping. */
const SCHEDULE_RUN_HISTORY = 50;
const noteHash = (text: string) => createHash('sha256').update(text).digest('hex');

/** Prefer the next action over the template's headings or background context. */
function todoNotePreview(text: string): string {
  const lines = text.split(/\r?\n/).map(line => line.trim());
  const next = lines.findIndex(line => /^##\s+下一步(?:\s+#+)?$/.test(line));
  const body = next < 0 ? lines : lines.slice(next + 1);
  const end = next < 0 ? -1 : body.findIndex(line => /^#{1,2}(?:\s|$)/.test(line));
  return (end < 0 ? body : body.slice(0, end)).find(line => line && !/^#{1,6}(?:\s|$)/.test(line)) ?? '';
}

const toTodoSource = (raw: string | null): TodoSource | null => {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as TodoSource;
    return value && typeof value === 'object' && TODO_SOURCE_TYPES.includes(value.type) && typeof value.ref === 'string' ? value : null;
  } catch { return null; }
};

const toTodo = (r: TodoRow): TodoRecord => ({
  owner: r.owner, state: r.state,
  lane: (TODO_LANES as string[]).includes(r.lane ?? '') ? r.lane as TodoLane : null,
  importance: (TODO_IMPORTANCES as string[]).includes(r.importance ?? '') ? r.importance as TodoImportance : null,
  urgentUntil: r.urgent_until,
  source: toTodoSource(r.source),
  subscriptions: todoSubscriptions(JSON.parse(r.subscriptions)),
  horizon: r.horizon ?? null, waitingOn: r.waiting_on, dueAt: r.due_at, followUpAt: r.follow_up_at,
  followUpNoProgress: r.follow_up_no_progress, followUpPaused: r.follow_up_paused === 1,
  rev: r.rev, proposed: r.proposed === 1, hasNote: r.has_note === 1, notePreview: r.note_preview, noteUpdatedAt: r.note_updated_at,
  id: r.id,
  text: r.text,
  done: r.done === 1,
  parentId: r.parent_id,
  position: r.position,
  createdAt: r.created_at,
  updatedAt: r.updated_at ?? r.created_at,
  doneAt: r.done_at,
});

interface ThreadRow {
  work_mode: string;
  speed: string | null;
  id: string;
  agent: string;
  cwd: string;
  title: string;
  agent_session_id: string | null;
  status: string;
  pinned: number;
  permission_mode: string;
  model: string | null;
  effort: string | null;
  archived_at: string | null;
  section_id: string | null;
  forked_from: string | null;
  handover: string | null;
  worktree: string | null;
  unread: number;
  hub_turn_at: string | null;
  external_at: string | null;
  preview: string | null;
  schedule: string | null;
  done_at: string | null;
  report: string | null;
  writing: string | null;
  created_at: string;
  updated_at: string;
}

interface EventRow {
  thread_id: string;
  seq: number;
  turn_id: string | null;
  type: string;
  ts: string;
  data: string;
}

const toThread = (r: ThreadRow): ThreadRecord => ({
  speed: r.speed,
  workMode: r.work_mode === 'plan' ? 'plan' : 'execute',
  id: r.id,
  agent: r.agent,
  cwd: r.cwd,
  title: r.title,
  agentSessionId: r.agent_session_id,
  status: r.status as ThreadRecord['status'],
  pinned: r.pinned === 1,
  permissionMode: r.permission_mode,
  model: r.model,
  effort: r.effort,
  archivedAt: r.archived_at,
  sectionId: r.section_id,
  forkedFrom: r.forked_from,
  handover: r.handover ? JSON.parse(r.handover) : null,
  worktree: r.worktree ? (JSON.parse(r.worktree) as ThreadWorktree) : null,
  unread: r.unread === 1,
  hubTurnAt: r.hub_turn_at,
  externalAt: r.external_at,
  preview: r.preview,
  schedule: r.schedule ? (JSON.parse(r.schedule) as ThreadRecord['schedule']) : null,
  doneAt: r.done_at ?? null,
  report: r.report ? (JSON.parse(r.report) as ProgressReport) : null,
  writing: r.writing ? (JSON.parse(r.writing) as WritingState) : null,
  createdAt: r.created_at,
  updatedAt: r.updated_at,
});

const toEvent = (r: EventRow): StoredEvent => ({
  threadId: r.thread_id,
  seq: r.seq,
  turnId: r.turn_id,
  type: r.type as EventType,
  ts: r.ts,
  data: JSON.parse(r.data) as Record<string, unknown>,
});

/** SQLite-backed store for threads and their event logs. Uses node:sqlite (no native build). */
/** One turn a search found: where it is, who said it, and the words around the match. */
export interface SearchHit {
  threadId: string;
  turnId: string;
  role: 'user' | 'assistant';
  ts: string;
  snippet: string;
  title: string;
  agent: string;
  cwd: string;
  archived: boolean;
}

/** The stretch of a text around the first word found in it, on one line. */
function snippetAround(text: string, terms: string[]): string {
  const lower = text.toLowerCase();
  const found = terms.map((t) => lower.indexOf(t)).filter((i) => i >= 0);
  const at = found.length ? Math.min(...found) : 0;
  const start = Math.max(0, at - 28);
  const end = Math.min(text.length, at + 110);
  return `${start > 0 ? '…' : ''}${text.slice(start, end).replace(/\s+/g, ' ').trim()}${end < text.length ? '…' : ''}`;
}

export class Store {
  /** Shared with the services the store hosts (telemetry) and the machine's own metering. */
  readonly db: DatabaseSync;
  readonly telemetry: Telemetry;
  readonly activity: Activity;
  readonly history: HistoryStore;
  readonly toolJobs: ToolJobs;
  /** The event that records an edit and hides what came before it in the current conversation. */
  readonly messageEdits: MessageEdits;

  constructor(dataDir: string) {
    mkdirSync(dataDir, { recursive: true });
    this.db = new DatabaseSync(join(dataDir, 'hub.db'));
    this.db.exec('PRAGMA journal_mode = WAL;');
    this.db.exec('PRAGMA foreign_keys = ON;');
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS hub_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS signals (
        key TEXT PRIMARY KEY, at TEXT NOT NULL, payload TEXT NOT NULL,
        disposition TEXT NOT NULL, updated_at TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS signals_at ON signals(at);
      CREATE INDEX IF NOT EXISTS signals_meeting ON signals(json_extract(payload, '$.meeting.meetingId'));
      CREATE TABLE IF NOT EXISTS signal_materials (
        signal_key TEXT PRIMARY KEY REFERENCES signals(key) ON DELETE CASCADE,
        scope TEXT NOT NULL, reference TEXT NOT NULL, material TEXT NOT NULL, content TEXT
      );
      CREATE TABLE IF NOT EXISTS threads (
        id TEXT PRIMARY KEY,
        agent TEXT NOT NULL,
        cwd TEXT NOT NULL,
        title TEXT NOT NULL,
        agent_session_id TEXT,
        status TEXT NOT NULL DEFAULT 'idle',
        pinned INTEGER NOT NULL DEFAULT 0,
        permission_mode TEXT NOT NULL DEFAULT 'auto_edit',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS events (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        turn_id TEXT,
        type TEXT NOT NULL,
        ts TEXT NOT NULL,
        data TEXT NOT NULL,
        PRIMARY KEY (thread_id, seq)
      );
      CREATE INDEX IF NOT EXISTS idx_events_turn_starts ON events(thread_id, seq) WHERE type = 'turn.started';
      CREATE INDEX IF NOT EXISTS idx_events_conversation_starts ON events(thread_id, seq) WHERE type IN ('turn.started', 'voice.message');
      CREATE INDEX IF NOT EXISTS idx_events_turn_lifecycle ON events(thread_id, seq) WHERE type IN ('turn.started', 'turn.done', 'turn.error');
      CREATE INDEX IF NOT EXISTS idx_events_tool_starts ON events(ts) WHERE type = 'tool.start';
      DROP INDEX IF EXISTS idx_events_tool_ids;
    `);
    // Migration: add pinned column if it doesn't exist
    try {
      this.db.prepare('ALTER TABLE threads ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0').run();
    } catch {
      // Column already exists
    }
    // Migration: threads created before permission modes existed get the default level.
    const cols = this.db.prepare('PRAGMA table_info(threads)').all() as Array<{ name: string }>;
    if (!cols.some((c) => c.name === 'permission_mode')) {
      this.db.exec("ALTER TABLE threads ADD COLUMN permission_mode TEXT NOT NULL DEFAULT 'auto_edit'");
    }
    // Migration: model and thinking effort. NULL means the agent's default.
    if (!cols.some((c) => c.name === 'model')) this.db.exec('ALTER TABLE threads ADD COLUMN model TEXT');
    if (!cols.some((c) => c.name === 'effort')) this.db.exec('ALTER TABLE threads ADD COLUMN effort TEXT');
    // Migration: archive and sections.
    if (!cols.some((c) => c.name === 'archived_at')) this.db.exec('ALTER TABLE threads ADD COLUMN archived_at TEXT');
    if (!cols.some((c) => c.name === 'section_id')) this.db.exec('ALTER TABLE threads ADD COLUMN section_id TEXT');
    // Migration: terminal-activity tracking (turns the terminal added, hub turns).
    if (!cols.some((c) => c.name === 'hub_turn_at')) this.db.exec('ALTER TABLE threads ADD COLUMN hub_turn_at TEXT');
    if (!cols.some((c) => c.name === 'external_at')) this.db.exec('ALTER TABLE threads ADD COLUMN external_at TEXT');
    // Migration: who set the title (see titleBy). NULL for threads from before, which no one renames.
    if (!cols.some((c) => c.name === 'title_by')) this.db.exec('ALTER TABLE threads ADD COLUMN title_by TEXT');
    // Migration: the sidebar's preview line, the newest thing the user asked.
    if (!cols.some((c) => c.name === 'preview')) this.db.exec('ALTER TABLE threads ADD COLUMN preview TEXT');
    // Migration: the board's done mark and the agent's progress line, as JSON.
    if (!cols.some((c) => c.name === 'done_at')) this.db.exec('ALTER TABLE threads ADD COLUMN done_at TEXT');
    if (!cols.some((c) => c.name === 'report')) this.db.exec('ALTER TABLE threads ADD COLUMN report TEXT');
    // Migration: writing mode's stage and article, as JSON.
    if (!cols.some((c) => c.name === 'writing')) this.db.exec('ALTER TABLE threads ADD COLUMN writing TEXT');
    // Migration: forks.
    if (!cols.some((c) => c.name === 'handover')) this.db.exec('ALTER TABLE threads ADD COLUMN handover TEXT');
    if (!cols.some((c) => c.name === 'forked_from')) this.db.exec('ALTER TABLE threads ADD COLUMN forked_from TEXT');
    // Migration: the git worktree a forked thread stands in, as JSON.
    if (!cols.some((c) => c.name === 'worktree')) this.db.exec('ALTER TABLE threads ADD COLUMN worktree TEXT');
    // Migration: unread marker.
    if (!cols.some((c) => c.name === 'unread')) this.db.exec('ALTER TABLE threads ADD COLUMN unread INTEGER NOT NULL DEFAULT 0');
    if (!cols.some(c => c.name === 'work_mode')) this.db.exec("ALTER TABLE threads ADD COLUMN work_mode TEXT NOT NULL DEFAULT 'execute'");
    if (!cols.some(c => c.name === 'speed')) this.db.exec('ALTER TABLE threads ADD COLUMN speed TEXT');
    // Migration: the schedule a thread was started by, if any, as JSON { id, name }.
    if (!cols.some((c) => c.name === 'schedule')) this.db.exec('ALTER TABLE threads ADD COLUMN schedule TEXT');
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS sections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        position INTEGER NOT NULL,
        created_at TEXT NOT NULL
      );
    `);
    // Ensure index exists for sorting
    try {
      this.db.exec('CREATE INDEX idx_threads_pinned_updated ON threads(pinned DESC, updated_at DESC)');
    } catch {
      // Index already exists
    }
    // The plan page: long-lived items the user ticks off, optionally linked to threads.
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS todos (
        id TEXT PRIMARY KEY,
        text TEXT NOT NULL,
        done INTEGER NOT NULL DEFAULT 0,
        parent_id TEXT REFERENCES todos(id) ON DELETE CASCADE,
        position INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        done_at TEXT
      );
      CREATE TABLE IF NOT EXISTS todo_threads (
        todo_id TEXT NOT NULL REFERENCES todos(id) ON DELETE CASCADE,
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        PRIMARY KEY (todo_id, thread_id)
      );
    `);
    // Scheduled tasks and their runs. Run history points at threads without a foreign key: a
    // thread can be deleted long after the run that made it, and the history stays.
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS schedules (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        agent TEXT NOT NULL,
        cwd TEXT NOT NULL,
        prompt TEXT NOT NULL,
        cron TEXT NOT NULL,
        mode TEXT NOT NULL,
        thread_id TEXT,
        permission_mode TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        last_fired_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS schedule_runs (
        id TEXT PRIMARY KEY,
        schedule_id TEXT NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
        status TEXT NOT NULL,
        thread_id TEXT,
        turn_id TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        message TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_schedules_runs ON schedule_runs(schedule_id, started_at DESC);
    `);
    const runColumns = this.db.prepare('PRAGMA table_info(schedule_runs)').all() as Array<{ name: string }>;
    if (!runColumns.some(c => c.name === 'todo_id')) this.transaction(() => this.db.exec(`
      ALTER TABLE schedule_runs RENAME TO old_schedule_runs;
      CREATE TABLE schedule_runs (
        id TEXT PRIMARY KEY, schedule_id TEXT REFERENCES schedules(id) ON DELETE CASCADE,
        todo_id TEXT REFERENCES todos(id) ON DELETE CASCADE, new_thread INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL, thread_id TEXT, turn_id TEXT, started_at TEXT NOT NULL, finished_at TEXT, message TEXT
      );
      INSERT INTO schedule_runs (id, schedule_id, status, thread_id, turn_id, started_at, finished_at, message)
        SELECT id, schedule_id, status, thread_id, turn_id, started_at, finished_at, message FROM old_schedule_runs;
      DROP TABLE old_schedule_runs;
      CREATE INDEX idx_schedules_runs ON schedule_runs(schedule_id, started_at DESC);
      CREATE INDEX idx_todo_runs ON schedule_runs(todo_id, started_at DESC);
    `));
    if (!runColumns.some(c => c.name === 'note_before_hash')) this.db.exec('ALTER TABLE schedule_runs ADD COLUMN note_before_hash TEXT');
    const todoColumns = new Set((this.db.prepare('PRAGMA table_info(todos)').all() as Array<{ name: string }>).map(c => c.name));
    for (const [column, definition] of Object.entries({ owner: "TEXT NOT NULL DEFAULT 'me'", state: "TEXT NOT NULL DEFAULT 'open'", lane: 'TEXT', importance: 'TEXT', urgent_until: 'TEXT', source: 'TEXT', subscriptions: "TEXT NOT NULL DEFAULT '[]'", waiting_on: 'TEXT', horizon: 'TEXT', due_at: 'TEXT', follow_up_at: 'TEXT', follow_up_no_progress: 'INTEGER NOT NULL DEFAULT 0', follow_up_paused: 'INTEGER NOT NULL DEFAULT 0', rev: 'INTEGER NOT NULL DEFAULT 1', proposed: 'INTEGER NOT NULL DEFAULT 0', has_note: 'INTEGER NOT NULL DEFAULT 0', note_preview: 'TEXT', note_updated_at: 'TEXT', updated_at: 'TEXT' })) {
      if (!todoColumns.has(column)) this.db.exec(`ALTER TABLE todos ADD COLUMN ${column} ${definition}`);
    }
    // Old items were last touched when they were created; every writer stamps from now on.
    this.db.exec('UPDATE todos SET updated_at = created_at WHERE updated_at IS NULL');
    if (!todoColumns.has('state')) this.db.exec("UPDATE todos SET state = CASE WHEN done = 1 THEN 'done' ELSE 'open' END");
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS todo_notes (todo_id TEXT PRIMARY KEY REFERENCES todos(id) ON DELETE CASCADE, text TEXT NOT NULL, updated_at TEXT NOT NULL, thread_id TEXT);
      CREATE TABLE IF NOT EXISTS todo_proposals (id TEXT PRIMARY KEY, todo_id TEXT NOT NULL REFERENCES todos(id) ON DELETE CASCADE, data TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS wishes (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS todo_summaries (id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, days INTEGER NOT NULL, started_at TEXT NOT NULL);
    `);
    if (!todoColumns.has('has_note')) this.transaction(() => {
      const update = this.db.prepare('UPDATE todos SET has_note = ? WHERE id = ?');
      for (const row of this.db.prepare('SELECT todo_id, text FROM todo_notes').iterate()) {
        update.run(Number(Boolean((row.text as string).trim())), row.todo_id);
      }
    });
    if (!this.hubSetting('todo_note_preview_v2', false)) this.transaction(() => {
      const update = this.db.prepare('UPDATE todos SET note_preview = ? WHERE id = ?');
      for (const row of this.db.prepare('SELECT todo_id, text FROM todo_notes').iterate()) {
        update.run(todoNotePreview(row.text as string), row.todo_id);
      }
      this.saveHubSetting('todo_note_preview_v2', true);
    });
    const linkColumns = this.db.prepare('PRAGMA table_info(todo_threads)').all() as Array<{ name: string }>;
    if (!linkColumns.some(c => c.name === 'linked_at')) this.db.exec("ALTER TABLE todo_threads ADD COLUMN linked_at TEXT NOT NULL DEFAULT ''");
    // Migration: IANA timezone for cron matching ("Asia/Shanghai", etc.). Null means daemon local.
    const schedCols = this.db.prepare('PRAGMA table_info(schedules)').all() as Array<{ name: string }>;
    if (!schedCols.some((c) => c.name === 'timezone')) {
      this.db.exec('ALTER TABLE schedules ADD COLUMN timezone TEXT');
    }
    // Migration: catch-up flag — fire once on restart when runs were missed during downtime.
    if (!schedCols.some((c) => c.name === 'catch_up')) {
      this.db.exec('ALTER TABLE schedules ADD COLUMN catch_up INTEGER NOT NULL DEFAULT 0');
    }
    if (!cols.some((c) => c.name === 'handover')) this.recoverHandovers();
    // What was said in each turn, whole: the user's words and the agent's. An answer is stored as
    // it streamed, a few characters an event, so nothing can be found in the log itself.
    const indexed = this.db.prepare("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'turn_text'").get();
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS turn_text (
        thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
        turn_id TEXT NOT NULL,
        role TEXT NOT NULL,
        ts TEXT NOT NULL,
        text TEXT NOT NULL,
        PRIMARY KEY (thread_id, turn_id, role)
      );
    `);
    if (!indexed) this.indexText();
    this.db.exec('CREATE INDEX IF NOT EXISTS idx_turn_text_ts ON turn_text(ts)');
    this.telemetry = new Telemetry(this.db);
    this.activity = new Activity(this.db);
    this.mcpEntries = new McpRegistry(this.db);
    this.extensions = new ExtensionStore(this.db);
    this.history = new HistoryStore(this.db);
    this.toolJobs = new ToolJobs(this.db);
    this.messageEdits = new MessageEdits(this.db);
  }

  /** The MCP registry: business line and purpose notes, plus manually registered servers. */
  readonly mcpEntries: McpRegistry;

  /** Which Calli extensions are installed on this machine, and the permissions confirmed for them. */
  readonly extensions: ExtensionStore;

  /** Where each open turn began, so its answer is read from there and not from the thread's start. */
  private readonly turnStarts = new Map<string, number>();

  private saveText(threadId: string, turnId: string, role: 'user' | 'assistant', ts: string, text: string): void {
    if (!text.trim()) return;
    this.db
      .prepare('INSERT INTO turn_text (thread_id, turn_id, role, ts, text) VALUES (?, ?, ?, ?, ?) ON CONFLICT(thread_id, turn_id, role) DO UPDATE SET ts = excluded.ts, text = excluded.text')
      .run(threadId, turnId, role, ts, text);
  }

  /** Reuse small tool projections. Cold/forked logs build only tools, yielding between batches. */
  async outputSources(threadId: string): Promise<{ diffs: DiffEvent[]; answers: Answer[]; seq: number }> {
    await this.history.ensure(threadId, 1, this.history.latest(threadId) + 1, true);
    const diffs = (this.db.prepare("SELECT turn_id, seq, ts, data FROM history_rows WHERE thread_id = ? AND type IN ('history.tool.start', 'history.tool.update', 'history.tool.end') ORDER BY seq").all(threadId) as Array<{ turn_id: string; seq: number; ts: string; data: string }>)
      .map(r => {
        const data = JSON.parse(r.data) as DiffEvent['data'];
        if (data.files?.some(f => f.created === undefined)) {
          // Projections from older hubs already have counts. Read only creation flags in SQLite,
          // never transfer old/new text (or the tool output) back into JS, then keep the upgrade.
          const flags = this.db.prepare(`SELECT json_type(p.value, '$.oldText') IS NOT 'text' AS created
            FROM events e, json_each(e.data, '$.content') p WHERE e.thread_id=? AND e.seq=?
            AND json_extract(p.value, '$.type')='diff'`).all(threadId, r.seq) as Array<{ created: number }>;
          data.files.forEach((f, i) => { f.created = Boolean(flags[i]?.created); });
          this.db.prepare('UPDATE history_rows SET data=? WHERE thread_id=? AND seq=?').run(JSON.stringify(data), threadId, r.seq);
        }
        return { turnId: r.turn_id, seq: r.seq, ts: r.ts, data };
      });
    // turn_text.ts is the first chunk's timestamp. Use the final message's log position instead,
    // so an answer that spans an edit, an import or a copied turn cannot be sorted before it.
    const answers = (this.db.prepare(`SELECT tt.turn_id, e.seq, e.ts, tt.text FROM turn_text tt
      JOIN events e ON e.thread_id=tt.thread_id AND e.seq=(SELECT MAX(seq) FROM events
        WHERE thread_id=tt.thread_id AND turn_id=tt.turn_id AND type IN ('message.delta', 'message.replace'))
      WHERE tt.thread_id=? AND tt.role='assistant' ORDER BY e.seq`).all(threadId) as Array<{ turn_id: string; seq: number; ts: string; text: string }>)
      .map(r => ({ turnId: r.turn_id, seq: r.seq, ts: r.ts, text: r.text }));
    const seq = this.history.latest(threadId);
    return { diffs, answers, seq };
  }

  /** Completed answers are already assembled for search, including imported and older turns. */
  referencesFile(threadId: string, path: string): boolean {
    const rows = this.db.prepare("SELECT text FROM turn_text WHERE thread_id = ? AND role = 'assistant'").iterate(threadId);
    for (const row of rows) if (referencesFile(String(row.text), path)) return true;
    return false;
  }

  /**
   * Reads turns out of the log into the searchable text: all of them, one thread's, or one turn
   * from a sequence number on. Turns the hub started itself (a handover, a teammate's message)
   * are not the user's words and are left out; what the agent answered is kept either way.
   */
  indexText(threadId?: string, turnId?: string, afterSeq = 0): void {
    const rows = this.db
      .prepare(`SELECT thread_id, turn_id, type, ts, data FROM events
        WHERE type IN ('turn.started', 'message.delta', 'message.replace') AND turn_id IS NOT NULL AND seq > ?
        ${threadId ? 'AND thread_id = ?' : ''} ${turnId ? 'AND turn_id = ?' : ''} ORDER BY thread_id, seq`)
      .iterate(...[afterSeq, ...(threadId ? [threadId] : []), ...(turnId ? [turnId] : [])]) as Iterable<{ thread_id: string; turn_id: string; type: string; ts: string; data: string }>;
    const said = new Map<string, { threadId: string; turnId: string; ts: string; text: string }>();
    const flush = () => { for (const a of said.values()) this.saveText(a.threadId, a.turnId, 'assistant', a.ts, a.text); said.clear(); };
    let current = '';
    for (const row of rows) {
      if (row.thread_id !== current) { flush(); current = row.thread_id; }
      const data = JSON.parse(row.data) as Record<string, unknown>;
      if (row.type === 'turn.started') {
        const text = typeof data.displayText === 'string' ? data.displayText : data.text;
        if (typeof text === 'string' && !data.handover && !data.coordination) this.saveText(row.thread_id, row.turn_id, 'user', row.ts, text);
        continue;
      }
      const answer = said.get(row.turn_id) ?? { threadId: row.thread_id, turnId: row.turn_id, ts: row.ts, text: '' };
      answer.text = row.type === 'message.replace' ? replaceMessageTail(answer.text, data)
        : answer.text + (typeof data.text === 'string' ? data.text : '');
      said.set(row.turn_id, answer);
    }
    flush();
  }

  /** Read only recent human display text; background prompts and assistant/tool bodies stay unread. */
  userTurns(threadId: string, limit = 20, since = ''): Array<{ ts: string; text: string }> {
    return this.db.prepare(`SELECT tt.ts, substr(tt.text, 1, 120) AS text FROM turn_text tt
      WHERE tt.thread_id = ? AND tt.role = 'user' AND tt.ts >= ?
        AND EXISTS (SELECT 1 FROM events e WHERE e.thread_id = tt.thread_id AND e.turn_id = tt.turn_id
          AND e.type = 'turn.started' AND NOT (
            coalesce(json_extract(e.data, '$.background'), 0) OR coalesce(json_extract(e.data, '$.scheduled'), 0)
            OR coalesce(json_extract(e.data, '$.review'), 0) OR coalesce(json_extract(e.data, '$.proactive'), 0)
            OR json_type(e.data, '$.coordination') IS NOT NULL OR json_type(e.data, '$.handover') IS NOT NULL))
      ORDER BY tt.ts DESC, tt.rowid DESC LIMIT ?`).all(threadId, since, limit) as Array<{ ts: string; text: string }>;
  }

  /**
   * Turns in which every word of the query was said, newest first: at most three a thread, so one
   * long thread does not crowd out the rest. Words are matched as they are written, anywhere in
   * the text, which is what works for Chinese; case is ignored for Latin letters.
   */
  searchText(query: string, limit = 40): SearchHit[] {
    const terms = [...new Set(query.toLowerCase().split(/\s+/).filter(Boolean))].slice(0, 6);
    if (terms.join('').length < 2) return [];
    const rows = this.db
      .prepare(`SELECT tt.thread_id, tt.turn_id, tt.role, tt.ts, tt.text, t.title, t.agent, t.cwd, t.archived_at
        FROM turn_text tt JOIN threads t ON t.id = tt.thread_id
        WHERE ${terms.map(() => 'instr(lower(tt.text), ?) > 0').join(' AND ')} ORDER BY tt.ts DESC LIMIT 600`)
      .all(...terms) as Array<{ thread_id: string; turn_id: string; role: string; ts: string; text: string; title: string; agent: string; cwd: string; archived_at: string | null }>;
    const perThread = new Map<string, number>();
    const hits: SearchHit[] = [];
    for (const row of rows) {
      const seen = perThread.get(row.thread_id) ?? 0;
      if (seen >= 3) continue;
      perThread.set(row.thread_id, seen + 1);
      hits.push({ threadId: row.thread_id, turnId: row.turn_id, role: row.role === 'user' ? 'user' : 'assistant', ts: row.ts,
        snippet: snippetAround(row.text, terms), title: row.title, agent: row.agent, cwd: row.cwd, archived: row.archived_at !== null });
      if (hits.length >= limit) break;
    }
    return hits;
  }

  /** Recover only the hub's explicit local handover envelope, never title similarity. */
  private recoverHandovers(): void {
    const candidates = this.db.prepare(`SELECT t.id, t.cwd, t.created_at,
      (SELECT e.data FROM events e WHERE e.thread_id = t.id AND e.type = 'turn.started' ORDER BY e.seq LIMIT 1) AS first_turn
      FROM threads t WHERE t.handover IS NULL AND t.forked_from IS NULL`).all() as Array<{ id: string; cwd: string; created_at: string; first_turn: string | null }>;
    for (const candidate of candidates) {
      if (!candidate.first_turn) continue;
      let text: unknown;
      try { text = (JSON.parse(candidate.first_turn) as { text?: unknown }).text; } catch { continue; }
      if (typeof text !== 'string') continue;
      const envelope = text.match(/^(?:来源机器：本机（当前 hub）\s+)?# 交接信息\r?\n([\s\S]*?)\r?\n## 接手要求(?:\r?\n|$)/);
      if (!envelope) continue;
      const ids = [...envelope[1].matchAll(/^- Hub 线程 ID：([^\r\n]+)$/gm)];
      if (ids.length !== 1) continue;
      const source = this.getThread(ids[0][1].trim());
      if (!source || source.id === candidate.id || source.cwd !== candidate.cwd || source.createdAt >= candidate.created_at) continue;
      this.db.prepare('UPDATE threads SET handover = ? WHERE id = ?').run(JSON.stringify({ sourceId: source.id, title: source.title, agent: source.agent }), candidate.id);
    }
  }

  teamRecords(): Array<{ id: string; data: string }> {
    this.db.exec('CREATE TABLE IF NOT EXISTS teams (id TEXT PRIMARY KEY, data TEXT NOT NULL)');
    return this.db.prepare('SELECT id, data FROM teams ORDER BY rowid').all() as Array<{ id: string; data: string }>;
  }
  saveTeam(id: string, data: string): void {
    this.db.prepare('INSERT INTO teams (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data').run(id, data);
  }
  deleteTeam(id: string): void { this.db.prepare('DELETE FROM teams WHERE id = ?').run(id); }

  /**
   * Feishu message -> thread. Both the owner's messages and the bot's replies are kept, so replying
   * to either one carries on the same thread. The table is created on first use.
   */
  private feishuTables(): void {
    this.db.exec('CREATE TABLE IF NOT EXISTS feishu_links (message_id TEXT PRIMARY KEY, chat_id TEXT, thread_id TEXT NOT NULL, at INTEGER NOT NULL)');
    this.db.exec('CREATE INDEX IF NOT EXISTS feishu_links_thread ON feishu_links (thread_id, at)');
    this.db.exec('CREATE TABLE IF NOT EXISTS feishu_seen (message_id TEXT PRIMARY KEY, at INTEGER NOT NULL)');
  }
  /** True the first time a message id is seen; Feishu redelivers events, and each message is handled once. */
  feishuSeen(messageId: string): boolean {
    this.feishuTables();
    const now = Date.now();
    this.db.prepare('DELETE FROM feishu_seen WHERE at < ?').run(now - 7 * 86_400_000);
    return this.db.prepare('INSERT OR IGNORE INTO feishu_seen (message_id, at) VALUES (?, ?)').run(messageId, now).changes > 0;
  }
  feishuLink(messageId: string, chatId: string | null, threadId: string): void {
    this.feishuTables();
    this.db.prepare('INSERT OR REPLACE INTO feishu_links (message_id, chat_id, thread_id, at) VALUES (?, ?, ?, ?)').run(messageId, chatId, threadId, Date.now());
  }
  feishuThreadOf(messageId: string): string | null {
    this.feishuTables();
    return (this.db.prepare('SELECT thread_id FROM feishu_links WHERE message_id = ?').get(messageId) as { thread_id: string } | undefined)?.thread_id ?? null;
  }
  /** The newest Feishu message of a thread: where an answer to a turn started on the web goes. */
  feishuLastOf(threadId: string): string | null {
    this.feishuTables();
    return (this.db.prepare('SELECT message_id FROM feishu_links WHERE thread_id = ? ORDER BY at DESC, rowid DESC LIMIT 1').get(threadId) as { message_id: string } | undefined)?.message_id ?? null;
  }

  hubSetting<T>(key: string, fallback: T): T {
    const row = this.db.prepare('SELECT value FROM hub_settings WHERE key = ?').get(key) as { value: string } | undefined;
    return row ? JSON.parse(row.value) as T : fallback;
  }
  saveHubSetting(key: string, value: unknown): void {
    this.db.prepare('INSERT INTO hub_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value').run(key, JSON.stringify(value));
  }

  coordinationRecords(): Array<{ id: string; data: string }> {
    this.db.exec('CREATE TABLE IF NOT EXISTS coordination (id TEXT PRIMARY KEY REFERENCES threads(id) ON DELETE CASCADE, data TEXT NOT NULL)');
    return this.db.prepare('SELECT id, data FROM coordination').all() as Array<{ id: string; data: string }>;
  }

  deleteCoordination(id: string): void { this.db.prepare('DELETE FROM coordination WHERE id = ?').run(id); }

  saveCoordination(id: string, data: string): void {
    this.db.prepare('INSERT INTO coordination (id, data) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data').run(id, data);
  }

  private injectionsReady = false;
  /** What was last attached to this thread's messages, per kind of protocol block; see injections.ts. */
  injection(threadId: string, kind: string): { digest: string; session: string | null; turns: number } | null {
    if (!this.injectionsReady) {
      this.db.exec('CREATE TABLE IF NOT EXISTS injections (thread_id TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE, kind TEXT NOT NULL, digest TEXT NOT NULL, session TEXT, turns INTEGER NOT NULL, PRIMARY KEY (thread_id, kind))');
      this.injectionsReady = true;
    }
    return (this.db.prepare('SELECT digest, session, turns FROM injections WHERE thread_id = ? AND kind = ?').get(threadId, kind) as { digest: string; session: string | null; turns: number } | undefined) ?? null;
  }
  saveInjection(threadId: string, kind: string, value: { digest: string; session: string | null; turns: number }): void {
    this.injection(threadId, kind);
    if (!this.getThread(threadId)) return;
    this.db.prepare('INSERT INTO injections (thread_id, kind, digest, session, turns) VALUES (?, ?, ?, ?, ?) ON CONFLICT(thread_id, kind) DO UPDATE SET digest = excluded.digest, session = excluded.session, turns = excluded.turns')
      .run(threadId, kind, value.digest, value.session, value.turns);
  }
  ageInjection(threadId: string, kind: string): void {
    this.injection(threadId, kind);
    this.db.prepare('UPDATE injections SET turns = turns + 1 WHERE thread_id = ? AND kind = ?').run(threadId, kind);
  }
  clearInjections(threadId: string): void {
    this.injection(threadId, '');
    this.db.prepare('DELETE FROM injections WHERE thread_id = ?').run(threadId);
  }

  close(): void {
    this.history.close();
    this.db.close();
  }

  createThread(
    t: Omit<
      ThreadRecord,
      | 'status'
      | 'doneAt'
      | 'report'
      | 'writing'
      | 'createdAt'
      | 'updatedAt'
      | 'agentSessionId'
      | 'pinned'
      | 'archivedAt'
      | 'sectionId'
      | 'forkedFrom'
      | 'worktree'
      | 'unread'
      | 'hubTurnAt'
      | 'externalAt'
      | 'preview'
      | 'schedule'
    > & {
      forkedFrom?: string | null;
      worktree?: ThreadWorktree | null;
      sectionId?: string | null;
      schedule?: { id: string; name: string } | null;
    },
  ): ThreadRecord {
    const now = new Date().toISOString();
    this.db
      .prepare(
        'INSERT INTO threads (id, agent, cwd, title, status, pinned, permission_mode, model, effort, forked_from, worktree, section_id, created_at, updated_at, handover, schedule, work_mode, speed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      )
      .run(
        t.id,
        t.agent,
        t.cwd,
        t.title,
        'idle',
        0,
        t.permissionMode,
        t.model,
        t.effort,
        t.forkedFrom ?? null,
        t.worktree ? JSON.stringify(t.worktree) : null,
        t.sectionId ?? null,
        now,
        now,
        t.handover ? JSON.stringify(t.handover) : null,
        t.schedule ? JSON.stringify(t.schedule) : null,
        t.workMode ?? 'execute',
        t.speed ?? null,
      );
    return this.getThread(t.id)!;
  }

  handoverMode(thread: ThreadRecord) {
    return thread.handover ? effectiveHandoverMode(this.db, thread.handover, thread.createdAt) : null;
  }

  /** Whether the source of this thread's handover has had a turn since; null without a handover. */
  handoverSourceMoved(thread: ThreadRecord): boolean | null {
    return thread.handover ? handoverSourceMoved(this.db, thread.handover, thread.createdAt) : null;
  }

  detachHandover(id: string): void {
    const thread = this.getThread(id);
    if (!thread?.handover) return;
    this.db.prepare('UPDATE threads SET handover = ? WHERE id = ?').run(JSON.stringify({ ...thread.handover, detached: true }), id);
  }

  getThread(id: string): ThreadRecord | null {
    const row = this.db.prepare('SELECT * FROM threads WHERE id = ?').get(id) as ThreadRow | undefined;
    return row ? toThread(row) : null;
  }

  /** The thread tied to an agent-side session, archived or not. Used to avoid importing one twice. */
  findThreadByAgentSession(agent: string, agentSessionId: string): ThreadRecord | null {
    const row = this.db
      .prepare('SELECT * FROM threads WHERE agent = ? AND agent_session_id = ? LIMIT 1')
      .get(agent, agentSessionId) as ThreadRow | undefined;
    return row ? toThread(row) : this.getThread(this.messageEdits.owner(agent, agentSessionId) ?? '');
  }

  /** Every agent-side session the hub has a thread for: sessionId → agent instance id. The
   *  machine's token meter names the launcher of a transcript through this. */
  agentSessionOwners(): Map<string, string> {
    const rows = this.db
      .prepare('SELECT agent, agent_session_id AS session FROM threads WHERE agent_session_id IS NOT NULL')
      .all() as Array<{ agent: string; session: string }>;
    return new Map(rows.map((row) => [row.session, row.agent]));
  }

  /** Copies a thread's whole event log to another thread, keeping order and timestamps. */
  copyEvents(fromThreadId: string, toThreadId: string, upToSeq = Number.MAX_SAFE_INTEGER): void {
    this.db
      .prepare(
        'INSERT INTO events (thread_id, seq, turn_id, type, ts, data) SELECT ?, seq, turn_id, type, ts, data FROM events WHERE thread_id = ? AND seq <= ?',
      )
      .run(toThreadId, fromThreadId, upToSeq);
    this.indexText(toThreadId);
  }

  /** Runs several writes as one: all of them happen, or none. Also much faster for bulk inserts. */
  transaction<T>(work: () => T): T {
    this.db.exec('SAVEPOINT hub_write');
    try {
      const result = work();
      this.db.exec('RELEASE hub_write');
      return result;
    } catch (err) {
      this.db.exec('ROLLBACK TO hub_write');
      this.db.exec('RELEASE hub_write');
      throw err;
    }
  }

  /** Removes a thread and, through the foreign key, its event log. */
  deleteThread(id: string): void {
    this.db.prepare('DELETE FROM threads WHERE id = ?').run(id);
  }

  /** Active threads by default; pass `archived: true` for the archive instead. */
  listThreads(opts: { archived?: boolean } = {}): ThreadRecord[] {
    const where = opts.archived ? 'archived_at IS NOT NULL' : 'archived_at IS NULL';
    const order = opts.archived ? 'archived_at DESC' : 'pinned DESC, updated_at DESC';
    const rows = this.db.prepare(`SELECT * FROM threads WHERE ${where} ORDER BY ${order}`).all() as unknown as ThreadRow[];
    return rows.map(toThread);
  }

  updateThread(
    id: string,
    patch: Partial<
      Pick<
        ThreadRecord,
        | 'title'
        | 'status'
        | 'agentSessionId'
        | 'pinned'
        | 'workMode'
        | 'speed'
        | 'permissionMode'
        | 'model'
        | 'effort'
        | 'archivedAt'
        | 'sectionId'
        | 'unread'
        | 'preview'
        | 'hubTurnAt'
        | 'externalAt'
      >
    >,
    /**
     * `touch` moves the thread to the top of "recent". Only conversation activity should do that;
     * renaming, pinning, filing or changing a setting must not reorder the list.
     */
    opts: { touch?: boolean } = {},
  ): void {
    const current = this.getThread(id);
    if (!current) return;
    const next = { ...current, ...patch, updatedAt: opts.touch ? new Date().toISOString() : current.updatedAt };
    this.db
      .prepare(
        'UPDATE threads SET title = ?, status = ?, agent_session_id = ?, pinned = ?, permission_mode = ?, model = ?, effort = ?, archived_at = ?, section_id = ?, unread = ?, preview = ?, hub_turn_at = ?, external_at = ?, updated_at = ?, work_mode = ?, speed = ? WHERE id = ?',
      )
      .run(
        next.title,
        next.status,
        next.agentSessionId,
        next.pinned ? 1 : 0,
        next.permissionMode,
        next.model,
        next.effort,
        next.archivedAt,
        next.sectionId,
        next.unread ? 1 : 0,
        next.preview,
        next.hubTurnAt,
        next.externalAt,
        next.updatedAt,
        next.workMode ?? 'execute',
        next.speed ?? null,
        id,
      );
  }

  /**
   * Who set a thread's title: `default` (the hub, from the first message), `user` (renamed by the
   * person) or `auto` (an extension). Null for threads from before this was kept.
   */
  titleBy(id: string): TitleBy | null {
    const row = this.db.prepare('SELECT title_by FROM threads WHERE id = ?').get(id) as { title_by: string | null } | undefined;
    const by = row?.title_by;
    return by === 'default' || by === 'user' || by === 'auto' ? by : null;
  }

  setTitleBy(id: string, by: TitleBy): void {
    this.db.prepare('UPDATE threads SET title_by = ? WHERE id = ?').run(by, id);
  }

  listSections(): SectionRecord[] {
    return this.db.prepare('SELECT id, name, position FROM sections ORDER BY position ASC').all() as unknown as SectionRecord[];
  }

  getSection(id: string): SectionRecord | null {
    const row = this.db.prepare('SELECT id, name, position FROM sections WHERE id = ?').get(id) as SectionRecord | undefined;
    return row ?? null;
  }

  /** New sections go to the end. */
  createSection(id: string, name: string): SectionRecord {
    const row = this.db.prepare('SELECT COALESCE(MAX(position), 0) + 1 AS next FROM sections').get() as { next: number };
    this.db
      .prepare('INSERT INTO sections (id, name, position, created_at) VALUES (?, ?, ?, ?)')
      .run(id, name, row.next, new Date().toISOString());
    return { id, name, position: row.next };
  }

  renameSection(id: string, name: string): void {
    this.db.prepare('UPDATE sections SET name = ? WHERE id = ?').run(name, id);
  }

  /** Removes the section. Its threads are kept and go back to being listed under their project. */
  deleteSection(id: string): void {
    this.db.exec('SAVEPOINT hub_write');
    try {
      this.db.prepare('UPDATE threads SET section_id = NULL WHERE section_id = ?').run(id);
      this.db.prepare('DELETE FROM sections WHERE id = ?').run(id);
      this.db.exec('RELEASE hub_write');
    } catch (err) {
      this.db.exec('ROLLBACK TO hub_write');
      this.db.exec('RELEASE hub_write');
      throw err;
    }
  }

  // ---------- Plan page (todos) ----------

  /** Old Router conversations remain Router threads after a settings change creates a successor. */
  isRouterThread(threadId: string): boolean {
    return !!this.db.prepare(`SELECT 1 FROM events WHERE thread_id = ? AND type = 'turn.started'
      AND json_extract(data, '$.hiddenPrompt') = 1
      AND instr(json_extract(data, '$.text'), ?) > 0 LIMIT 1`).get(threadId, '\n\n<calli-personal-router>\n');
  }

  isReviewThread(threadId: string): boolean {
    return !!this.db.prepare("SELECT 1 FROM events WHERE thread_id = ? AND type = 'turn.started' AND json_extract(data, '$.review') = 1 LIMIT 1").get(threadId);
  }

  hasRunningReview(): boolean {
    return !!this.db.prepare("SELECT 1 FROM threads t WHERE t.status = 'running' AND EXISTS (SELECT 1 FROM events e WHERE e.thread_id = t.id AND e.type = 'turn.started' AND json_extract(e.data, '$.review') = 1) LIMIT 1").get();
  }

  listTodos(): TodoRecord[] {
    return (this.db.prepare('SELECT * FROM todos ORDER BY position ASC, created_at ASC').all() as unknown as TodoRow[]).map(toTodo);
  }

  insertSignal(key: string, payload: unknown, disposition: SignalDisposition, at = new Date().toISOString()): boolean {
    return this.db.prepare('INSERT OR IGNORE INTO signals (key, at, payload, disposition, updated_at) VALUES (?, ?, ?, ?, ?)')
      .run(key, at, JSON.stringify(payload), JSON.stringify(disposition), at).changes > 0;
  }

  getSignal(key: string, materialScope?: string): SignalRecord | null {
    // Only the small state crosses this join; the body has its own explicit read.
    const row = this.db.prepare(`SELECT s.*, m.material FROM signals s LEFT JOIN signal_materials m
      ON m.signal_key = s.key AND m.scope = ? WHERE s.key = ?`).get(materialScope ?? null, key) as SignalRow | undefined;
    return row ? toSignal(row) : null;
  }

  updateSignalDisposition(key: string, disposition: SignalDisposition, at = new Date().toISOString()): void {
    // A Router can associate a matter while a delivery is awaited. Never erase that provenance.
    const previous = this.getSignal(key)?.disposition;
    const matterIds = [...new Set([...(previous?.matterIds ?? []), ...(disposition.matterIds ?? [])])];
    this.db.prepare('UPDATE signals SET disposition = ?, updated_at = ? WHERE key = ?')
      .run(JSON.stringify({ ...disposition, ...(matterIds.length ? { matterIds } : {}) }), at, key);
  }

  listSignals(options: { matterId?: string; pending?: boolean; limit?: number; materialScope?: string } = {}): SignalRecord[] {
    return (this.db.prepare(`SELECT s.*, m.material FROM signals s LEFT JOIN signal_materials m
      ON m.signal_key = s.key AND m.scope = ? WHERE
      (? IS NULL OR EXISTS (SELECT 1 FROM json_each(s.disposition, '$.matterIds') WHERE value = ?))
      AND (? = 0 OR json_extract(s.disposition, '$.pending') = 1)
      ORDER BY s.at DESC, s.key LIMIT ?`).all(options.materialScope ?? null, options.matterId ?? null, options.matterId ?? null,
        options.pending ? 1 : 0, Math.max(1, Math.min(options.limit ?? 100, 1000))) as SignalRow[]).map(toSignal);
  }

  /** Explicit meeting identity, not a title, document token, meeting number or delivery ID. */
  meetingSignals(meetingId: string): SignalRecord[] {
    return (this.db.prepare(`SELECT * FROM signals WHERE json_extract(payload, '$.meeting.meetingId') = ?
      AND json_extract(disposition, '$.outcome') != 'filtered' ORDER BY at DESC, key LIMIT 1000`)
      .all(meetingId) as SignalRow[]).map(toSignal);
  }

  updateSignalMeeting(key: string, meeting: MeetingReference, at = new Date().toISOString()): void {
    const previous = this.getSignal(key)?.payload as { meeting?: MeetingReference } | undefined;
    if (!previous || JSON.stringify(previous.meeting) === JSON.stringify(meeting)) return;
    this.transaction(() => {
      this.db.prepare("UPDATE signals SET payload = json_set(payload, '$.meeting', json(?)), updated_at = ? WHERE key = ?")
        .run(JSON.stringify(meeting), at, key);
      this.db.prepare('DELETE FROM signal_materials WHERE signal_key = ?').run(key);
    });
  }

  /** Called inside the matter write transaction; late artifacts inherit the same provenance. */
  associateSignal(key: string, matterId: string): void {
    const record = this.getSignal(key);
    if (!record) throw new HttpError(404, 'signal not found');
    if (!this.getTodo(matterId)) throw new HttpError(404, 'todo not found');
    const meetingId = (record.payload as { meeting?: MeetingReference }).meeting?.meetingId;
    const related = typeof meetingId === 'string' ? this.meetingSignals(meetingId) : [];
    this.transaction(() => {
      for (const row of new Map([record, ...related].map(row => [row.key, row])).values()) {
        this.updateSignalDisposition(row.key, { ...row.disposition, matterIds: [...new Set([...(row.disposition.matterIds ?? []), matterId])] });
      }
    });
  }

  getSignalMaterial(key: string, scope: string, reference: string): SignalMaterialRead | null {
    const row = this.db.prepare('SELECT material, content FROM signal_materials WHERE signal_key = ? AND scope = ? AND reference = ?')
      .get(key, scope, reference) as { material: string; content: string | null } | undefined;
    return row ? { material: JSON.parse(row.material) as SignalMaterial, content: row.content } : null;
  }

  saveSignalMaterial(key: string, scope: string, reference: string, read: SignalMaterialRead): void {
    this.db.prepare(`INSERT INTO signal_materials (signal_key, scope, reference, material, content) VALUES (?, ?, ?, ?, ?)
      ON CONFLICT(signal_key) DO UPDATE SET scope = excluded.scope, reference = excluded.reference,
      material = excluded.material, content = excluded.content`).run(key, scope, reference, JSON.stringify(read.material), read.content);
  }

  clearSignalMaterials(): void { this.db.prepare('DELETE FROM signal_materials').run(); }

  pruneSignals(now = new Date()): void {
    this.db.prepare('DELETE FROM signals WHERE at < ?').run(new Date(now.getTime() - 90 * 86400000).toISOString());
  }

  activeSubscriptionThreads(): TodoThread[] {
    return this.db.prepare(`SELECT DISTINCT json_extract(s.value, '$.kind') AS kind, json_extract(s.value, '$.ref') AS ref
      FROM todos t, json_each(t.subscriptions) s WHERE t.done = 0 AND t.state != 'done'`).all() as unknown as TodoThread[];
  }

  /** Exact source identity; completed matters remain discoverable for fourteen days. */
  subscribedTodos(thread: TodoThread, now = new Date()): TodoRecord[] {
    return (this.db.prepare(`SELECT t.* FROM todos t WHERE
      (t.state != 'done' AND t.done = 0 OR t.done_at >= ?)
      AND EXISTS (SELECT 1 FROM json_each(t.subscriptions) s
        WHERE json_extract(s.value, '$.kind') = ? AND json_extract(s.value, '$.ref') = ?)
      ORDER BY t.position, t.created_at`).all(new Date(now.getTime() - 14 * 86400000).toISOString(), thread.kind, thread.ref) as unknown as TodoRow[]).map(toTodo);
  }

  getTodo(id: string): TodoRecord | null {
    const row = this.db.prepare('SELECT * FROM todos WHERE id = ?').get(id) as TodoRow | undefined;
    return row ? toTodo(row) : null;
  }

  /** New items go to the end of their level: top level, or after their siblings under a parent. */
  createTodo(id: string, text: string, parentId: string | null, fields: TodoPatch = {}): TodoRecord {
    const subscriptions = todoSubscriptions(fields.subscriptions ?? []);
    const row = this.db
      .prepare('SELECT COALESCE(MAX(position), 0) + 1 AS next FROM todos WHERE parent_id IS ?')
      .get(parentId) as { next: number };
    const now = new Date().toISOString();
    this.db
      .prepare('INSERT INTO todos (id, text, done, parent_id, position, created_at, updated_at) VALUES (?, ?, 0, ?, ?, ?, ?)')
      .run(id, text, parentId, row.next, now, now);
    if (Object.keys(fields).length) {
      this.db.prepare('UPDATE todos SET horizon = ?, owner = ?, due_at = ?, proposed = ?, lane = ?, importance = ?, urgent_until = ?, source = ?, subscriptions = ? WHERE id = ?')
        .run(fields.horizon ?? null, fields.owner ?? 'me', fields.dueAt ?? null, fields.proposed ? 1 : 0, fields.lane ?? null, fields.importance ?? null, fields.urgentUntil ?? null, fields.source === undefined ? null : JSON.stringify(fields.source), JSON.stringify(subscriptions), id);
    }
    return this.getTodo(id)!;
  }

  /** Text edits keep the item where it is; toggling done stamps `done_at` (or clears it). */
  checkTodoRev(id: string, rev: unknown): TodoRecord {
    const todo = this.getTodo(id);
    if (!todo) throw new HttpError(404, 'todo not found');
    if (!Number.isSafeInteger(rev) || Number(rev) < 1) throw new HttpError(400, 'rev is required; read the todo before modifying it');
    if (todo.rev !== rev) throw new HttpError(409, 'todo changed; read it again and retry with its rev');
    return todo;
  }

  updateTodo(id: string, patch: TodoPatch, rev = this.getTodo(id)?.rev): TodoRecord {
    const current = this.checkTodoRev(id, rev);
    if (patch.lane !== undefined && patch.lane !== null && !TODO_LANES.includes(patch.lane)) throw new HttpError(400, 'lane must be you, doing, wait, later or null');
    if (patch.importance !== undefined && patch.importance !== null && !TODO_IMPORTANCES.includes(patch.importance)) throw new HttpError(400, 'importance must be high, normal, low or null');
    if (patch.source !== undefined && patch.source !== null && (!TODO_SOURCE_TYPES.includes(patch.source.type) || !patch.source.ref)) throw new HttpError(400, 'invalid todo source');
    const next = { ...current, ...patch, subscriptions: todoSubscriptions(patch.subscriptions ?? current.subscriptions) };
    if (patch.state !== undefined) next.done = patch.state === 'done';
    else if (patch.done !== undefined) next.state = patch.done ? 'done' : 'open';
    next.doneAt = next.done ? (current.doneAt ?? new Date().toISOString()) : null;
    if (next.state !== 'waiting') next.waitingOn = null;
    next.updatedAt = new Date().toISOString();
    this.db.prepare('UPDATE todos SET horizon = ?, text = ?, done = ?, done_at = ?, owner = ?, state = ?, lane = ?, importance = ?, urgent_until = ?, source = ?, subscriptions = ?, waiting_on = ?, due_at = ?, follow_up_at = ?, follow_up_no_progress = ?, follow_up_paused = ?, proposed = ?, updated_at = ?, rev = rev + 1 WHERE id = ?')
      .run(next.horizon, next.text, next.done ? 1 : 0, next.doneAt, next.owner, next.state, next.lane, next.importance, next.urgentUntil, next.source === null ? null : JSON.stringify(next.source), JSON.stringify(next.subscriptions), next.waitingOn, next.dueAt, next.followUpAt, next.followUpNoProgress, next.followUpPaused ? 1 : 0, next.proposed ? 1 : 0, next.updatedAt, id);
    return this.getTodo(id)!;
  }

  /** 「还在跟」: refreshes only the activity timestamp; no rev bump, nothing else changes. */
  touchTodo(id: string): TodoRecord {
    if (!this.getTodo(id)) throw new HttpError(404, 'todo not found');
    this.db.prepare('UPDATE todos SET updated_at = ? WHERE id = ?').run(new Date().toISOString(), id);
    return this.getTodo(id)!;
  }

  todoNote(id: string): TodoNote {
    const row = this.db.prepare('SELECT text, updated_at, thread_id FROM todo_notes WHERE todo_id = ?').get(id) as { text: string; updated_at: string; thread_id: string | null } | undefined;
    return row ? { text: row.text, updatedAt: row.updated_at, threadId: row.thread_id } : { text: '', updatedAt: null, threadId: null };
  }
  writeTodoNote(id: string, text: string, threadId: string | null, rev: number, bumpRev = true): TodoRecord {
    return this.transaction(() => {
      const todo = this.checkTodoRev(id, rev);
      const changed = this.todoNote(id).text !== text;
      const now = new Date().toISOString();
      this.db.prepare('INSERT INTO todo_notes (todo_id, text, updated_at, thread_id) VALUES (?, ?, ?, ?) ON CONFLICT(todo_id) DO UPDATE SET text = excluded.text, updated_at = excluded.updated_at, thread_id = excluded.thread_id').run(id, text, now, threadId);
      this.db.prepare('UPDATE todos SET has_note = ?, note_preview = ?, note_updated_at = ?, follow_up_no_progress = ?, follow_up_paused = ?, updated_at = ?, rev = rev + ? WHERE id = ?')
        .run(Number(Boolean(text.trim())), todoNotePreview(text), now, changed ? 0 : todo.followUpNoProgress, changed ? 0 : Number(todo.followUpPaused), now, bumpRev ? 1 : 0, id);
      return this.getTodo(id)!;
    });
  }
  todoProposals(id: string): TodoProposal[] {
    return (this.db.prepare('SELECT data FROM todo_proposals WHERE todo_id = ? ORDER BY rowid').all(id) as Array<{ data: string }>).map(r => JSON.parse(r.data));
  }
  saveTodoProposal(proposal: TodoProposal): void {
    this.db.prepare('INSERT INTO todo_proposals (id, todo_id, data) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET data = excluded.data').run(proposal.id, proposal.todoId, JSON.stringify(proposal));
  }
  saveWish(id: string, threadId: string, text: string): void {
    this.db.prepare('INSERT INTO wishes (id, thread_id, text, created_at) VALUES (?, ?, ?, ?)').run(id, threadId, text, new Date().toISOString());
  }
  wishCount(): number { return (this.db.prepare('SELECT COUNT(*) AS count FROM wishes').get() as { count: number }).count; }
  listWishes(): Array<{ id: string; threadId: string; text: string; createdAt: string }> {
    return this.db.prepare('SELECT id, thread_id AS threadId, text, created_at AS createdAt FROM wishes ORDER BY created_at DESC').all() as never;
  }

  deleteTodo(id: string): void {
    this.db.prepare('DELETE FROM todos WHERE id = ?').run(id);
  }

  /**
   * Puts an item under `parentId` (null: top level), just before the sibling `beforeId`, or last
   * when that is null. The new level is renumbered from 1, so positions stay small and unique.
   */
  moveTodo(id: string, parentId: string | null, beforeId: string | null): void {
    this.transaction(() => {
      const siblings = (
        this.db
          .prepare('SELECT id FROM todos WHERE parent_id IS ? AND id != ? ORDER BY position ASC, created_at ASC')
          .all(parentId, id) as Array<{ id: string }>
      ).map((r) => r.id);
      const at = beforeId === null ? -1 : siblings.indexOf(beforeId);
      siblings.splice(at === -1 ? siblings.length : at, 0, id);
      this.db.prepare('UPDATE todos SET parent_id = ?, rev = rev + 1 WHERE id = ?').run(parentId, id);
      const place = this.db.prepare('UPDATE todos SET position = ? WHERE id = ?');
      siblings.forEach((sibling, i) => place.run(i + 1, sibling));
    });
  }

  todoThreadIds(todoId: string): string[] {
    const rows = this.db.prepare('SELECT thread_id FROM todo_threads WHERE todo_id = ? ORDER BY linked_at DESC, rowid DESC').all(todoId) as Array<{ thread_id: string }>;
    return rows.map((r) => r.thread_id);
  }

  linkTodoThread(todoId: string, threadId: string): void {
    this.db.prepare('INSERT INTO todo_threads (todo_id, thread_id, linked_at) VALUES (?, ?, ?) ON CONFLICT(todo_id, thread_id) DO UPDATE SET linked_at = excluded.linked_at').run(todoId, threadId, new Date().toISOString());
  }

  unlinkTodoThread(todoId: string, threadId: string): void {
    this.db.prepare('DELETE FROM todo_threads WHERE todo_id = ? AND thread_id = ?').run(todoId, threadId);
  }

  // ---------- Recent-work summaries ----------

  /** The plan page's "summarize recent days" runs: their threads are summaries, not work. */
  saveSummaryRun(id: string, threadId: string, days: number): void {
    this.db.prepare('INSERT INTO todo_summaries (id, thread_id, days, started_at) VALUES (?, ?, ?, ?)').run(id, threadId, days, new Date().toISOString());
  }
  latestSummaryRun(): { threadId: string; days: number; startedAt: string } | null {
    const row = this.db.prepare('SELECT thread_id, days, started_at FROM todo_summaries ORDER BY started_at DESC, rowid DESC LIMIT 1').get() as { thread_id: string; days: number; started_at: string } | undefined;
    return row ? { threadId: row.thread_id, days: row.days, startedAt: row.started_at } : null;
  }
  summaryThreadIds(): Set<string> {
    return new Set((this.db.prepare('SELECT thread_id FROM todo_summaries').all() as Array<{ thread_id: string }>).map(r => r.thread_id));
  }
  /** Threads that ran a scheduled turn which ended quietly: nothing in them for a summary. */
  quietRunThreadIds(): Set<string> {
    return new Set((this.db.prepare("SELECT DISTINCT thread_id FROM schedule_runs WHERE status = 'quiet' AND thread_id IS NOT NULL").all() as Array<{ thread_id: string }>).map(r => r.thread_id));
  }
  /** Threads with turns since `since`, newest activity first. */
  activeThreadsSince(since: string): Array<{ threadId: string; lastAt: string }> {
    return (this.db.prepare('SELECT thread_id, MAX(ts) AS last FROM turn_text WHERE ts >= ? GROUP BY thread_id ORDER BY last DESC')
      .all(since) as Array<{ thread_id: string; last: string }>).map(r => ({ threadId: r.thread_id, lastAt: r.last }));
  }
  /** The first and last thing asked since `since`, and the end of the latest answer, each bounded. */
  turnTextSince(threadId: string, since: string): { firstAsk: string | null; lastAsk: string | null; lastAnswer: string | null } {
    const one = (role: string, order: string, part: string) => (this.db.prepare(`SELECT ${part} AS text FROM turn_text WHERE thread_id = ? AND role = ? AND ts >= ? ORDER BY ts ${order}, rowid ${order} LIMIT 1`)
      .get(threadId, role, since) as { text: string } | undefined)?.text ?? null;
    return { firstAsk: one('user', 'ASC', 'substr(text, 1, 200)'), lastAsk: one('user', 'DESC', 'substr(text, 1, 200)'),
      lastAnswer: one('assistant', 'DESC', 'substr(text, max(1, length(text) - 299))') };
  }

  /**
   * A tool call's output arrives as snapshots, each superseding the last (see
   * docs/tool-output-storage-design.md). Keeping every snapshot stored one command's output
   * once per streamed fragment — terabytes over months for megabytes of actual text. The
   * store keeps one snapshot per call: an incoming update or end replaces the stored snapshot
   * when it carries every payload field the stored one does. What the live stream receives is
   * decided elsewhere; this only sizes what replay will read back.
   */
  private collapseToolSnapshot(threadId: string, turnId: string | null, type: EventType, data: Record<string, unknown>): Record<string, unknown> {
    if (type !== 'tool.update' && type !== 'tool.end' || typeof data.id !== 'string') return data;
    const stored = this.capOutput({ ...data });
    if (!turnId) return stored;
    // Tool ids are unique within a turn, not across a thread's turns; supersede within the turn
    // only, so one call can never erase another turn's record. Payloads are parsed in JS, guarded:
    // the write path must survive rows whose data was never valid JSON (a pinned guarantee).
    const rows = this.db.prepare(
      "SELECT seq, data FROM events WHERE thread_id = ? AND turn_id = ? AND type = 'tool.update' ORDER BY seq DESC",
    ).all(threadId, turnId) as Array<{ seq: number; data: string }>;
    let prior: { seq: number; data: Record<string, unknown> } | undefined;
    for (const row of rows) {
      try {
        const parsed = JSON.parse(row.data) as Record<string, unknown>;
        if (parsed.id === data.id) { prior = { seq: row.seq, data: parsed }; break; }
      } catch { /* a row with an unreadable payload is never superseded */ }
    }
    if (prior) {
      const before = prior.data;
      const brings = (ev: Record<string, unknown>) => ({
        output: typeof ev.output === 'string' && ev.output.length > 0,
        content: hasDiffParts(ev.content),
      });
      const now = brings(stored);
      const old = brings(before);
      // A payload field the incoming snapshot does not carry (diffs especially) is only in the
      // old row; keep the row then. A call alternates fields rarely, so this stays bounded.
      if (!(!now.output && old.output) && !(!now.content && old.content)) {
        // What names the call (a streamed one learns its command only from an update) goes on
        // with the snapshot that replaces the row, or a reload would show "Terminal" again.
        for (const field of ['title', 'kind', 'input'] as const) {
          if (stored[field] === undefined && before[field] !== undefined) stored[field] = before[field];
        }
        this.db.prepare('DELETE FROM events WHERE thread_id = ? AND seq = ?').run(threadId, prior.seq);
        this.db.prepare('DELETE FROM history_rows WHERE thread_id = ? AND seq = ?').run(threadId, prior.seq);
      }
    }
    return stored;
  }

  /** One-time rewrite of logs written before snapshots were collapsed; see the design doc. */
  collapseStoredToolOutputs(): void {
    if (this.hubSetting('toolOutputCollapsed', false)) return;
    const threads = this.db.prepare('SELECT id FROM threads').all() as Array<{ id: string }>;
    for (const { id } of threads) {
      this.transaction(() => {
        const rows = this.db.prepare(
          "SELECT seq, turn_id, type, data FROM events WHERE thread_id = ? AND type IN ('tool.update', 'tool.end') ORDER BY seq",
        ).all(id) as Array<{ seq: number; turn_id: string | null; type: string; data: string }>;
        // Seq of the stored snapshot per call id, with what payload fields it carries.
        const kept = new Map<string, { seq: number; output: boolean; content: boolean }>();
        const drop: number[] = [];
        for (const row of rows) {
          let data: Record<string, unknown>;
          try {
            data = JSON.parse(row.data) as Record<string, unknown>;
          } catch { continue; } // a row with an unreadable payload is never superseded
          if (typeof data.id !== 'string') continue;
          const key = `${row.turn_id ?? ''}\u0000${data.id}`;
          const prior = kept.get(key);
          const now = { output: typeof data.output === 'string' && data.output.length > 0, content: hasDiffParts(data.content) };
          if (prior && !(!now.output && prior.output) && !(!now.content && prior.content)) drop.push(prior.seq);
          if (row.type === 'tool.update') kept.set(key, { seq: row.seq, ...now });
          else kept.delete(key);
        }
        for (const seq of drop) {
          this.db.prepare('DELETE FROM events WHERE thread_id = ? AND seq = ?').run(id, seq);
          this.db.prepare('DELETE FROM history_rows WHERE thread_id = ? AND seq = ?').run(id, seq);
        }
        // Cap what survived, in place: the cap keeps head and tail, so nothing is re-collapsed.
        for (const { seq } of kept.values()) {
          const row = this.db.prepare('SELECT data FROM events WHERE thread_id = ? AND seq = ?').get(id, seq) as { data: string } | undefined;
          if (!row) continue;
          const data = JSON.parse(row.data) as Record<string, unknown>;
          const stored = this.capOutput(data);
          if (stored !== data) this.db.prepare('UPDATE events SET data = ? WHERE thread_id = ? AND seq = ?').run(JSON.stringify(stored), id, seq);
        }});
    }
    this.saveHubSetting('toolOutputCollapsed', true);
  }

  /** Head and tail of an oversized output, with an honest marker where the middle was. */
  private capOutput(data: Record<string, unknown>): Record<string, unknown> {
    if (typeof data.output !== 'string' || data.output.length <= MAX_OUTPUT_CHARS) return data;
    const omitted = data.output.length - OUTPUT_HEAD_CHARS - OUTPUT_TAIL_CHARS;
    const stored = { ...data };
    stored.output = `${data.output.slice(0, OUTPUT_HEAD_CHARS)}\n[truncated, ${omitted} chars omitted]\n${data.output.slice(-OUTPUT_TAIL_CHARS)}`;
    stored.outputTruncated = true;
    stored.outputOmitted = omitted;
    return stored;
  }

  appendEvent(threadId: string, turnId: string | null, type: EventType, data: Record<string, unknown>): StoredEvent {
    // A savepoint also nests inside bulk import transactions. Publish only committed projections.
    this.db.exec('SAVEPOINT history_event');
    try {
      const ts = new Date().toISOString();
      data = this.collapseToolSnapshot(threadId, turnId, type, data);
      const row = this.db
        .prepare('SELECT MAX(COALESCE((SELECT MAX(seq) FROM events WHERE thread_id=?),0), COALESCE((SELECT last_seq FROM event_sequences WHERE thread_id=?),0)) + 1 AS next')
        .get(threadId, threadId) as { next: number };
      const seq = row.next;
      this.db
        .prepare('INSERT INTO events (thread_id, seq, turn_id, type, ts, data) VALUES (?, ?, ?, ?, ?, ?)')
        .run(threadId, seq, turnId, type, ts, JSON.stringify(data));
      this.db.prepare('INSERT INTO event_sequences VALUES (?, ?) ON CONFLICT(thread_id) DO UPDATE SET last_seq=excluded.last_seq').run(threadId, seq);
      if (turnId && type === 'turn.started') {
        this.turnStarts.set(turnId, seq);
        const text = typeof data.displayText === 'string' ? data.displayText : data.text;
        if (typeof text === 'string' && !data.handover && !data.coordination) this.saveText(threadId, turnId, 'user', ts, text);
      } else if (turnId && (type === 'turn.done' || type === 'turn.error')) {
        // Without the start (the daemon was restarted mid-turn) the whole thread is read for it.
        this.indexText(threadId, turnId, (this.turnStarts.get(turnId) ?? 1) - 1);
        this.turnStarts.delete(turnId);
      }
      // The sidebar's preview line is the newest ask, whatever wrote it — a live turn, an import, a
      // sync from the terminal. Hub-injected handover turns are not the user asking.
      if (type === 'turn.started' && typeof data.text === 'string' && data.text.trim() && !data.handover && !data.coordination) {
        this.db.prepare('UPDATE threads SET preview = ? WHERE id = ?').run(askedText(typeof data.displayText === 'string' ? data.displayText : data.text).slice(0, 80), threadId);
      }
      const event = { threadId, seq, turnId, type, ts, data };
      this.history.record(event);
      this.telemetry.observe(event);
      this.db.exec('RELEASE history_event');
      return event;
    } catch (error) {
      this.db.exec('ROLLBACK TO history_event; RELEASE history_event');
      throw error;
    }
  }

  /** Completed native compactions, by distinct id: a replayed terminal update counts once. */
  compactionSummary(threadId: string): { count: number; last: { ts: string; automatic: boolean } | null } {
    const done = "type='context.compaction' AND json_extract(data, '$.status')='completed'";
    const count = (this.db.prepare(`SELECT COUNT(DISTINCT json_extract(data, '$.id')) AS n FROM events WHERE thread_id=? AND ${done}`).get(threadId) as { n: number }).n;
    if (!count) return { count: 0, last: null };
    const row = this.db
      .prepare(`SELECT ts, json_extract(data, '$.automatic') AS automatic FROM events WHERE thread_id=? AND ${done} ORDER BY seq DESC LIMIT 1`)
      .get(threadId) as { ts: string; automatic: number | null } | undefined;
    return { count, last: row ? { ts: row.ts, automatic: row.automatic === 1 } : null };
  }

  /** Repair a hub-generated prompt's display without changing its agent input or event identity. */
  setTurnDisplayText(threadId: string, seq: number, displayText: string): void {
    const row = this.db.prepare("SELECT * FROM events WHERE thread_id=? AND seq=? AND type='turn.started'").get(threadId, seq) as unknown as EventRow | undefined;
    if (!row) return;
    const event = toEvent(row);
    event.data = { ...event.data, displayText, hiddenPrompt: true };
    this.transaction(() => {
      this.db.prepare('UPDATE events SET data=? WHERE thread_id=? AND seq=?').run(JSON.stringify(event.data), threadId, seq);
      this.history.record(event);
      if (event.turnId && !event.data.handover && !event.data.coordination) {
        this.saveText(threadId, event.turnId, 'user', event.ts, displayText);
        const latest = this.db.prepare("SELECT MAX(seq) AS seq FROM events WHERE thread_id=? AND type='turn.started' AND json_extract(data, '$.handover') IS NULL AND json_extract(data, '$.coordination') IS NULL").get(threadId);
        if (latest?.seq === seq) this.db.prepare('UPDATE threads SET preview=? WHERE id=?').run(askedText(displayText).slice(0, 80), threadId);
      }
    });
  }

  /** Events after a sequence number, oldest first. A negative limit means all of them. */
  listEvents(threadId: string, afterSeq = 0, limit = 5000): StoredEvent[] {
    return [...this.iterateEvents(threadId, afterSeq, limit)];
  }

  /** Read one row at a time so replay can discard superseded tool snapshots immediately. */
  *iterateEvents(threadId: string, afterSeq = 0, limit = -1): IterableIterator<StoredEvent> {
    const rows = this.db
      .prepare('SELECT * FROM events WHERE thread_id = ? AND seq > ? ORDER BY seq ASC LIMIT ?')
      .iterate(threadId, afterSeq, limit);
    for (const row of rows) yield toEvent(row as unknown as EventRow);
  }

  /** The board's state of a thread; neither changes its place in "recent". */
  setThreadProgress(id: string, patch: { doneAt?: string | null; report?: ProgressReport | null }): void {
    if (patch.doneAt !== undefined) this.db.prepare('UPDATE threads SET done_at = ? WHERE id = ?').run(patch.doneAt, id);
    if (patch.report !== undefined) this.db.prepare('UPDATE threads SET report = ? WHERE id = ?').run(patch.report ? JSON.stringify(patch.report) : null, id);
  }

  /** Writing mode's state; like the board's, it does not change the thread's place in "recent". */
  setThreadWriting(id: string, writing: WritingState | null): void {
    this.db.prepare('UPDATE threads SET writing = ? WHERE id = ?').run(writing ? JSON.stringify(writing) : null, id);
  }

  /** The events of one turn, oldest first. */
  listTurnEvents(threadId: string, turnId: string): StoredEvent[] {
    const rows = this.db
      .prepare('SELECT * FROM events WHERE thread_id = ? AND turn_id = ? ORDER BY seq ASC')
      .all(threadId, turnId) as unknown as EventRow[];
    return rows.map(toEvent);
  }

  /** Bounded conversation context for a voice coordinator; never reads tool payloads. */
  voiceContext(threadId: string): Array<{ role: string; text: string }> {
    return (this.db.prepare(`SELECT role, substr(text, 1, 2000) AS text FROM turn_text
      WHERE thread_id = ? ORDER BY ts DESC, rowid DESC LIMIT 6`).all(threadId) as Array<{ role: string; text: string }>).reverse();
  }

  isVoiceSummary(threadId: string, turnId: string): boolean {
    return Boolean(this.db.prepare("SELECT 1 FROM events WHERE thread_id=? AND turn_id=? AND type='turn.started' AND json_extract(data, '$.voiceSummary') IS NOT NULL LIMIT 1").get(threadId, turnId));
  }

  /** Answer text and its boundaries only; large tool arguments, output and reasoning stay on disk. */
  voiceAnswerEvents(threadId: string, turnId: string): StoredEvent[] {
    return (this.db.prepare(`SELECT thread_id, turn_id, seq, type, ts,
      CASE WHEN type IN ('message.delta', 'message.replace')
      THEN json_object('text', json_extract(data, '$.text'), 'previous', json_extract(data, '$.previous')) ELSE '{}' END AS data
      FROM events WHERE thread_id=? AND turn_id=? AND type IN
      ('message.delta', 'message.replace', 'tool.start', 'reasoning.delta', 'approval.request') ORDER BY seq`)
      .all(threadId, turnId) as unknown as EventRow[]).map(toEvent);
  }

  /** Every event of one type in a thread, oldest first. There are few of the types this is asked for. */
  listEventsOfType(threadId: string, type: EventType): StoredEvent[] {
    const rows = this.db
      .prepare('SELECT * FROM events WHERE thread_id = ? AND type = ? ORDER BY seq ASC')
      .all(threadId, type) as unknown as EventRow[];
    return rows.map(toEvent);
  }

  /**
   * One page of a thread's history: the events just before `beforeSeq`, or the end of the log
   * when that is left out. A page begins where a turn begins, so the reader never gets an answer
   * without its question or a tool result without its call. It reaches back to the earliest turn
   * that starts within `budget` events of the page's end; a single turn longer than that still
   * comes whole. At most three turns are included, even when their event counts are small.
   * Whatever precedes the first turn rides along with it.
   */
  listEventsPage(threadId: string, budget: number, beforeSeq?: number): StoredEvent[] {
    return [...this.iterateEventsPage(threadId, budget, beforeSeq)];
  }

  *iterateEventsPage(threadId: string, budget: number, beforeSeq?: number): IterableIterator<StoredEvent> {
    const end =
      beforeSeq ??
      (this.db.prepare('SELECT COALESCE(MAX(seq), 0) + 1 AS next FROM events WHERE thread_id = ?').get(threadId) as { next: number }).next;
    const turnStart = (where: string, order: 'ASC' | 'DESC', ...args: number[]) =>
      (
        this.db
          .prepare(`SELECT seq FROM events WHERE thread_id = ? AND type = 'turn.started' AND ${where} ORDER BY seq ${order} LIMIT 1`)
          .get(threadId, ...args) as { seq: number } | undefined
      )?.seq;
    let start = turnStart('seq < ? AND seq >= ?', 'ASC', end, end - budget) ?? turnStart('seq < ?', 'DESC', end) ?? 1;
    const third = this.db.prepare("SELECT seq FROM events WHERE thread_id = ? AND type = 'turn.started' AND seq < ? ORDER BY seq DESC LIMIT 1 OFFSET 2")
      .get(threadId, end) as { seq: number } | undefined;
    if (third) start = Math.max(start, third.seq);
    if (turnStart('seq < ?', 'DESC', start) === undefined) start = 1;
    const rows = this.db
      .prepare('SELECT * FROM events WHERE thread_id = ? AND seq >= ? AND seq < ? ORDER BY seq ASC')
      .iterate(threadId, start, end);
    for (const row of rows) yield toEvent(row as unknown as EventRow);
  }

  /** The context the agent last reported holding, in tokens; null when it never reported one. */
  lastContextUsed(threadId: string): number | null {
    const row = this.db
      .prepare("SELECT json_extract(data, '$.used') AS used FROM events WHERE thread_id = ? AND type = 'usage.updated' ORDER BY seq DESC LIMIT 1")
      .get(threadId) as { used: number | null } | undefined;
    return typeof row?.used === 'number' ? row.used : null;
  }

  /**
   * How the thread's latest turn failed, when it did: the newest lifecycle event, read through the
   * lifecycle index so a thread list can afford it. Null when the latest turn did not fail.
   */
  lastTurnFailure(threadId: string): { turnId: string; at: string; code: string | null; message: string } | null {
    const row = this.db
      .prepare("SELECT turn_id, ts, type, data FROM events WHERE thread_id = ? AND type IN ('turn.started', 'turn.done', 'turn.error') ORDER BY seq DESC LIMIT 1")
      .get(threadId) as { turn_id: string | null; ts: string; type: string; data: string } | undefined;
    if (!row || row.type !== 'turn.error' || !row.turn_id) return null;
    let data: { code?: unknown; message?: unknown } = {};
    try { data = JSON.parse(row.data) as typeof data; } catch { /* a malformed payload still failed */ }
    return { turnId: row.turn_id, at: row.ts, code: typeof data.code === 'string' ? data.code : null, message: typeof data.message === 'string' ? data.message : '' };
  }

  /** The newest turn starts, ends and failures, newest first: enough to read how the last few turns went. */
  recentLifecycle(threadId: string, limit: number): Array<{ turnId: string; seq: number; type: 'turn.started' | 'turn.done' | 'turn.error'; ts: string; data: Record<string, unknown> }> {
    const rows = this.db
      .prepare("SELECT turn_id, seq, ts, type, data FROM events WHERE thread_id = ? AND type IN ('turn.started', 'turn.done', 'turn.error') ORDER BY seq DESC LIMIT ?")
      .all(threadId, limit) as Array<{ turn_id: string | null; seq: number; ts: string; type: 'turn.started' | 'turn.done' | 'turn.error'; data: string }>;
    return rows.filter(row => row.turn_id).map(row => {
      let data: Record<string, unknown> = {};
      try { data = JSON.parse(row.data) as Record<string, unknown>; } catch { /* a malformed payload still ended the turn */ }
      return { turnId: row.turn_id!, seq: row.seq, type: row.type, ts: row.ts, data };
    });
  }

  /**
   * One turn's tool calls as logged, minus their output, which can be large and says nothing of what a
   * call touched. `fromSeq` is where the turn began: events are indexed by position, not by turn.
   */
  turnToolEvents(threadId: string, turnId: string, fromSeq: number): StoredEvent[] {
    const rows = this.db
      .prepare("SELECT thread_id, turn_id, seq, type, ts, json_remove(data, '$.output') AS data FROM events WHERE thread_id = ? AND seq >= ? AND turn_id = ? AND type IN ('tool.start', 'tool.update', 'tool.end') ORDER BY seq")
      .all(threadId, fromSeq, turnId) as unknown as EventRow[];
    return rows.map(toEvent);
  }

  /** What the agent said last in a turn that began at `fromSeq`: the text after its final tool call, cut at `limit` characters. */
  finalAnswer(threadId: string, turnId: string, fromSeq: number, limit = 2000): string {
    const after = (this.db
      .prepare("SELECT MAX(seq) AS seq FROM events WHERE thread_id = ? AND seq >= ? AND turn_id = ? AND type IN ('tool.start', 'tool.update', 'tool.end')")
      .get(threadId, fromSeq, turnId) as { seq: number | null }).seq ?? fromSeq;
    const rows = this.db
      .prepare("SELECT type, data FROM events WHERE thread_id = ? AND seq > ? AND turn_id = ? AND type IN ('message.delta', 'message.replace') ORDER BY seq")
      .iterate(threadId, after, turnId) as Iterable<{ type: string; data: string }>;
    let text = '';
    for (const row of rows) {
      const data = JSON.parse(row.data) as Record<string, unknown>;
      text = row.type === 'message.replace' ? replaceMessageTail(text, data) : text + (typeof data.text === 'string' ? data.text : '');
      if (text.length >= limit) break;
    }
    return text.slice(0, limit);
  }

  /** The newest approval the agent asked for at or after `fromSeq`, or null when it has not. */
  latestApprovalRequest(threadId: string, fromSeq: number): StoredEvent | null {
    const row = this.db
      .prepare("SELECT * FROM events WHERE thread_id = ? AND seq >= ? AND type = 'approval.request' ORDER BY seq DESC LIMIT 1")
      .get(threadId, fromSeq) as unknown as EventRow | undefined;
    return row ? toEvent(row) : null;
  }

  /** A thread's turns, oldest first, by where each began in the log. */
  listTurns(threadId: string): Array<{ turnId: string; ts: string; seq: number }> {
    const rows = this.db
      .prepare("SELECT seq, turn_id, ts FROM events WHERE thread_id = ? AND type = 'turn.started' AND turn_id IS NOT NULL ORDER BY seq ASC")
      .all(threadId) as Array<{ seq: number; turn_id: string; ts: string }>;
    return rows.map((row) => ({ turnId: row.turn_id, ts: row.ts, seq: row.seq }));
  }

  /** One turn's events, oldest first: from where it began up to where the next one did. */
  turnEvents(threadId: string, turnId: string): StoredEvent[] {
    const turns = this.listTurns(threadId);
    const at = turns.findIndex((t) => t.turnId === turnId);
    if (at < 0) return [];
    const end = turns[at + 1]?.seq ?? Number.MAX_SAFE_INTEGER;
    const rows = this.db
      .prepare('SELECT * FROM events WHERE thread_id = ? AND seq >= ? AND seq < ? AND turn_id = ? ORDER BY seq ASC')
      .all(threadId, turns[at].seq, end, turnId) as unknown as EventRow[];
    return rows.map(toEvent);
  }

  /**
   * The id of the thread's last turn when it never ended (no turn.done or turn.error), else null.
   */
  openTurn(threadId: string): string | null {
    const last = this.db
      .prepare("SELECT seq, turn_id FROM events WHERE thread_id = ? AND type = 'turn.started' ORDER BY seq DESC LIMIT 1")
      .get(threadId) as { seq: number; turn_id: string | null } | undefined;
    if (!last?.turn_id) return null;
    const ended = this.db
      .prepare("SELECT 1 AS yes FROM events WHERE thread_id = ? AND seq > ? AND turn_id = ? AND type IN ('turn.done', 'turn.error') LIMIT 1")
      .get(threadId, last.seq, last.turn_id);
    return ended ? null : last.turn_id;
  }

  // ---------- Scheduled tasks ----------

  listSchedules(): ScheduleRecord[] {
    return (this.db.prepare('SELECT * FROM schedules ORDER BY created_at ASC').all() as unknown as ScheduleRow[]).map(toSchedule);
  }

  getSchedule(id: string): ScheduleRecord | null {
    const row = this.db.prepare('SELECT * FROM schedules WHERE id = ?').get(id) as ScheduleRow | undefined;
    return row ? toSchedule(row) : null;
  }

  createSchedule(s: ScheduleRecord): ScheduleRecord {
    this.db
      .prepare(
        'INSERT INTO schedules (id, name, agent, cwd, prompt, cron, mode, thread_id, permission_mode, enabled, timezone, catch_up, last_fired_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
      )
      .run(s.id, s.name, s.agent, s.cwd, s.prompt, s.cron, s.mode, s.threadId, s.permissionMode, s.enabled ? 1 : 0, s.timezone, s.catchUp ? 1 : 0, s.lastFiredAt, s.createdAt, s.updatedAt);
    return this.getSchedule(s.id)!;
  }

  updateSchedule(id: string, patch: Partial<Omit<ScheduleRecord, 'id' | 'createdAt'>>): ScheduleRecord | null {
    const current = this.getSchedule(id);
    if (!current) return null;
    const next = { ...current, ...patch, updatedAt: new Date().toISOString() };
    this.db
      .prepare('UPDATE schedules SET name = ?, agent = ?, cwd = ?, prompt = ?, cron = ?, mode = ?, thread_id = ?, permission_mode = ?, enabled = ?, timezone = ?, catch_up = ?, last_fired_at = ?, updated_at = ? WHERE id = ?')
      .run(next.name, next.agent, next.cwd, next.prompt, next.cron, next.mode, next.threadId, next.permissionMode, next.enabled ? 1 : 0, next.timezone, next.catchUp ? 1 : 0, next.lastFiredAt, next.updatedAt, id);
    return this.getSchedule(id);
  }

  deleteSchedule(id: string): void {
    this.db.prepare('DELETE FROM schedules WHERE id = ?').run(id);
  }

  /** A schedule's runs, newest first. */
  listTodoRuns(todoId: string): ScheduleRunRecord[] {
    return (this.db.prepare('SELECT * FROM schedule_runs WHERE todo_id = ? ORDER BY started_at DESC LIMIT 50').all(todoId) as unknown as ScheduleRunRow[]).map(toScheduleRun);
  }

  listScheduleRuns(scheduleId: string, limit = 20): ScheduleRunRecord[] {
    return (
      this.db.prepare('SELECT * FROM schedule_runs WHERE schedule_id = ? ORDER BY started_at DESC, id DESC LIMIT ?').all(scheduleId, limit) as unknown as ScheduleRunRow[]
    ).map(toScheduleRun);
  }

  /** The newest run of each schedule, keyed by schedule id. */
  latestScheduleRuns(): Map<string, ScheduleRunRecord> {
    const rows = this.db.prepare('SELECT r.* FROM schedule_runs r JOIN (SELECT schedule_id, MAX(started_at) AS latest FROM schedule_runs GROUP BY schedule_id) m ON r.schedule_id = m.schedule_id AND r.started_at = m.latest').all() as unknown as ScheduleRunRow[];
    const out = new Map<string, ScheduleRunRecord>();
    for (const row of rows) {
      const run = toScheduleRun(row);
      if (!run.scheduleId) continue;
      if (!out.has(run.scheduleId) || out.get(run.scheduleId)!.startedAt < run.startedAt) out.set(run.scheduleId, run);
    }
    return out;
  }

  insertScheduleRun(run: ScheduleRunRecord, noteBefore: string | null = null): ScheduleRunRecord {
    this.db
      .prepare('INSERT INTO schedule_runs (id, schedule_id, status, thread_id, turn_id, started_at, finished_at, message, todo_id, new_thread, note_before_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
      .run(run.id, run.scheduleId, run.status, run.threadId, run.turnId, run.startedAt, run.finishedAt, run.message, run.todoId ?? null, run.newThread ? 1 : 0, noteBefore === null ? null : noteHash(noteBefore));
    // History is for looking at, not bookkeeping: keep the newest slice only.
    const column = run.todoId ? 'todo_id' : 'schedule_id';
    this.db
      .prepare(`DELETE FROM schedule_runs WHERE ${column} = ? AND id NOT IN (SELECT id FROM schedule_runs WHERE ${column} = ? ORDER BY started_at DESC, id DESC LIMIT ?)`)
      .run(run.todoId ?? run.scheduleId, run.todoId ?? run.scheduleId, SCHEDULE_RUN_HISTORY);
    return run;
  }

  /** Consume a follow-up's note fingerprint once; return whether it just paused the item. */
  finishTodoFollowUp(runId: string): boolean {
    return this.transaction(() => {
      const run = this.db.prepare('SELECT todo_id, note_before_hash FROM schedule_runs WHERE id = ?').get(runId) as { todo_id: string | null; note_before_hash: string | null } | undefined;
      if (!run?.todo_id || run.note_before_hash === null) return false;
      this.db.prepare('UPDATE schedule_runs SET note_before_hash = NULL WHERE id = ?').run(runId);
      const todo = this.getTodo(run.todo_id);
      if (!todo) return false;
      const count = noteHash(this.todoNote(todo.id).text) === run.note_before_hash ? Math.min(3, todo.followUpNoProgress + 1) : 0;
      const paused = count >= 3;
      if (count !== todo.followUpNoProgress || paused !== todo.followUpPaused || paused && todo.followUpAt !== null) {
        this.updateTodo(todo.id, { followUpNoProgress: count, followUpPaused: paused, ...(paused ? { followUpAt: null } : {}) });
      }
      return paused && !todo.followUpPaused;
    });
  }

  updateScheduleRun(id: string, patch: Partial<Pick<ScheduleRunRecord, 'status' | 'finishedAt' | 'message' | 'threadId'>>): void {
    const sets: string[] = [];
    const args: Array<string | number | null> = [];
    for (const [key, column] of [['status', 'status'], ['finishedAt', 'finished_at'], ['message', 'message'], ['threadId', 'thread_id']] as const) {
      if (patch[key] !== undefined) {
        sets.push(`${column} = ?`);
        args.push(patch[key]);
      }
    }
    if (sets.length === 0) return;
    args.push(id);
    this.db.prepare(`UPDATE schedule_runs SET ${sets.join(', ')} WHERE id = ?`).run(...args);
  }

  /** A run that is still going, if the schedule has one. */
  runningScheduleRun(scheduleId: string): ScheduleRunRecord | null {
    const row = this.db
      .prepare("SELECT * FROM schedule_runs WHERE schedule_id = ? AND status = 'running' ORDER BY started_at DESC, id DESC LIMIT 1")
      .get(scheduleId) as ScheduleRunRow | undefined;
    return row ? toScheduleRun(row) : null;
  }

  /** Every run still marked running: what a restart has to close out. */
  runningScheduleRuns(): ScheduleRunRecord[] {
    return (this.db.prepare("SELECT * FROM schedule_runs WHERE status = 'running'").all() as unknown as ScheduleRunRow[]).map(toScheduleRun);
  }

  findScheduleRunByTurn(turnId: string): ScheduleRunRecord | null {
    const row = this.db.prepare('SELECT * FROM schedule_runs WHERE turn_id = ? LIMIT 1').get(turnId) as ScheduleRunRow | undefined;
    return row ? toScheduleRun(row) : null;
  }
}
