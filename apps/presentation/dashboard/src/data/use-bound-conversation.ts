import { useEffect, useState } from "react";
import { resolveBoundConversation,
  type BoundConversationTarget, type ChatSessionSummary, type BoundConversationSource } from "./chat";

/** Reads the source's title and executor. Admission remains with the Core owner. */
export function useBoundConversation(target: BoundConversationTarget | null, enabled: boolean) {
  const bindingId = target?.bindingId, sessionId = target?.sessionId;
  const key = JSON.stringify([bindingId, sessionId]);
  const [state, setState] = useState<{ key: string; session?: ChatSessionSummary; source?: BoundConversationSource; error?: string }>();
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!enabled || bindingId === undefined || sessionId === undefined) return;
    let active = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    resolveBoundConversation({ bindingId, sessionId }, controller.signal)
      .then(({session, source}) => {
        if (!active) return;
        setState({ key, session, source });
      }).catch(error => { if (active) setState({ key, error: error instanceof Error ? error.message : String(error) }); })
      .finally(() => clearTimeout(timeout));
    return () => { active = false; controller.abort(); clearTimeout(timeout); };
  }, [bindingId, sessionId, key, enabled, revision]);
  return { ...(enabled && state?.key === key ? state : {}), retry: () => setRevision(value => value + 1) };
}
