/** Client retry identity only; the Chat ingress/store still owns delivery. */
export type ComposerSteeringRequest = {
  sessionId: string;
  turnId: string;
  text: string;
  id: string;
};

const storageKey = "loopx-pw-composer-steering";

export function readComposerSteeringRequests(): Map<string, ComposerSteeringRequest> {
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    const entries: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(entries)) return new Map();
    return new Map(entries.flatMap((entry): [string, ComposerSteeringRequest][] => {
      if (!Array.isArray(entry) || entry.length !== 2 || typeof entry[0] !== "string") return [];
      const request = entry[1];
      if (!request || typeof request !== "object" || Array.isArray(request)
        || ![request.sessionId, request.turnId, request.text, request.id]
          .every(value => typeof value === "string" && value.length > 0)) return [];
      return [[entry[0], { sessionId: request.sessionId, turnId: request.turnId, text: request.text, id: request.id }]];
    }));
  } catch {
    return new Map();
  }
}

export function persistComposerSteeringRequests(requests: ReadonlyMap<string, ComposerSteeringRequest>) {
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify([...requests]));
  } catch {
    // As with composer drafts, unavailable browser storage retains only memory.
    // No provider action or delivery conclusion follows from this cache.
  }
}
