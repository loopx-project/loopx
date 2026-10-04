import { useCallback, useEffect, useRef, useState } from "react";
import { fetchChatHistory, type ChatHistory } from "./chat";

type HistoryState = {
  scope: string;
  history: ChatHistory | null;
  reading: boolean;
  failed: boolean;
};

export type ConversationHistoryStatus = Pick<ReturnType<typeof useConversationHistory>, "phase" | "reading" | "sendBlocked" | "retry">;

/** The newest Session a channel read found for one executor. */
export function currentChannelSession(history: ChatHistory | null, agentId: string) {
  return history?.sessions.find((session) => session.agent_id === agentId) ?? null;
}

/** Read recovery shared by steward and Goal conversations; never executes a Turn. */
export function useConversationHistory({ agentId, currentAgentId, channelId, goalId, enabled }: {
  agentId?: string;
  currentAgentId: string;
  channelId: string;
  goalId?: string;
  enabled: boolean;
}) {
  const scope = JSON.stringify([agentId ?? null, currentAgentId, channelId, goalId ?? null]);
  const [state, setState] = useState<HistoryState | null>(null);
  const retryRead = useRef<(() => void) | null>(null);
  const retry = useCallback(() => retryRead.current?.(), []);
  const cached = useRef<{ scope: string; history: ChatHistory } | null>(null);
  const cachedHistory = (forScope: string) => cached.current?.scope === forScope ? cached.current.history : null;
  const params = useRef({ agentId, channelId, goalId, scope });
  params.current = { agentId, channelId, goalId, scope };
  // A caller that learned of a Turn the cached read cannot show -- the service
  // refused a send because that Turn is running -- needs what the Session looks
  // like now, not what this hook read earlier. The re-read stays here so the
  // conversation keeps a single history owner.
  const refresh = useCallback(async () => {
    const { agentId, channelId, goalId, scope } = params.current;
    const loaded = await fetchChatHistory({ agentId, channelId, goalId });
    // The caller may still need its completed read, but a conversation that
    // was left while it waited no longer owns this hook's visible history.
    if (params.current.scope !== scope) return loaded;
    cached.current = { scope, history: loaded };
    setState({ scope, history: loaded, reading: false, failed: false });
    return loaded;
  }, []);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    let reading = false;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let attempts = 0;
    async function read() {
      if (cancelled || reading) return;
      clearTimeout(retryTimer);
      reading = true;
      const previous = cachedHistory(scope);
      setState({ scope, history: previous, reading: true, failed: false });
      try {
        const loaded = await fetchChatHistory({ agentId, channelId, goalId }, previous ?? undefined);
        if (cancelled || params.current.scope !== scope) return;
        cached.current = { scope, history: loaded };
        setState({ scope, history: loaded, reading: false, failed: false });
      } catch {
        if (cancelled || params.current.scope !== scope) return;
        setState({ scope, history: cachedHistory(scope), reading: false, failed: true });
      } finally {
        reading = false;
        const known = cachedHistory(scope);
        if (!cancelled && (!known || known.unavailableSessionIds.length > 0)) {
          // Back off sustained transport failures; a user can retry immediately.
          retryTimer = setTimeout(() => void read(), Math.min(3000 * 2 ** attempts++, 30_000));
        }
      }
    }
    retryRead.current = () => void read();
    void read();
    return () => {
      cancelled = true;
      clearTimeout(retryTimer);
      retryRead.current = null;
    };
  }, [agentId, channelId, goalId, enabled, scope]);
  const current = enabled && state?.scope === scope ? state : null;
  const history = current?.history ?? null;
  // A channel transcript spans executors; readability of another executor's
  // latest session cannot authorize sending into the selected one.
  const latest = currentChannelSession(history, currentAgentId);
  const currentSessionReadable = history !== null && (!latest || history.snapshots.some((snapshot) => snapshot.session.session_id === latest.session_id));
  return {
    history,
    currentSession: latest,
    refresh,
    phase: !enabled ? "ready" as const : !current ? "loading" as const
      : current.failed ? "unavailable" as const
      : history?.unavailableSessionIds.length ? (history.snapshots.length ? "partial" as const : "unavailable" as const)
      : current.reading ? "loading" as const : "ready" as const,
    reading: current?.reading ?? enabled,
    sendBlocked: enabled && !currentSessionReadable,
    // Missing older history must not restart a recovered current stream.
    connectionKey: currentSessionReadable ? `${scope}:${latest?.session_id ?? "empty"}` : null,
    retry,
  };
}
