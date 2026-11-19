import type { ConnectionState } from '@calli/extension-api/web';
import type { Api, Thread } from '../api';

/** A live stream is useful only with a successful, current snapshot. Retries never replay events. */
export function createThreadFeed(
  api: Pick<Api, 'threads' | 'demo'>,
  receive: (threads: Thread[], observedAt: string | null) => void,
  connection: (state: ConnectionState) => void,
) {
  let connected = false;
  let fresh = false;
  let closed = false;
  let generation = 0;
  let retry: ReturnType<typeof setTimeout> | undefined;
  const publish = () => connection(api.demo ? 'offline' : connected && fresh ? 'live' : 'reconnecting');
  const clearRetry = () => { clearTimeout(retry); retry = undefined; };
  const read = async () => {
    const request = ++generation;
    try {
      const threads = await api.threads();
      if (!closed && request === generation) {
        clearRetry();
        fresh = true;
        receive(threads, api.demo ? null : new Date().toISOString());
        publish();
      }
      return threads;
    } catch (error) {
      if (!closed && request === generation) {
        fresh = false;
        publish();
        clearRetry();
        retry = setTimeout(() => { void read().catch(() => undefined); }, 2000);
      }
      throw error;
    }
  };
  return {
    read,
    start() { closed = false; publish(); },
    stream(online: boolean) {
      if (online !== connected) generation++;
      connected = online;
      fresh = false;
      publish();
      if (online) void read().catch(() => undefined);
    },
    dispose() { closed = true; generation++; clearRetry(); },
  };
}
