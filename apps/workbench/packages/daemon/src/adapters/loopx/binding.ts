import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';

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
 * The LoopX agent a loochat agent stands for, by adapter kind. Only agents the core can run a Goal on
 * are listed; anything else cannot start a Goal conversation.
 */
export function goalAgentFor(adapterId: string): string | null {
  if (adapterId === 'codex') return 'codex';
  if (adapterId === 'claude') return 'claude-code';
  return null;
}

/** The core's own rule: only an agent that can keep to a directory boundary may write inside the workspace. */
export function permissionAvailable(adapterId: string, permission: LoopxPermission): { ok: boolean; reason?: string } {
  if (permission === 'read_only') return { ok: true };
  if (adapterId === 'codex') return { ok: true };
  return { ok: false, reason: '该 agent 守不住目录边界，只有 codex 能把写入限制在所选目录内' };
}
