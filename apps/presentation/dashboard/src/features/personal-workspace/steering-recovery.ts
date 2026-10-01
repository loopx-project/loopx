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

function persistRequests() {
  try {
    window.sessionStorage.setItem(storageKey, JSON.stringify([...requestCache()]));
  } catch {
    // Unavailable browser storage retains drafts/retries in this shared page cache.
    // No provider action or delivery conclusion follows from this cache.
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

export function retainSteeringRequest(key: string, request: SteeringRequest) {
  requestCache().set(key, request);
  persistRequests();
}

export function retireSteeringRequest(key: string, id: string) {
  if (requestCache().get(key)?.id !== id) return;
  requestCache().delete(key);
  persistRequests();
}
