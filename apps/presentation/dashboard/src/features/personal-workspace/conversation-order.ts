import type { WorkspaceTimelineItem } from "./personal-workspace-model";

/** Restore proposals at creation, never at refresh/apply time or array arrival. */
export function conversationOrder(items: WorkspaceTimelineItem[]): WorkspaceTimelineItem[] {
  const dated = (value?: string) => {
    const time = value ? Date.parse(value) : NaN;
    return Number.isFinite(time) ? time : -Infinity;
  };
  const time = (item: WorkspaceTimelineItem) => {
    if (item.kind === "proposal") return dated(item.proposal.createdAt);
    if (item.kind !== "message") return -Infinity;
    const stored = dated(item.message.createdAt);
    // Until the immutable message arrives, a terminal reply belongs at its
    // observed completion, not at the time we prepared or queued the request.
    return !item.message.sourceMessageId && !item.message.pending && item.message.endedAt !== undefined
      ? Math.max(stored, item.message.endedAt) : stored;
  };
  const turnKey = (item: WorkspaceTimelineItem) => item.kind === "message"
    && item.message.sourceSessionId && item.message.sourceTurnId
    ? JSON.stringify([item.message.sourceSessionId, item.message.sourceTurnId]) : undefined;
  const requestTimes = new Map<string, number>();
  const latestRequestTimes = new Map<string, number>();
  for (const item of items) {
    const key = turnKey(item);
    if (key && item.kind === "message" && item.message.role === "user" && Number.isFinite(time(item))) {
      requestTimes.set(key, Math.min(requestTimes.get(key) ?? Infinity, time(item)));
      latestRequestTimes.set(key, Math.max(latestRequestTimes.get(key) ?? -Infinity, time(item)));
    }
  }
  // Optimistic timestamps never outrank same-Turn request order. While working,
  // follow the initial request; before terminal readback, follow its last input.
  // Stored commentary retains its actual timestamp, including before steering.
  const pendingRequest = (item: WorkspaceTimelineItem) => item.kind === "message"
    && item.message.role !== "user" && !item.message.sourceMessageId
    ? (item.message.pending ? requestTimes : latestRequestTimes).get(turnKey(item) ?? "") : undefined;
  const orderedTime = (item: WorkspaceTimelineItem) => {
    const requestTime = pendingRequest(item);
    return requestTime === undefined ? time(item) : Math.max(time(item), requestTime);
  };
  // Non-conversation facts retain their existing grouping. Unknown dates stay
  // before dated conversation rows, rather than pretending to be new activity.
  const background = items.filter(item => item.kind !== "proposal" && item.kind !== "message");
  const conversation = items.filter(item => item.kind === "proposal" || item.kind === "message");
  return [...background, ...conversation.sort((a, b) => orderedTime(a) - orderedTime(b)
    || Number(pendingRequest(a) !== undefined) - Number(pendingRequest(b) !== undefined))];
}
