import { readFile, stat } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join } from 'node:path';

/** Ferry's menu shows observations for 30 minutes, not a live total queue length. */
const QUEUE_FRESH_MS = 30 * 60_000;

interface FerryQueueObservation {
  /** Recent request position, zero for an observed nonqueued request, null when stale. */
  position: number | null;
  /** Ferry's observation time, in epoch milliseconds. */
  at: number;
}

/** Standard/Max and their context suffix share the same upstream configuration. */
export function ferryModelKey(id: string): string {
  return id.replace(/^claude-/i, '').replace(/\[1m\]$/i, '').replace(/-max$/i, '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Read existing observations only. Missing Ferry, invalid caches and I/O errors are optional metadata. */
export async function readFerryQueueObservations(env: NodeJS.ProcessEnv): Promise<Map<string, FerryQueueObservation>> {
  const observations = new Map<string, FerryQueueObservation>();
  try {
    const home = env.HOME || env.USERPROFILE || homedir();
    const directory = env.FERRY_HOME || (process.platform === 'darwin'
      ? join(home, 'Library', 'Application Support', 'ferry')
      : process.platform === 'win32'
        ? join(env.LOCALAPPDATA || join(home, 'AppData', 'Local'), 'ferry')
        : join(env.XDG_DATA_HOME || join(home, '.local', 'share'), 'ferry'));
    const file = join(directory, 'queue-seen.json');
    const info = await stat(file);
    // Ignore directories/devices and oversized metadata instead of blocking discovery on them.
    if (!info.isFile() || info.size > 1024 * 1024) return observations;
    const cache: unknown = JSON.parse(await readFile(file, 'utf8'));
    if (!cache || typeof cache !== 'object' || Array.isArray(cache)) return observations;
    const now = Date.now();
    for (const [config, value] of Object.entries(cache)) {
      if (!value || typeof value !== 'object' || Array.isArray(value)) continue;
      const { at, position } = value as { at?: unknown; position?: unknown };
      if (typeof at !== 'number' || !Number.isFinite(at) || at <= 0 || at > now) continue;
      // Only an absent position is Ferry's explicit nonqueued observation; invalid values are unknown.
      if ('position' in value && (typeof position !== 'number' || !Number.isSafeInteger(position) || position <= 0)) continue;
      const key = ferryModelKey(config);
      if (!key) continue;
      observations.set(key, { at, position: now - at > QUEUE_FRESH_MS ? null : typeof position === 'number' ? position : 0 });
    }
  } catch {
    // No executable, control token, probes, writes or warnings: this cache is never required to run Calli.
  }
  return observations;
}
