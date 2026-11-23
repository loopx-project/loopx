import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import type { PermissionMode } from '../types.js';

/** What a Goal conversation may do. Fixed when the thread is created; changing it means a new thread. */
export const LOOPX_PERMISSIONS = ['read_only', 'workspace_auto'] as const;
export type LoopxPermission = (typeof LOOPX_PERMISSIONS)[number];

/** The words the core's goal.create review uses for the same two choices. */
const CORE_PERMISSION: Record<LoopxPermission, string> = { read_only: 'read_only', workspace_auto: 'workspace_write_on_confirmation' };
export const corePermission = (p: LoopxPermission): string => CORE_PERMISSION[p];
export const fromCorePermission = (p: unknown): LoopxPermission | null =>
  (Object.keys(CORE_PERMISSION) as LoopxPermission[]).find((k) => CORE_PERMISSION[k] === p) ?? null;

/**
 * The part of a thread that makes it a LoopX Goal conversation. A thread without a binding is an
 * ordinary conversation and runs exactly as it does in Calli: nothing else in the daemon knows what
 * a Goal is.
 */
export interface LoopxBinding {
  goalId: string;
  /** The agent the Goal registered with LoopX (codex, claude-code), not this daemon's instance name. */
  goalAgentId: string;
  /** The core's address of this Agent's conversation channel, kept so a thread can be matched to its Goal. */
  managedAgentId: string | null;
  goalInstanceId: string | null;
  /** The Goal's registered project root, which is also the thread's working directory. */
  workspace: string;
  permission: LoopxPermission;
  /** The creation proposal that made this Goal; a retried launch finds its thread again by it. */
  proposalId?: string;
  /** Kept until the first message has gone out; a retried launch sends it then instead of reporting success. */
  firstMessage?: string;
  /** The Goal's name as last read from the core, for lists that cannot ask the core per row. */
  goalTitle?: string;
  /** The core no longer has this Goal. The binding stays, so the conversation keeps its record. */
  goalMissing?: boolean;
  /** How the thread came to be bound: here (`workbench`), through the core's external-session bindings, or by its own text. */
  basis?: 'core' | 'content' | 'workbench';
  source?: string;
}

/** Bindings live in their own file, so removing this adapter leaves the thread store untouched. */
export class LoopxBindings {
  private map = new Map<string, LoopxBinding>();

  constructor(private readonly file: string) {
    if (!existsSync(file)) return;
    try {
      const raw = JSON.parse(readFileSync(file, 'utf8')) as Record<string, LoopxBinding>;
      for (const [id, binding] of Object.entries(raw)) if (isBinding(binding)) this.map.set(id, binding);
    } catch {
      // An unreadable file is treated as no bindings rather than blocking every thread.
    }
  }

  get(threadId: string): LoopxBinding | null {
    return this.map.get(threadId) ?? null;
  }

  entries(): Array<[string, LoopxBinding]> {
    return [...this.map];
  }

  threadsOf(goalId: string): string[] {
    return [...this.map].filter(([, b]) => b.goalId === goalId).map(([id]) => id);
  }

  byProposal(proposalId: string): string | null {
    for (const [id, b] of this.map) if (b.proposalId === proposalId) return id;
    return null;
  }

  update(threadId: string, patch: Partial<LoopxBinding>): void {
    const b = this.map.get(threadId);
    if (!b) return;
    const next = { ...b, ...patch };
    for (const k of Object.keys(next) as (keyof LoopxBinding)[]) if (next[k] === undefined) delete next[k];
    this.set(threadId, next);
  }

  set(threadId: string, binding: LoopxBinding): void {
    this.map.set(threadId, binding);
    this.save();
  }

  delete(threadId: string): void {
    if (this.map.delete(threadId)) this.save();
  }

  private save(): void {
    mkdirSync(dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    writeFileSync(tmp, JSON.stringify(Object.fromEntries(this.map), null, 1));
    renameSync(tmp, this.file);
  }
}

export function isBinding(value: unknown): value is LoopxBinding {
  const b = value as Partial<LoopxBinding> | null;
  return !!b && typeof b.goalId === 'string' && typeof b.goalAgentId === 'string' && typeof b.workspace === 'string'
    && (LOOPX_PERMISSIONS as readonly string[]).includes(b.permission as string);
}

/**
 * The LoopX agent a loochat agent stands for, by adapter kind: the endpoints in the core's chat
 * catalog. The hub runs the conversation itself (open_session=false), so the core only needs the
 * endpoint registered, not live. Anything else cannot start a Goal conversation.
 */
const GOAL_AGENTS: Record<string, string> = { codex: 'codex', claude: 'claude-code', agy: 'antigravity', dsh: 'dsh' };
export function goalAgentFor(adapterId: string): string | null {
  return GOAL_AGENTS[adapterId] ?? null;
}

/** What permissionAvailable needs to know of an adapter. */
export interface GoalAdapter { id: string; capabilities: { permissionModes: readonly PermissionMode[] } }

/**
 * Which permissions an agent can hold a Goal under. Writing is open to every Goal agent, so the work
 * gets done: Codex keeps it inside the workspace with its sandbox, the others are only told to. Read
 * only needs an agent that asks before it acts (its asks are refused), so one that cannot ask is
 * offered writing alone rather than a read-only it would not keep.
 */
export function permissionAvailable(adapter: GoalAdapter, permission: LoopxPermission): { ok: boolean; reason?: string } {
  if (permission === 'workspace_auto') return { ok: true };
  if (adapter.id === 'codex' || adapter.capabilities.permissionModes.includes('ask')) return { ok: true };
  return { ok: false, reason: '该 agent 不会先问再做，只读拦不住它，只能选工作区写入' };
}

/** Whether this agent's writing is held to the workspace by a sandbox (Codex), or only by what it is told. */
export const workspaceSandboxed = (adapterId: string): boolean => adapterId === 'codex';

/** The permission a Goal conversation's thread is created with; Codex's own sandbox policy takes over in the session. */
export function goalThreadMode(adapter: GoalAdapter, permission: LoopxPermission): PermissionMode | undefined {
  const modes = adapter.capabilities.permissionModes;
  if (adapter.id === 'codex' || permission === 'read_only') return 'ask';
  // Its asks would be refused, so a writing Goal agent runs without asking.
  return modes.includes('full_access') ? 'full_access' : modes.includes('auto_edit') ? 'auto_edit' : undefined;
}
