import type { ChatVisibleMessage } from "./chat";

type ConversationMessage = {
  sourceSessionId?: string;
  sourceMessageId?: string;
  sourceTurnId?: string;
  collaboration?: ChatVisibleMessage["collaboration"];
  returnDelivery?: ChatVisibleMessage["return_delivery"];
};

/** Old sessions remain relevant while they owe a result, not forever. */
export function conversationReturnSessions(activeSessionId: string | undefined, messages: ConversationMessage[]): string[] {
  const sessions = new Set(activeSessionId ? [activeSessionId] : []);
  for (const message of messages) {
    const waitingForConclusion = message.collaboration && !message.collaboration.returns.some(
      (reply) => reply.phase === "conclusion" && reply.status === "delivered",
    );
    const delivery = message.returnDelivery;
    const waitingForDelivery = delivery && !["delivered", "superseded"].includes(delivery.status);
    const waitingForTranscript = message.sourceTurnId && !message.sourceMessageId;
    if (message.sourceSessionId && (waitingForConclusion || waitingForDelivery || waitingForTranscript)) sessions.add(message.sourceSessionId);
  }
  return [...sessions].sort();
}

/** A snapshot may refresh only its own session; it never replaces streamed text. */
export function reconcileConversationReturns<T extends ConversationMessage>(
  previous: T[], sessionId: string, messages: ChatVisibleMessage[],
  createReply: (message: ChatVisibleMessage) => T,
): T[] {
  const byId = new Map(messages.map((row) => [row.message_id, row]));
  const byTurn = new Map(messages.filter((row) => row.role !== "user" && row.origin !== "manager_followup")
    .map((row) => [row.turn_id, row]));
  const seen = new Set(previous.filter((row) => row.sourceSessionId === sessionId).map((row) => row.sourceMessageId));
  let changed = false;
  const updated = previous.map((row) => {
    if (row.sourceSessionId !== sessionId) return row;
    const source = row.sourceMessageId ? byId.get(row.sourceMessageId) : row.sourceTurnId ? byTurn.get(row.sourceTurnId) : undefined;
    if (!source) return row;
    // Projection absence is not a retraction: the backend can temporarily be
    // unable to read collaboration metadata. Keep the last observed receipt and
    // its outstanding read obligation until a newer observation arrives.
    const returnDelivery = source.return_delivery ?? row.returnDelivery;
    const collaboration = source.collaboration ?? row.collaboration;
    if (row.sourceMessageId === source.message_id && JSON.stringify(row.returnDelivery) === JSON.stringify(returnDelivery)
      && JSON.stringify(row.collaboration) === JSON.stringify(collaboration)) return row;
    changed = true;
    return { ...row, sourceMessageId: source.message_id, returnDelivery, collaboration };
  });
  for (const message of messages) {
    if (message.origin !== "manager_followup" || seen.has(message.message_id)) continue;
    seen.add(message.message_id);
    changed = true;
    updated.push(createReply(message));
  }
  return changed ? updated : previous;
}
