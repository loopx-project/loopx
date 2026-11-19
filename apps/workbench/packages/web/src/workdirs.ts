import type { WorkdirsConfig } from './api';

/** The machine's answer about its folders, as the roots dialog lists them: each with its own remove flag. */
export function rootRows(config: WorkdirsConfig): Array<{ path: string; removable: boolean }> {
  return config.roots ?? config.workdirs.map((path) => ({ path, removable: false }));
}

/**
 * One machine's folder list changed from the page; every agent of that machine carries the new one,
 * so the next directory picker for it starts from the fresh list instead of a copy grown stale.
 */
export function withWorkdirs<T extends { host: string; directories?: string[] }>(agents: T[], host: string, config: WorkdirsConfig): T[] {
  if (!agents.some((a) => a.host === host && a.directories !== config.workdirs)) return agents;
  return agents.map((a) => (a.host === host ? { ...a, directories: config.workdirs } : a));
}
