import { fetchChatSession, fetchChatSessions, type ChatSessionSummary, type ChatSessionSnapshot } from "./chat";

function conversationReadRevision(session: ChatSessionSummary): string | null {
  return session.transcript_revision ? JSON.stringify([
    session.transcript_revision, session.updated_at, session.status, session.active_turn_id,
  ]) : null;
}

/** One read-only observation across known conversations; never discovers work or starts a Turn. */
export async function readConversationReturns({ sessionIds, pendingSessionIds, revisions, receive, signal }: {
  sessionIds: string[];
  pendingSessionIds: Set<string>;
  revisions: Map<string, string>;
  receive: (snapshot: ChatSessionSnapshot) => void;
  signal: AbortSignal;
}): Promise<string[]> {
  const index = await fetchChatSessions({ signal });
  const listed = new Map(index.sessions.map((session) => [session.session_id, session]));
  const changed = sessionIds.filter((id) => {
    const session = listed.get(id);
    const revision = session && conversationReadRevision(session);
    return pendingSessionIds.has(id) || !revision || revisions.get(id) !== revision;
  });
  const unavailable: string[] = [];
  // Large histories share one compact index read, with bounded snapshot concurrency.
  for (let offset = 0; offset < changed.length && !signal.aborted; offset += 4) {
    const batch = changed.slice(offset, offset + 4);
    const results = await Promise.allSettled(batch.map(async (id) => {
      const snapshot = await fetchChatSession(id, signal);
      if (signal.aborted) return;
      if (snapshot.session.session_id !== id) throw new Error("Conversation snapshot identity mismatch");
      receive(snapshot);
      // Commit the revision actually read, not a newer index observation.
      const revision = conversationReadRevision(snapshot.session);
      if (revision) revisions.set(id, revision);
    }));
    results.forEach((result, index) => { if (result.status === "rejected") unavailable.push(batch[index]); });
  }
  return unavailable;
}

