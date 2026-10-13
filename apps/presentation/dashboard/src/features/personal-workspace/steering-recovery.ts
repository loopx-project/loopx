/** Local drafts/retry identity only; Chat ingress/store still owns delivery. */
export type SteeringRequest = {
  sessionId: string;
  turnId: string;
  text: string;
  id: string;
};

const storageKey = "loopx-pw-composer-steering";

let requests: Map<string, SteeringRequest> | undefined;

function restoreRequests(): Map<string, SteeringRequest> {
  try {
    const raw = window.sessionStorage.getItem(storageKey);
    const entries: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(entries)) return new Map();
    return new Map(entries.flatMap((entry): [string, SteeringRequest][] => {
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

function persistRequests(): boolean {
  try {
    const serialized = JSON.stringify([...requestCache()]);
    window.sessionStorage.setItem(storageKey, serialized);
    return window.sessionStorage.getItem(storageKey) === serialized;
  } catch {
    // Keep the editable page draft, but callers must not dispatch without a
    // durable retry identity that survives a lost response and a page reload.
    return false;
  }
}

// One cache prevents composer and inline controls from overwriting each other's
// entries. Lazy restoration also supports environments without browser storage.
function requestCache() {
  return requests ??= restoreRequests();
}

export function readSteeringRequest(key: string) {
  return requestCache().get(key);
}

export function retainSteeringRequest(key: string, request: SteeringRequest): boolean {
  requestCache().set(key, request);
  if (persistRequests()) return true;
  // Another conversation's unsaved draft may prevent rewriting the whole cache.
  // Only the current request's exact durable identity qualifies its retry.
  const durable = restoreRequests().get(key);
  return durable?.sessionId === request.sessionId
    && durable.turnId === request.turnId
    && durable.text === request.text
    && durable.id === request.id;
}

export function retireSteeringRequest(key: string, id: string) {
  if (requestCache().get(key)?.id !== id) return;
  requestCache().delete(key);
  persistRequests();
}
