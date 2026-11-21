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

/** Explains the node's connection state and how to restore it. */
export function hostProblem(code: HostFailure | undefined): string {
  switch (code) {
    case 'not_joined': return t('hosts.error.notJoined');
    case 'relay_lost': return t('hosts.error.relayLost');
    default: return t('hosts.error.other');
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
export function useHosts(token: string): { hosts: HostInfo[]; loaded: boolean; reload: () => Promise<void> } {
  const [state, setState] = useState({ hosts: known, loaded: knownLoaded });
  const reload = useCallback(async () => {
    if (!token) return;
    try {
      const hosts = await new Api(token).hosts();
      const changed = !knownLoaded || JSON.stringify(hosts) !== JSON.stringify(known);
      known = hosts;
      knownLoaded = true;
      if (changed) setState({ hosts, loaded: true });
    } catch {
      // A hub that cannot say keeps what the page last heard.
    }
  }, [token]);

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
