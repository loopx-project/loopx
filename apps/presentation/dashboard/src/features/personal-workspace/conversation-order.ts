import type { WorkspaceTimelineItem } from "./personal-workspace-model";

/** Restore proposals at creation, never at refresh/apply time or array arrival. */
export function conversationOrder(items: WorkspaceTimelineItem[]): WorkspaceTimelineItem[] {
  const dated = (value?: string) => {
    const time = value ? Date.parse(value) : NaN;
    return Number.isFinite(time) ? time : -Infinity;
  };
  const time = (item: WorkspaceTimelineItem) => item.kind === "proposal"
    ? dated(item.proposal.createdAt)
    : item.kind === "message" ? dated(item.message.createdAt) : -Infinity;
  const turnKey = (item: WorkspaceTimelineItem) => item.kind === "message"
    && item.message.sourceSessionId && item.message.sourceTurnId
    ? JSON.stringify([item.message.sourceSessionId, item.message.sourceTurnId]) : undefined;
  const requestTimes = new Map<string, number>();
  for (const item of items) {
    const key = turnKey(item);
    if (key && item.kind === "message" && item.message.role === "user" && Number.isFinite(time(item))) {
      requestTimes.set(key, Math.min(requestTimes.get(key) ?? Infinity, time(item)));
    }
  }
  // Admission precedes the durable user message. The optimistic work row has
  // a client timestamp, so its clock alone cannot put it ahead of its request.
  const pendingRequest = (item: WorkspaceTimelineItem) => item.kind === "message" && item.message.pending
    ? requestTimes.get(turnKey(item) ?? "") : undefined;
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
