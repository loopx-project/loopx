import type { WorkProjection } from './work.js';
// Browser side of the contract: what a built-in extension's view receives from the host.
// Views import nothing from packages/web or packages/daemon; everything they need is on `WebHost`.
// Styling: only the variables at the top of packages/web/src/styles.css. No custom colours.

import type { ComponentType, ReactNode } from 'react';
import type { ExtensionManifest, HostIconName } from './manifest.js';
import type { WebHostMemory } from './memory.js';
import type { ThreadSummary } from './summary.js';

export type Locale = 'zh' | 'en' | 'ja';

/**
 * - `live`: the event stream is connected; summaries are current.
 * - `reconnecting`: the stream dropped and the host is retrying; show 「可能已过时」.
 * - `offline`: the hub cannot be reached, or this is the demo without a hub.
 */
export type ConnectionState = 'live' | 'reconnecting' | 'offline';

export interface AgentInfo {
  id: string;
  label: string;
  available: boolean;
  /** The permission modes the agent offers, when known: one without `ask` cannot be held to answering only. */
  permissionModes?: string[];
}

export interface Unsubscribe {
  (): void;
}

export interface WebHostThreads {
  /** Current non-archived threads on the connected machine, team members excluded. */
  snapshot(): ThreadSummary[];
  /** Called with the full new list whenever any summary changes. Returns an unsubscribe function. */
  subscribe(listener: (threads: ThreadSummary[]) => void): Unsubscribe;
  /** Marks a thread done or not, as the person's own click asks. Routed to the thread's machine. */
  setDone(threadId: string, done: boolean): Promise<void>;
  /**
   * Places a thread in a board column by hand, as the person's own drag or menu asks; the agent's
   * next progress report replaces it. Needs `progress.report`. Clears done. Routed to the thread's machine.
   */
  setStage(threadId: string, stage: 'backlog' | 'doing' | 'blocked' | 'review'): Promise<void>;
}

/**
 * A machine whose threads the snapshot holds. One entry (the connected machine) unless the
 * sidebar shows every machine. `cached`: last read shown while asking again; `failed`/`offline`:
 * what was last read, possibly stale. Changes are announced through `threads.subscribe`.
 */
export interface MachineState {
  id: string | null;
  label: string;
  state: 'live' | 'cached' | 'loading' | 'failed' | 'offline';
  /** ISO time of the last successful read, null before one. */
  observedAt: string | null;
}

/** For extensions whose manifest declares `assistant.ask`. */
export interface WebHostAssistant {
  /**
   * Hands the text to the person's assistant as their message and opens its page, where the reply
   * appears. Until the permission is granted the host first asks the person in its own dialog;
   * rejects when they decline. Thread links in the text use `threadLink`.
   */
  ask(text: string): Promise<void>;
  /** A Markdown link target the assistant's reply opens as that thread, on its machine. */
  threadLink(threadId: string): string;
}

export interface WebHostConnection {
  state(): ConnectionState;
  /**
   * ISO time of the last successful thread list read, or null before the first one.
   * Null also covers the demo without a hub, where the host shows its own banner:
   * views warn 「可能已过时」 only when `state()` is not `live` and this is not null.
   */
  observedAt(): string | null;
  subscribe(listener: (state: ConnectionState) => void): Unsubscribe;
}

export interface WebHostStorage {
  /** Per device, namespaced to the extension. */
  get(key: string): string | null;
  set(key: string, value: string | null): void;
}

export interface PopoverProps {
  /** Accessible name of the panel. */
  label: string;
  trigger: ReactNode;
  triggerClassName?: string;
  triggerTitle?: string;
  align?: 'start' | 'end';
  /** Wide-screen placement; phones always use a bottom sheet. Defaults to above. */
  placement?: 'above' | 'below';
  children: (close: () => void) => ReactNode;
}

export interface PopoverOptionProps {
  multiple?: boolean;
  selected: boolean;
  onSelect: () => void;
  children: ReactNode;
}

export interface ConfirmProps {
  title: string;
  message?: string;
  /** Names the action, e.g. 「标为已完成」, never 「确定」. */
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Host components. They follow theme, density and phone layout like core screens. */
export interface WebHostUi {
  WorkSummary: ComponentType<{ items: WorkProjection[] }>;
  /** Markdown without raw HTML, external media or active links. */
  Markdown: ComponentType<{ text: string }>;
  Icon: ComponentType<{ name: HostIconName | 'pin' | 'clock' | 'worktree' | 'fork' | 'team' | 'check' | 'chevron' | 'close' | 'plus'; size?: number }>;
  /** The agent instance's mark and, with `label`, its name. */
  AgentMark: ComponentType<{ agentId: string; size?: number; label?: boolean }>;
  /** 「3 分钟前」 style text that updates itself. */
  RelativeTime: ComponentType<{ iso: string }>;
  /** Panel above the trigger on desktop, bottom sheet on phones. */
  Popover: ComponentType<PopoverProps>;
  /** A selectable row inside a Popover. */
  PopoverOption: ComponentType<PopoverOptionProps>;
  /** The host's empty-state illustration with one line of text. */
  EmptyState: ComponentType<{ text: string }>;
  /** The host's modal asking once before an action; render it only while asking. */
  Confirm: ComponentType<ConfirmProps>;
}

export interface WebHostNavigate {
  /** Leave the view and open the thread, as clicking it in the sidebar would. */
  openThread(threadId: string): void;
  /** Wide screens: the thread opens in a panel beside the view, which keeps its place; phones open it as `openThread`. */
  peekThread(threadId: string): void;
  /** The host's own menu for the thread (board status, pin, archive…), at a point on screen. */
  threadMenu(threadId: string, at: { x: number; y: number }): void;
  /** Opens a new conversation's draft, in this project directory and on this machine when given. */
  newThread(options?: { project?: string; machine?: string | null }): void;
}

export interface WebHostRealtimeSession {
  readonly id: string;
  readonly threadId: string;
  readonly status: 'connecting' | 'connected' | 'closed' | 'error';
  readonly isMuted: boolean;
}

export interface WebHostRealtime {
  activeSession(): WebHostRealtimeSession | null;
  subscribe(listener: (session: WebHostRealtimeSession | null) => void): Unsubscribe;
}

/** 自动取名's settings, as the hub keeps them. */
export interface AutoTitleSettings {
  /** The agent instance that names threads; null until the person picks one. */
  agent: string | null;
  /** Its model; null for the agent's default. */
  model: string | null;
  /** The person's naming rules, sent as written. */
  rules: string;
  /** Name when the first message is sent, or once the first turn has its answer. */
  trigger: 'sent' | 'finished';
  /** Name once more after the third turn, while the title is still an automatic one. */
  again: boolean;
}

export interface AutoTitleStatus {
  last: { threadId: string; title: string; at: string } | null;
  error: { threadId: string; message: string; at: string } | null;
}

/** A backlog pass in progress: how many threads it is naming, and how many are done. */
export interface AutoTitlePass {
  total: number;
  done: number;
  at: string;
}

/** Only for the built-in 自动取名 extension; others get nothing here. */
export interface WebHostAutotitle {
  load(): Promise<{ settings: AutoTitleSettings; status: AutoTitleStatus; pass: AutoTitlePass | null; defaultRules: string }>;
  save(patch: Partial<AutoTitleSettings>): Promise<AutoTitleSettings>;
  /** The title the rules give a thread now, without renaming it. */
  preview(threadId: string): Promise<{ title: string }>;
  /** Name every live thread still carrying a default title; the answer says how many were queued. */
  organize(): Promise<{ queued: number }>;
  /** The models an agent instance offers, for choosing which one names. */
  models(agentId: string): Promise<{ id: string; label: string }[]>;
}

export interface WebHost {
  extensionId: string;
  autotitle?: WebHostAutotitle;
  threads: WebHostThreads;
  /** The machines in `threads.snapshot()`. */
  machines(): MachineState[];
  assistant?: WebHostAssistant;
  workSources: { snapshot(): WorkProjection[]; subscribe(listener: (items: WorkProjection[]) => void): Unsubscribe };
  memory: WebHostMemory;
  connection: WebHostConnection;
  agents(): AgentInfo[];
  navigate: WebHostNavigate;
  locale(): Locale;
  storage: WebHostStorage;
  ui: WebHostUi;
  /** True on screens ≤ 640px wide; updates on resize through a re-render. */
  narrow: boolean;
  /** Controlled realtime voice status and subscription; does not expose SDP, raw RPC, or tokens. */
  realtime?: WebHostRealtime;
}

export interface ExtensionViewProps {
  host: WebHost;
}

/** What a built-in extension's web entry exports as default. */
export interface WebExtensionModule {
  manifest: ExtensionManifest;
  /** Keyed by `ViewContribution.id`. Optional for extensions without view contributions. */
  views?: Record<string, ComponentType<ExtensionViewProps>>;
}
