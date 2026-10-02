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
  // Non-conversation facts retain their existing grouping. Unknown dates stay
  // before dated conversation rows, rather than pretending to be new activity.
  const background = items.filter(item => item.kind !== "proposal" && item.kind !== "message");
  const conversation = items.filter(item => item.kind === "proposal" || item.kind === "message");
  return [...background, ...conversation.sort((a, b) => time(a) - time(b) || 0)];
}
