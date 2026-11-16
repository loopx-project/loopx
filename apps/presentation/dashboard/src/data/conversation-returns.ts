import type { ChatVisibleMessage } from "./chat";
import type { WorkspaceImageAttachment } from "../features/personal-workspace/personal-workspace-model";

type ConversationMessage = {
  queueInputRevision?: number;
  queueInputCancelled?: true;
  sourceSessionId?: string;
  sourceMessageId?: string;
  sourceTurnId?: string;
  sourceCreatedAt?: string;
  role?: string;
  pending?: boolean;
  queued?: boolean;
  collaboration?: ChatVisibleMessage["collaboration"];
  collaborations?: ChatVisibleMessage["collaborations"];
  returnDelivery?: ChatVisibleMessage["return_delivery"];
  replyContext?: ChatVisibleMessage["reply_context"];
  attachments?: WorkspaceImageAttachment[];
  text?: string;
};

function refreshHistoryImageLinks(row: ConversationMessage, source: ChatVisibleMessage, sessionId: string) {
  if (row.pending || row.queued || row.queueInputCancelled || !row.sourceMessageId || row.sourceMessageId !== source.message_id
      || source.session_id !== sessionId || row.role !== (source.role === "user" ? "user" : "assistant")
      || !row.attachments?.length || source.attachments?.length !== row.attachments.length) return row.attachments;
  const oldAttachments = row.attachments;
  const freshAttachments = source.attachments;
  const imageBase = `/api/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(source.message_id)}/images/`;
  let updated: WorkspaceImageAttachment[] | undefined;
  for (const [index, old] of oldAttachments.entries()) {
    const given = freshAttachments[index];
    const prefix = `${imageBase}${index}/`;
    if (given.id !== old.id || given.mime_type !== old.mimeType || given.data_url === old.dataUrl
        || !old.dataUrl.startsWith(prefix) || !given.data_url.startsWith(prefix)
        || !/^[a-f0-9]{64}$/.test(given.data_url.slice(prefix.length))) continue;
    updated ??= oldAttachments.slice();
    updated[index] = {...old, dataUrl: given.data_url, name: given.name, size: given.size};
  }
  return updated ?? oldAttachments;
}

/** Delivery of one result does not close the conversation or its later updates. */
export function conversationReturnSessions(activeSessionId: string | undefined, messages: ConversationMessage[]): string[] {
  const sessions = new Set(activeSessionId ? [activeSessionId] : []);
  for (const message of messages) if (message.sourceSessionId) sessions.add(message.sourceSessionId);
  return [...sessions].sort();
}

/** Only the current brief's conclusion can settle its outstanding return. */
export function currentCollaborationConclusion(request: NonNullable<ChatVisibleMessage["collaboration"]>) {
  const replies = request.returns.filter(reply => reply.phase === "conclusion"
    && (request.request_revision === undefined || (reply.request_revision ?? 1) === request.request_revision));
  return replies.find(reply => reply.status === "delivered")
    ?? replies.find(reply => reply.status !== "superseded") ?? replies[0];
}

/** Metadata can change before a new transcript row is appended. */
export function conversationPendingReturnSessions(messages: ConversationMessage[]): string[] {
  const sessions = new Set<string>();
  for (const message of messages) {
    const requests = message.collaborations ?? (message.collaboration ? [message.collaboration] : []);
    const waitingForConclusion = requests.some(request => currentCollaborationConclusion(request)?.status !== "delivered");
    const delivery = message.returnDelivery;
    const waitingForDelivery = delivery && !["delivered", "superseded"].includes(delivery.status);
    const waitingForTranscript = message.sourceTurnId && !message.sourceMessageId;
    if (message.sourceSessionId && (message.pending || waitingForConclusion || waitingForDelivery || waitingForTranscript)) sessions.add(message.sourceSessionId);
  }
  return [...sessions].sort();
}

/** A snapshot may refresh only its own session; it never replaces streamed text. */
export function reconcileConversationReturns<T extends ConversationMessage>(
  previous: T[], sessionId: string, messages: ChatVisibleMessage[],
  createReply: (message: ChatVisibleMessage) => T, refreshLinkedImages = false,
): T[] {
  const byId = new Map(messages.map((row) => [row.message_id, row]));
  const byTurn = new Map<string, ChatVisibleMessage>();
  for (const row of messages) {
    if (row.origin === "manager_followup") continue;
    const key = `${row.turn_id}:${row.role === "user" ? "user" : "assistant"}`;
    // The Turn starts with its user request. Later user instructions have their
    // own message IDs; they must not hydrate that original optimistic request.
    if (row.role !== "user" || !byTurn.has(key)) byTurn.set(key, row);
  }
  const seen = new Set(previous.filter((row) => row.sourceSessionId === sessionId).map((row) => row.sourceMessageId));
  let changed = false;
  const updated = previous.map((row) => {
    if (row.sourceSessionId !== sessionId) return row;
    const source = row.sourceMessageId ? byId.get(row.sourceMessageId) : row.sourceTurnId
      ? byTurn.get(`${row.sourceTurnId}:${row.role === "user" ? "user" : "assistant"}`) : undefined;
    if (!source) return row;
    // Projection absence is not a retraction: the backend can temporarily be
    // unable to read collaboration metadata. Keep the last observed receipt and
    // its outstanding read obligation until a newer observation arrives.
    const returnDelivery = source.return_delivery ?? row.returnDelivery;
    const collaboration = source.collaboration ?? row.collaboration;
    const collaborations = source.collaborations ?? row.collaborations;
    const sourceCreatedAt = source.created_at ?? row.sourceCreatedAt;
    const replyContext = source.reply_context ?? row.replyContext;
    const attachments = refreshLinkedImages ? refreshHistoryImageLinks(row, source, sessionId) : row.attachments;
    // Only the exact canonical user body can switch to its source-backed view.
    // A background read never replaces a live answer or a different draft.
    const inputChanged = row.role === "user" && source.role === "user"
      && (!row.sourceTurnId || source.turn_id === row.sourceTurnId) && typeof source.queue_input_revision === "number"
      && source.queue_input_revision > (row.queueInputRevision ?? 0);
    const queueInputRevision = inputChanged ? source.queue_input_revision : row.queueInputRevision;
    const queueInputCancelled = inputChanged ? source.queue_input_cancelled : row.queueInputCancelled;
    const queueAnswered = row.queued === true && !row.pending && row.role === "assistant"
      && Boolean(row.sourceTurnId) && source.role !== "user" && source.origin !== "manager_followup" && source.turn_id === row.sourceTurnId;
    const queued = queueAnswered ? false : row.queued;
    const text = inputChanged || queueAnswered ? source.text : row.role === "user" && source.reply_context && row.text === source.text
      ? source.reply_context.source_text : row.text;
    if (row.sourceMessageId === source.message_id && JSON.stringify(row.returnDelivery) === JSON.stringify(returnDelivery)
      && JSON.stringify(row.collaboration) === JSON.stringify(collaboration)
      && JSON.stringify(row.collaborations) === JSON.stringify(collaborations) && row.sourceCreatedAt === sourceCreatedAt
      && JSON.stringify(row.replyContext) === JSON.stringify(replyContext) && row.text === text && row.attachments === attachments
      && row.queueInputRevision === queueInputRevision && row.queueInputCancelled === queueInputCancelled && row.queued === queued) return row;
    changed = true;
    return { ...row, sourceMessageId: source.message_id, sourceCreatedAt, returnDelivery, collaboration, collaborations, replyContext, text,
      ...(attachments === row.attachments ? {} : {attachments}), queueInputRevision, queueInputCancelled,
      ...(queueAnswered ? {queued: false} : {}) };
  });
  for (const message of messages) {
    if (message.origin !== "manager_followup" || seen.has(message.message_id)) continue;
    seen.add(message.message_id);
    changed = true;
    updated.push(createReply(message));
  }
  return changed ? updated : previous;
}

/** Recover missing history without retracting messages or overwriting live text. */
export function reconcileConversationHistory<T extends ConversationMessage>(
  previous: T[], messages: ChatVisibleMessage[], createMessage: (message: ChatVisibleMessage) => T,
  verifiedBoundSessionId?: string,
): T[] {
  let updated = previous;
  const sessions = new Set(messages.map((message) => message.session_id).filter((id): id is string => Boolean(id)));
  for (const sessionId of sessions) {
    updated = reconcileConversationReturns(updated, sessionId, messages.filter((message) => message.session_id === sessionId),
      createMessage, sessionId === verifiedBoundSessionId);
  }
  // A history read can arrive before admission hydrates the optimistic row.
  // Deduplicate only established Session/message identities, never body text.
  // A live row owns the stream callbacks and controls even when the history
  // row appeared first. Keep that row when both acquire the same identity.
  const pendingIdentities = new Set(updated.filter(message => message.pending && message.sourceSessionId && message.sourceMessageId)
    .map(message => JSON.stringify([message.sourceSessionId, message.sourceMessageId])));
  const identities = new Set<string>();
  const unique = updated.filter((message) => {
    if (!message.sourceSessionId || !message.sourceMessageId) return true;
    const key = JSON.stringify([message.sourceSessionId, message.sourceMessageId]);
    if (pendingIdentities.has(key) && !message.pending) return false;
    if (identities.has(key)) return false;
    identities.add(key);
    return true;
  });
  if (unique.length !== updated.length) updated = unique;
  const seen = new Set(updated.map((message) => `${message.sourceSessionId}:${message.sourceMessageId}`));
  const recovered = messages.filter((message) => !seen.has(`${message.session_id}:${message.message_id}`)).map(createMessage);
  if (!recovered.length) return updated;
  return [...updated, ...recovered].sort((left, right) => !left.sourceCreatedAt ? (right.sourceCreatedAt ? 1 : 0)
    : !right.sourceCreatedAt ? -1 : left.sourceCreatedAt.localeCompare(right.sourceCreatedAt));
}
