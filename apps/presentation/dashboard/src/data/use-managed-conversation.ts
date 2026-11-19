import {useEffect, useState} from "react";
import {resolveManagedChatSession, type ChatSessionSummary, type ManagedConversationTarget} from "./chat";

/** Resolve the original Session without opening an adapter or starting work. */
export function useManagedConversation(target: ManagedConversationTarget | null, enabled: boolean) {
  const goalId = target?.goalId, agentId = target?.agentId, sessionId = target?.sessionId;
  const key = JSON.stringify([goalId, agentId, sessionId]);
  const [state, setState] = useState<{key: string; session?: ChatSessionSummary; error?: string}>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!enabled || goalId === undefined || agentId === undefined || sessionId === undefined) return;
    const controller = new AbortController();
    void resolveManagedChatSession({goalId, agentId, sessionId}, AbortSignal.any([controller.signal, AbortSignal.timeout(5000)]))
      .then(session => {if (!controller.signal.aborted) setState({key, session});})
      .catch(error => {if (!controller.signal.aborted) setState({key, error: error instanceof Error ? error.message : String(error)});});
    return () => controller.abort();
  }, [goalId, agentId, sessionId, key, enabled, revision]);
  return {...(enabled && state?.key === key ? state : {}), retry: () => setRevision(value => value + 1)};
}
