// Every hook between Calli and its extensions. See docs/extensions-design.md, 「hook 清单与首版实现状态」.
//
// Three shapes, no interception:
// - Notification: host → extension, after the fact, delivered asynchronously. The core never waits.
// - Request: the host asks an extension for something it owns (a view, a status line, a decision).
//   Only the extension's own part waits; sending and approvals never do.
// - Host API: extension → host, limited by permissions.
// There is no "before" hook: extensions cannot veto or rewrite sends, approvals, new threads,
// messages or prompts.
//
// Status tags in the comments:
//   @status v1       implemented by the first host
//   @status planned  name and shape reserved; hosts reject or ignore it for now

import type { MemoryDocument, MemoryIndex, MemorySource } from './memory.js';
import type { ExtensionManifest, LocalizedText } from './manifest.js';
import type { Permission } from './permissions.js';
import type { ThreadSummary, TurnSummary, WaitSummary } from './summary.js';

// ---------------------------------------------------------------- envelope

/** Every notification carries where it sits in the stream, so extensions can spot gaps and restarts. */
export interface Envelope<T extends NotificationHook = NotificationHook> {
  hook: T;
  /** Changes every daemon start. A new value means in-memory state was lost: re-read snapshots. */
  generation: string;
  /** Per-thread event sequence for thread-scoped hooks, else 0. Delivered in order within a thread. */
  seq: number;
  ts: string;
  data: NotificationPayloads[T];
}

// ---------------------------------------------------------------- notifications

/**
 * Delivery rule: `extension.activated`, `extension.deactivating` and `host.resync` always reach the
 * extension, declared or not, so it can never miss a lifecycle change or a lost-events signal.
 * Every other notification is delivered only when listed in `manifest.subscribes`, and
 * thread-scoped ones also need `events.summary`.
 */
export interface NotificationPayloads {
  // Extension itself
  /** @status v1 — installed and enabled, or enabled again. Start here, then read `threads.list`. */
  'extension.activated': { extensionId: string; generation: string };
  /** @status v1 — about to be disabled or uninstalled. Best effort; the host cleans up regardless. */
  'extension.deactivating': { extensionId: string; reason: 'disabled' | 'uninstalled' | 'stopping' };
  /** @status planned */
  'extension.settingsChanged': { extensionId: string };

  // Machine
  /** @status v1 — once per daemon start, after restart recovery. Extensions enabled later do not get it. */
  'machine.ready': { generation: string };
  /** @status planned — best effort. */
  'machine.stopping': Record<string, never>;

  // Agent instances
  /** @status planned — instance added, removed, or availability/capabilities changed. */
  'agent.changed': { agentId: string };

  // Threads
  /** @status v1 */
  'thread.created': {
    thread: ThreadSummary;
    /** `fork` and the finer origins are planned; v1 reports `new`, `import` or `unknown`. */
    origin: 'new' | 'import' | 'fork' | 'handover' | 'team' | 'schedule' | 'unknown';
  };
  /** @status v1 — `fields` lists what changed when the host knows, else it is empty. */
  'thread.changed': { thread: ThreadSummary; fields: (keyof ThreadSummary)[] };
  /** @status planned */
  'thread.deleted': { threadId: string };

  // Turns
  /** @status v1 */
  'turn.started': { turn: TurnSummary };
  /** @status v1 — one per turn, success or not. Summary fields an agent does not report are absent, not 0. */
  'turn.finished': { turn: Required<Pick<TurnSummary, 'outcome' | 'finishedAt'>> & TurnSummary };

  // Waiting for a person
  /** @status v1 — approval, question or plan confirmation. No hook can answer it. */
  'attention.raised': { wait: WaitSummary };
  /** @status v1 */
  'attention.resolved': { wait: WaitSummary; how: 'answered' | 'cancelled' | 'turn-ended' };

  // Next turn, handover, teams
  /** @status planned */
  'schedule.runFinished': { scheduleId: string; threadId: string | null; outcome: 'done' | 'error' | 'skipped' };
  /** @status planned */
  'handover.created': { fromThreadId: string; toThreadId: string };
  /** @status planned */
  'team.changed': { rootThreadId: string };

  /**
   * @status v1 — the delivery queue overflowed or the host restarted.
   * Re-read the current state with `threads.list`. Missed events are not replayed.
   */
  'host.resync': { reason: 'overflow' | 'restart' };
}

export type NotificationHook = keyof NotificationPayloads;

// ---------------------------------------------------------------- requests

/** Declarative content the host draws. Extensions never ship DOM or CSS. */
export type Tone = 'neutral' | 'positive' | 'warn' | 'danger';

export interface StatusLine {
  text: string;
  tone?: Tone;
  /** 0..1, with where the number comes from. */
  progress?: { value: number; source: 'agent' | 'verified' };
}

export interface RequestContext {
  requestId: string;
  /** ISO deadline. Late answers are dropped. */
  deadline: string;
  signal: AbortSignal;
}

export interface RequestPayloads {
  'worksource.snapshot': {
    in: { binding: import('./work.js').WorkBinding };
    out: import('./work.js').WorkSnapshot;
  };
  /** Implemented for external work sources: installed tool and verified version. */
  'extension.detect': { in: Record<string, never>; out: { ok: boolean; version?: string; missing?: string } };
  /** External work sources validate installation settings before saving. */
  'extension.validateSettings': { in: { settings: Record<string, unknown> }; out: { errors: Record<string, string> } };
  /** @status planned — per-instance report of one capability, e.g. memory. */
  'capability.status': {
    in: { capability: string };
    out: { instances: { agentId: string; state: 'unconfigured' | 'unverified' | 'available' | 'unavailable'; reason?: string; source?: string }[] };
  };
  /** @status planned — one line above the composer; the host merges all extensions into one row. */
  'ui.threadStatus': { in: { threadId: string }; out: StatusLine | null };
  /** @status planned */
  'ui.threadBadge': { in: { threadId: string }; out: { label: string; tone?: Tone } | null };
  /** @status planned */
  'ui.threadActions': { in: { threadId: string }; out: { id: string; label: string; confirm?: boolean }[] };
  /** @status planned */
  'ui.commands': { in: Record<string, never>; out: { id: string; label: string }[] };
  /** @status planned */
  'ui.panel': { in: { threadId: string }; out: { title: string; sections: { title?: string; markdown: string }[] } };
  /** @status planned — declarative page for process extensions. Built-in v1 views render React through `./web`. */
  'ui.page': { in: { viewId: string }; out: { sections: { title?: string; markdown: string }[] } };
  /** @status planned */
  'ui.settings': { in: Record<string, never>; out: { fields: { key: string; label: string; kind: 'text' | 'toggle' | 'secret' }[] } };
  /** @status planned — needs `progress.read`. Replanning replaces the whole snapshot. */
  'progress.snapshot': {
    in: { threadId: string; turnId?: string };
    out: { source: 'agent'; items: { text: string; status: 'pending' | 'in_progress' | 'completed' }[]; updatedAt: string } | null;
  };
  /** @status planned — needs `toolresult.read`. */
  'card.forToolResult': { in: { threadId: string; turnId: string; toolCallId: string }; out: { title: string; markdown: string } | null };
  /** @status planned — a visible, editable section the user may include when handing over. */
  'handover.context': { in: { threadId: string }; out: { title: string; markdown: string } | null };
  /**
   * @status planned — only for the extension holding the thread's drive right.
   * A timeout pauses driving; it never counts as "nothing to send".
   */
  'driver.next': {
    in: { threadId: string; version: string };
    out: { send: string } | { waitUntil: string } | { needsPerson: string } | { stop: string };
  };
  /** @status planned — side effect; retries reuse `deliveryId`, duplicates remain possible. */
  'channel.deliver': {
    in: { deliveryId: string; threadId: string; title: string; kind: 'approval' | 'question' | 'plan' | 'error' | 'done'; link: string };
    out: { delivered: boolean };
  };
}

export type RequestHook = keyof RequestPayloads;

/** How the host treats a request that times out or fails. */
export type RequestFailureMode =
  /** Read-only: hide the slot or show 「暂时无法获取」, retry on the next refresh. */
  | 'degrade'
  /** `driver.next`: pause driving the thread and say why. */
  | 'pause'
  /** `channel.deliver`: outcome unknown; bounded retries with the same delivery id. */
  | 'unknown';

// ---------------------------------------------------------------- host API

export interface HostApi {
  /** @status v1 — needs `memory.read`; groups instances using the same native store. */
  'memory.sources': { in: Record<string, never>; out: MemorySource[] };
  /** @status v1 — needs `memory.read`; bounded document inventory, not a count of memories. */
  'memory.list': { in: { sourceId: string }; out: MemoryIndex };
  /** @status v1 — needs `memory.read`; generated documents only, no arbitrary paths. */
  'memory.read': { in: { sourceId: string; fileId: string }; out: MemoryDocument };

  /** @status v1 — needs `events.summary`. */
  'events.subscribe': { in: { hooks: NotificationHook[] }; out: { generation: string } };
  /** @status v1 — needs `events.summary`. Current state only; it does not replay history. */
  'threads.list': { in: { archived?: boolean }; out: { generation: string; threads: ThreadSummary[] } };
  /** @status v1 — needs `events.summary`. */
  'threads.get': { in: { threadId: string }; out: ThreadSummary | null };
  /** @status planned — needs `events.summary`. */
  'turns.summary': { in: { threadId: string; turnId: string }; out: TurnSummary | null };
  /** @status planned — needs `transcript.read`; user messages and replies only, never reasoning. */
  'turns.transcript': { in: { threadId: string; turnId: string }; out: { role: 'user' | 'agent'; text: string }[] };
  /** @status planned — needs `files.read`. */
  'files.read': { in: { threadId: string; path: string }; out: { text: string; truncated: boolean } };
  /** External work sources: needs `commands.run`; arguments come from saved settings and bindings. */
  'commands.run': { in: { command: string; bindingId?: string }; out: { code: number; stdout: string; stderr: string } };
  /** @status planned — needs `notify`. */
  notify: { in: { dedupeKey: string; threadId?: string; title: string; kind: 'approval' | 'question' | 'plan' | 'error' | 'done' }; out: Record<string, never> };
  /** @status planned — refetch this extension's content in one slot. */
  'ui.invalidate': { in: { slot: RequestHook; threadId?: string }; out: Record<string, never> };
  /** @status v1 — small values in the extension's own namespace. In the browser this is per device. */
  'storage.get': { in: { key: string }; out: { value: string | null } };
  /** @status v1 */
  'storage.set': { in: { key: string; value: string | null }; out: Record<string, never> };
  /** @status planned — needs `task.control`; one send bound to one user click. Never for auto-driving. */
  'threads.send': { in: { threadId: string; text: string; clickToken: string }; out: { turnId: string | null } };
}

export type HostMethod = keyof HostApi;

/** Which permission each host method needs. `null` means none. */
export type HostMethodPermission = { [M in HostMethod]: Permission | null };

// ---------------------------------------------------------------- daemon-side extensions

/**
 * What an in-process (built-in) extension implements on the daemon side.
 * Process extensions speak the same names over JSON-RPC on stdio (planned).
 */
export interface DaemonExtension {
  manifest: ExtensionManifest;
  /** Called for each subscribed notification, in order per thread. Must not throw; errors are logged. */
  notify?<T extends NotificationHook>(event: Envelope<T>): void;
  /** Planned: answers to declared requests. */
  request?<T extends RequestHook>(hook: T, input: RequestPayloads[T]['in'], ctx: RequestContext): Promise<RequestPayloads[T]['out']>;
}

/** Text used in install dialogs for each permission. */
export type PermissionDescriptions = Record<Permission, LocalizedText>;
