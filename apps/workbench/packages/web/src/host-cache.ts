import type { AgentInfo, ModelOption, PetStatus } from './api';
import type { PetBust } from './pet';

/**
 * What a machine last told this device: its agents, the choices a new thread starts from, and the
 * assistant's portrait. A cold start, and a switch to another machine, render their first frame
 * from here; the live answers that arrive later replace it without a jump.
 */

const KEY = 'calli.host.v1';
const PET_KEY = 'calli.pet.v1';
const MAX_HOSTS = 8;

/** The choices a new thread opens with, as the last one on this machine was left. */
export interface DraftChoice { agent: string; cwd: string; scratch: boolean }

/** A model list without its live parts: load and queue readings go stale in storage. */
type StillModel = Omit<ModelOption, 'loadPercent' | 'loadError' | 'queueSize' | 'queuePosition' | 'queueObservedAt'>;

/** The assistant's shape as last seen: what frames to ask for, never how an install is going. */
export type PetShape = Pick<PetStatus, 'id' | 'commit' | 'installed' | 'clips' | 'frameMs'>;

interface HostState { at: number; agents: AgentInfo[]; draft: DraftChoice | null; models: Record<string, StillModel[]> }
interface PetKept { at: number; status: PetShape; bust: PetBust | null }

/** This page's copies, whole: switching machines back and forth needs no storage at all. */
const memory = new Map<string, HostState>();
const petMemory = new Map<string, PetKept>();

const hostKey = (hostId: string | null) => hostId ?? '';

function readStore(key: string): Record<string, unknown> {
  try {
    const data = JSON.parse(localStorage.getItem(key) ?? '{}');
    if (!data || typeof data !== 'object' || Array.isArray(data)) return {};
    return data as Record<string, unknown>;
  } catch {
    return {};
  }
}

function writeStore(key: string, entries: Record<string, unknown>): void {
  try {
    const recent = Object.entries(entries).filter((e): e is [string, { at: number }] => typeof (e[1] as { at?: unknown } | null)?.at === 'number').sort((a, b) => b[1].at - a[1].at).slice(0, MAX_HOSTS);
    localStorage.setItem(key, JSON.stringify(Object.fromEntries(recent)));
  } catch {
    // Storage full or disabled: this page still keeps its copy in memory.
  }
}

function readAgents(value: unknown): AgentInfo[] | null {
  if (!Array.isArray(value)) return null;
  const list = value.filter((a): a is AgentInfo => Boolean(a && typeof a === 'object' && typeof (a as AgentInfo).id === 'string' && typeof (a as AgentInfo).label === 'string' && typeof (a as AgentInfo).available === 'boolean'));
  return list.length ? list : null;
}

function readModels(value: unknown): Record<string, StillModel[]> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const models: Record<string, StillModel[]> = {};
  for (const [agent, list] of Object.entries(value as Record<string, unknown>)) {
    if (!Array.isArray(list)) continue;
    const kept = list.filter((m): m is StillModel => Boolean(m && typeof m === 'object' && typeof (m as ModelOption).id === 'string' && typeof (m as ModelOption).label === 'string'));
    if (kept.length) models[agent] = kept;
  }
  return Object.keys(models).length ? models : null;
}

function readDraft(value: unknown): DraftChoice | null {
  if (!value || typeof value !== 'object') return null;
  const draft = value as Partial<DraftChoice>;
  return typeof draft.agent === 'string' ? { agent: draft.agent, cwd: typeof draft.cwd === 'string' ? draft.cwd : '', scratch: draft.scratch === true } : null;
}

function hostState(hostId: string | null): HostState | null {
  const key = hostKey(hostId);
  const held = memory.get(key);
  if (held) return held;
  const stored = readStore(KEY)[key];
  if (!stored || typeof stored !== 'object') return null;
  const state = stored as Partial<HostState>;
  if (typeof state.at !== 'number') return null;
  return { at: state.at, agents: readAgents(state.agents) ?? [], draft: readDraft(state.draft), models: readModels(state.models) ?? {} };
}

/** Reads this machine's state, updating whatever part of it the caller already knows. */
function putHostState(hostId: string | null, patch: Partial<Omit<HostState, 'at'>>, changed: boolean): void {
  const key = hostKey(hostId);
  const before = hostState(hostId);
  const next: HostState = { at: Date.now(), agents: patch.agents ?? before?.agents ?? [], draft: patch.draft !== undefined ? patch.draft : before?.draft ?? null, models: patch.models ?? before?.models ?? {} };
  memory.set(key, next);
  // A part that reads back the same does not count as a change: live load readings must not write.
  if (!changed && before && JSON.stringify({ agents: before.agents, draft: before.draft, models: before.models }) === JSON.stringify({ agents: next.agents, draft: next.draft, models: next.models })) return;
  writeStore(KEY, { ...readStore(KEY), [key]: next });
}

/** The agents this machine last listed, so the picker names one before the machine answers. */
export function cachedAgents(hostId: string | null): AgentInfo[] | null {
  return readAgents(hostState(hostId)?.agents);
}

export function rememberAgents(hostId: string | null, agents: AgentInfo[]): void {
  if (!agents.length) return;
  const before = hostState(hostId)?.agents ?? [];
  const changed = JSON.stringify(before) !== JSON.stringify(agents);
  putHostState(hostId, { agents }, changed);
}

/** Where a new thread on this machine last started: which instance, in which directory. */
export function cachedDraft(hostId: string | null): DraftChoice | null {
  return readDraft(hostState(hostId)?.draft);
}

export function rememberDraft(hostId: string | null, draft: DraftChoice): void {
  if (!draft.agent) return;
  const before = hostState(hostId)?.draft ?? null;
  const changed = JSON.stringify(before) !== JSON.stringify(draft);
  putHostState(hostId, { draft }, changed);
}

/** Each agent's model list as it last stood, without the load readings that were live then. */
export function cachedModels(hostId: string | null): Record<string, ModelOption[]> | null {
  return readModels(hostState(hostId)?.models);
}

export function rememberModels(hostId: string | null, models: Record<string, ModelOption[]>): void {
  const strip = (list: ModelOption[]): StillModel[] => list.map(({ loadPercent: _l, loadError: _e, queueSize: _qs, queuePosition: _qp, queueObservedAt: _qo, ...still }) => still);
  const still = Object.fromEntries(Object.entries(models).map(([agent, list]) => [agent, strip(list)]).filter(([, list]) => (list as StillModel[]).length));
  const before = hostState(hostId)?.models ?? {};
  const changed = JSON.stringify(before) !== JSON.stringify(still);
  putHostState(hostId, { models: still }, changed);
}

function readPetStatus(value: unknown): PetShape | null {
  if (!value || typeof value !== 'object') return null;
  const status = value as Partial<PetShape>;
  if (typeof status.id !== 'string' || typeof status.commit !== 'string' || status.installed !== true) return null;
  const clips = status.clips;
  if (!clips || typeof clips !== 'object' || Array.isArray(clips)) return null;
  return typeof status.frameMs === 'number' ? { id: status.id, commit: status.commit, installed: true, clips, frameMs: status.frameMs } : null;
}

function readBust(value: unknown): PetBust | null {
  if (!value || typeof value !== 'object') return null;
  const bust = value as Partial<PetBust>;
  return typeof bust.src === 'string' && typeof bust.aspect === 'number' ? { src: bust.src, aspect: bust.aspect } : null;
}

function petKept(hostId: string | null): PetKept | null {
  const key = hostKey(hostId);
  const held = petMemory.get(key);
  if (held) return held;
  const stored = readStore(PET_KEY)[key];
  if (!stored || typeof stored !== 'object') return null;
  const kept = stored as Partial<PetKept>;
  const status = readPetStatus(kept.status);
  if (!status || typeof kept.at !== 'number') return null;
  return { at: kept.at, status, bust: readBust(kept.bust) };
}

/** The assistant as this machine last showed her, portrait included; null when she was not there. */
export function cachedPet(hostId: string | null): { status: PetShape; bust: PetBust | null } | null {
  const kept = petKept(hostId);
  return kept ? { status: kept.status, bust: kept.bust } : null;
}

function putPetKept(hostId: string | null, patch: Partial<PetKept>, drop = false): void {
  const key = hostKey(hostId);
  const before = petKept(hostId);
  const next: PetKept = { at: Date.now(), status: patch.status ?? before?.status ?? { id: '', commit: '', installed: false, clips: {}, frameMs: 0 }, bust: patch.bust !== undefined ? patch.bust : before?.bust ?? null };
  if (drop || !next.status.installed) {
    petMemory.delete(key);
    const rest = readStore(PET_KEY);
    delete rest[key];
    try { localStorage.setItem(PET_KEY, JSON.stringify(rest)); } catch { /* Storage may be full or disabled. */ }
    return;
  }
  petMemory.set(key, next);
  if (JSON.stringify(before?.status) === JSON.stringify(next.status) && before?.bust?.src === next.bust?.src) return;
  writeStore(PET_KEY, { ...readStore(PET_KEY), [key]: next });
}

/** Her shape as the machine now tells it; an uninstall drops what was kept, rather than freezing it. */
export function rememberPetStatus(hostId: string | null, status: PetStatus): void {
  if (!status.installed) { putPetKept(hostId, {}, true); return; }
  const shape: PetShape = { id: status.id, commit: status.commit, installed: true, clips: status.clips, frameMs: status.frameMs };
  putPetKept(hostId, { status: shape });
}

/** The portrait cut from her first frame, so the sidebar knows her face before the frame arrives. */
export function rememberPetBust(hostId: string | null, bust: PetBust): void {
  putPetKept(hostId, { bust });
}

/** Another hub, or signed out: what was read from the last one is not this one's. */
export function clearHostCache(): void {
  memory.clear();
  petMemory.clear();
  try { localStorage.removeItem(KEY); localStorage.removeItem(PET_KEY); } catch { /* Storage may be disabled. */ }
}
