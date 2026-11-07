import { useEffect, useState } from "react";
import { fetchChatQueue, type ChatQueuedTurn } from "./chat";

/** Observes one original Session; its persisted queue remains the only owner. */
export function useConversationQueue({sessionId, scope, active, refreshKey}: {
  sessionId?: string; scope: string; active: boolean; refreshKey: string;
}) {
  const key = JSON.stringify([scope, sessionId]);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<{key: string; turns: ChatQueuedTurn[]; activeTurnId: string | null; error: Error | null} | null>(null);
  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0, pending = false;
    const read = async () => {
      try {
        const snapshot = await fetchChatQueue(sessionId, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]));
        if (controller.signal.aborted) return;
        failures = 0; pending = snapshot.turns.length > 0 || Boolean(snapshot.active_turn_id);
        setState({key, turns: snapshot.turns, activeTurnId: snapshot.active_turn_id, error: null});
      } catch (cause) {
        if (controller.signal.aborted) return;
        failures += 1;
        // A failed read is unknown, including revoked scope. Never retain
        // potentially inaccessible input or present an empty successful queue.
        setState({key, turns: [], activeTurnId: null, error: cause instanceof Error ? cause : new Error(String(cause))});
      }
      // After a successful read, the original queue/Session decides whether
      // observation continues; a cached model hint must not keep idle polling.
      if (!controller.signal.aborted && (pending || (failures > 0 && active))) {
        timer = setTimeout(() => void read(), failures ? Math.min(5000 * 2 ** failures, 30_000) : 5000);
      }
    };
    void read();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [key, sessionId, active, refreshKey, attempt]);
  const current = sessionId && state?.key === key ? state : null;
  return {turns: current?.turns ?? [], activeTurnId: current?.activeTurnId ?? null, error: current?.error ?? null, readReady: Boolean(current && !current.error),
    retry: () => setAttempt(value => value + 1)};
}
