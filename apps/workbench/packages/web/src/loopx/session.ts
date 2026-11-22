/** How a conversation's LoopX Goal reads in lists and the session panel. Pure, so it can be tested directly. */
import type { SessionLoopx } from '../api';

type Origin = 'app' | 'cli' | null | undefined;

/** The core's host surface words, for a binding the core recorded. */
const HOST_SURFACES: Record<string, 'app' | 'cli' | 'ide'> = {
  'codex-app': 'app', 'codex-app-ssh': 'app', 'codex-ide-plugin': 'ide', 'codex-cli-tui': 'cli',
};

/** Which side wakes this Goal's conversation on its heartbeat. `workbench`: nothing outside does. */
export function wakeOwner(info: SessionLoopx, origin: Origin): 'app' | 'cli' | 'ide' | 'workbench' | 'unknown' {
  if (info.basis === 'workbench') return 'workbench';
  if (info.basis === 'core') return HOST_SURFACES[info.source] ?? 'unknown';
  return origin === 'app' ? 'app' : origin === 'cli' ? 'cli' : 'unknown';
}

/**
 * Whether an outside host's own heartbeat still drives this conversation, so a message sent here
 * could land at the same time as the host's. Only the desktop app counts: a terminal client waits
 * for the person and does not send on its own.
 */
export function drivenByApp(info: SessionLoopx | null | undefined, origin: Origin): boolean {
  return Boolean(info && !info.missing && wakeOwner(info, origin) === 'app');
}
