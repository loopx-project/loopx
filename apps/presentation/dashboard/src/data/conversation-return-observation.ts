import { fetchChatSession, fetchChatSessions, type ChatSessionSummary, type ChatSessionSnapshot } from "./chat";

function conversationReadRevision(session: ChatSessionSummary): string | null {
  return session.transcript_revision ? JSON.stringify([
    session.transcript_revision, session.updated_at, session.status, session.active_turn_id,
  ]) : null;
}

/** One read-only observation across known conversations; never discovers work or starts a Turn. */
export async function readConversationReturns({ sessionIds, pendingSessionIds, revisions, receive, signal, sessions }: {
  sessionIds: string[];
  pendingSessionIds: Set<string>;
  revisions: Map<string, string>;
  receive: (snapshot: ChatSessionSnapshot) => void;
  signal: AbortSignal;
  sessions?: ChatSessionSummary[];
}): Promise<string[]> {
  const listedSessions = sessions ?? (await fetchChatSessions({ signal })).sessions;
  const listed = new Map(listedSessions.map((session) => [session.session_id, session]));
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

export async function readTaskSessions(goalId: string, previous: Record<string, ChatSessionSnapshot>, signal: AbortSignal) {
  const listed = await fetchChatSessions({ goalId, signal });
  const sessions = listed.sessions.filter((session) => session.channel_id?.startsWith("task."));
  const snapshots: Record<string, ChatSessionSnapshot> = {};
  const revisions = new Map<string, string>();
  for (const session of sessions) {
    const snapshot = previous[session.session_id];
    if (!snapshot) continue;
    snapshots[session.session_id] = snapshot;
    const revision = conversationReadRevision(snapshot.session);
    if (revision) revisions.set(session.session_id, revision);
  }
  const unavailableSessionIds = await readConversationReturns({
    sessions,
    sessionIds: sessions.map((session) => session.session_id),
    pendingSessionIds: new Set(sessions.filter((session) => session.active_turn_id).map((session) => session.session_id)),
    revisions,
    receive(snapshot) { snapshots[snapshot.session.session_id] = snapshot; },
    signal,
  });
  return { sessions, snapshots, unavailableSessionIds };
}
