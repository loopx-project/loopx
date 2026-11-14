import { useCallback, useEffect, useState } from 'react';
import { Api, type HostFailure, type HostInfo } from './api';
import { t } from './i18n';

const HOST_KEY = 'calli.host';

/** The machine this device was last looking at; null is the hub's own. */
export function loadHost(): string | null {
  try {
    return localStorage.getItem(HOST_KEY) || null;
  } catch {
    return null;
  }
}

export function saveHost(id: string | null): void {
  try {
    if (id) localStorage.setItem(HOST_KEY, id);
    else localStorage.removeItem(HOST_KEY);
  } catch {
    // Not remembered, still switched.
  }
}

export const hostStatus = (status: HostInfo['status']): string => t(`hosts.status.${status}`);

/**
 * Why a machine cannot be reached, said so that it is clear what to do about it. The hub signs in
 * as itself, with nobody to answer a prompt, so most of these are fixed on the hub's machine.
 */
export function hostProblem(code: HostFailure | undefined, target: string): string {
  switch (code) {
    case 'no_ssh':
      return t('hosts.error.noSsh');
    case 'host_key':
      return t('hosts.error.hostKey', target);
    case 'auth':
      return t('hosts.error.auth', target);
    case 'unreachable':
      return t('hosts.error.unreachable');
    case 'daemon_missing':
      return t('hosts.error.noHub');
    case 'forward_denied':
      return t('hosts.error.noForwarding');
    default:
      return t('hosts.error.other');
  }
}

// Switching machines remounts the app; what was known about the machines carries over, so the
// switcher does not blink out and in.
let known: HostInfo[] = [];
let knownLoaded = false;

/** How often the machines are asked about while the page is in front. */
const HOSTS_POLL_MS = 10_000;

/**
 * The hub's other machines, kept fresh. `loaded` is false until the hub has answered once: until
 * then nothing can be said about a machine, not even that it is gone.
 */
export function useHosts(enabled: boolean): { hosts: HostInfo[]; loaded: boolean; reload: () => Promise<void> } {
  const [state, setState] = useState({ hosts: known, loaded: knownLoaded });
  const reload = useCallback(async () => {
    if (!enabled) return;
    try {
      const hosts = await new Api('').hosts();
      const changed = !knownLoaded || JSON.stringify(hosts) !== JSON.stringify(known);
      known = hosts;
      knownLoaded = true;
      if (changed) setState({ hosts, loaded: true });
    } catch {
      // A hub that cannot say keeps what the page last heard.
    }
  }, [enabled]);

  useEffect(() => {
    void reload();
    const timer = setInterval(() => {
      if (!document.hidden) void reload();
    }, HOSTS_POLL_MS);
    const onVisible = () => {
      if (!document.hidden) void reload();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [reload]);

  return { ...state, reload };
}
