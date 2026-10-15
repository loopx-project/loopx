import type { WorkspaceAttention } from "./personal-workspace-model";

/**
 * Browser notifications for items that need the owner.
 *
 * The preference is a per-browser convenience, like the theme: it lives in
 * local storage, defaults to off and never writes to the service. While it is
 * on, the page re-reads the workspace in the background so a decision that
 * appears while the owner is elsewhere reaches them; nothing is sent for
 * progress, runs or counts, only for a new item that waits on the owner.
 */

export const attentionNotificationStorageKey = "loopx-pw-attention-notifications";

export type AttentionNotificationSupport = "unsupported" | "default" | "granted" | "denied";

export function readAttentionNotificationPreference(): boolean {
  try {
    return window.localStorage.getItem(attentionNotificationStorageKey) === "on";
  } catch {
    return false;
  }
}

export function writeAttentionNotificationPreference(enabled: boolean): void {
  try {
    if (enabled) window.localStorage.setItem(attentionNotificationStorageKey, "on");
    else window.localStorage.removeItem(attentionNotificationStorageKey);
  } catch {
    // Storage may be unavailable; the in-memory state still applies to this page.
  }
}

/** The browser's current answer; "unsupported" when no Notification API exists. */
export function attentionNotificationSupport(scope: { Notification?: { permission: string } } = globalThis as never): AttentionNotificationSupport {
  const api = scope.Notification;
  if (!api || typeof api.permission !== "string") return "unsupported";
  return api.permission === "granted" ? "granted" : api.permission === "denied" ? "denied" : "default";
}

/** Identity of a needs-you item across reads: one Goal, one Todo or gate. */
export function attentionNotificationKey(item: Pick<WorkspaceAttention, "goalId" | "todoId">): string {
  return `${item.goalId}\u0000${item.todoId}`;
}

/** Items not present in the previously seen set, in projection order. */
export function newAttentionItems<T extends Pick<WorkspaceAttention, "goalId" | "todoId">>(seen: ReadonlySet<string>, items: readonly T[]): T[] {
  return items.filter((item) => !seen.has(attentionNotificationKey(item)));
}

/**
 * Notify only when the owner is not looking at this page: enabled, permitted,
 * and the document is hidden or unfocused. A visible, focused page already
 * shows the item in its needs-you lane.
 */
export function shouldNotifyAttention(input: { enabled: boolean; support: AttentionNotificationSupport; visible: boolean; focused: boolean }): boolean {
  return input.enabled && input.support === "granted" && (!input.visible || !input.focused);
}

/** Background re-read cadence while notifications are on; slower when hidden. */
export function attentionPollIntervalMs(hidden: boolean): number {
  return hidden ? 60_000 : 30_000;
}

export const attentionNotificationBatchLimit = 3;

export type AttentionNotificationContent = { body: string; tag: string; title: string };

/**
 * Owner-language content: the Goal title names where the decision lives and
 * the body is the item text. Beyond the batch limit one summary stands in for
 * the rest, so a burst never floods the owner.
 */
export function attentionNotificationContents(
  items: readonly WorkspaceAttention[],
  copy: { fallbackTitle: string; more: (count: number) => string },
): AttentionNotificationContent[] {
  const shown = items.slice(0, attentionNotificationBatchLimit).map((item) => ({
    body: item.text.length > 160 ? `${item.text.slice(0, 159)}…` : item.text,
    tag: attentionNotificationKey(item),
    title: item.goalTitle?.trim() || copy.fallbackTitle,
  }));
  const rest = items.length - shown.length;
  if (rest > 0) shown.push({ body: copy.more(rest), tag: "loopx-attention-more", title: copy.fallbackTitle });
  return shown;
}
