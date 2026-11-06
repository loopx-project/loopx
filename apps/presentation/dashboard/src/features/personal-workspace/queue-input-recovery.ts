import { chatQueueInputRequestSchema, type ChatQueueInputRequest } from "../../data/chat";

export function queueInputKeys(sessionId: string, turnId: string) {
  const request = `loopx.queue-input:${JSON.stringify([sessionId, turnId])}`;
  return {request, draft: `${request}:draft`};
}

export function readQueueInputRecovery(storage: Storage, sessionId: string, turnId: string) {
  const keys = queueInputKeys(sessionId, turnId);
  const raw = storage.getItem(keys.request), saved = storage.getItem(keys.draft);
  const editor: {message: string; revision: number} | null = saved === null ? null : JSON.parse(saved);
  if (saved !== null && (!editor || typeof editor !== "object" || typeof editor.message !== "string" || !Number.isSafeInteger(editor.revision) || editor.revision < 0)) {
    throw new Error("Invalid edit recovery");
  }
  return {request: raw === null ? null : chatQueueInputRequestSchema.parse(JSON.parse(raw)), editor};
}

export type QueueInputRecovery = {
  turnId: string; request: ChatQueueInputRequest | null; editor: {message: string; revision: number} | null;
};

/** An old response may retire only its own retained intent, never a newer edit. */
export function retireQueueInputRequest(storage: Storage, sessionId: string, turnId: string,
  request: ChatQueueInputRequest, applied: boolean) {
  const current = readQueueInputRecovery(storage, sessionId, turnId);
  if (JSON.stringify(current.request) !== JSON.stringify(chatQueueInputRequestSchema.parse(request))) return;
  const keys = queueInputKeys(sessionId, turnId);
  if (!applied && request.action === "edit" && !current.editor) storage.setItem(keys.draft,
    JSON.stringify({message: request.message, revision: request.expected_revision}));
  if (applied && request.action === "edit" && current.editor?.message === request.message &&
      current.editor.revision === request.expected_revision) storage.removeItem(keys.draft);
  storage.removeItem(keys.request);
}

/** This is unsent input/retry identity, never a second queue or a Turn status. */
export function listQueueInputRecovery(storage: Storage, sessionId: string) {
  const ids = new Set<string>();
  let failed = false;
  const entries: QueueInputRecovery[] = [];
  try {
    for (let i = 0; i < storage.length; i += 1) {
      const key = storage.key(i);
      if (!key?.startsWith("loopx.queue-input:")) continue;
      try {
        const identity: unknown = JSON.parse(key.slice("loopx.queue-input:".length).replace(/:draft$/, ""));
        if (Array.isArray(identity) && identity.length === 2 && identity[0] === sessionId && typeof identity[1] === "string") ids.add(identity[1]);
      } catch { /* An unresolvable identity must not be assigned to this Session. */ }
    }
    for (const turnId of ids) {
      try {
        const input = readQueueInputRecovery(storage, sessionId, turnId);
        if (input.request || input.editor) entries.push({turnId, ...input});
      } catch {failed = true;}
    }
  } catch {failed = true;}
  return {entries, failed};
}
