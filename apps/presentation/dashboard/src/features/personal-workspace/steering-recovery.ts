/** Local drafts/retry identity only; Chat ingress/store still owns delivery. */
export type SteeringRequest = {
  sessionId: string;
  turnId: string;
  text: string;
  id: string;
  /** Missing on retained legacy instructions; queue never retargets a Turn. */
  delivery?: "queue";
  attachments?: import("./personal-workspace-model").WorkspaceImageAttachment[];
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
          .every(value => typeof value === "string" && value.length > 0)
        || (request.delivery !== undefined && request.delivery !== "queue")
        || (request.attachments !== undefined && !Array.isArray(request.attachments))) return [];
      return [[entry[0], { sessionId: request.sessionId, turnId: request.turnId, text: request.text, id: request.id,
        ...(request.delivery === "queue" ? { delivery: "queue" as const, attachments: request.attachments } : {}) }]];
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

// One conversation-scoped cache preserves retry identities across navigation.
// Lazy restoration also supports environments without browser storage.
function requestCache() {
  return requests ??= restoreRequests();
}

export function readSteeringRequest(key: string) {
  return requestCache().get(key);
}

export function retainSteeringRequest(key: string, request: SteeringRequest): boolean {
  const previous = requestCache().get(key);
  requestCache().set(key, request);
  if (persistRequests()) return true;
  // Another conversation's unsaved draft may prevent rewriting the whole cache.
  // Only the current request's exact durable identity qualifies its retry.
  const durable = restoreRequests().get(key);
  const verified = durable?.sessionId === request.sessionId
    && durable.turnId === request.turnId
    && durable.text === request.text
    && durable.id === request.id && durable.delivery === request.delivery
    && JSON.stringify(durable.attachments) === JSON.stringify(request.attachments);
  if (!verified) {
    // No dispatch follows a failed qualification. The composer already owns
    // its editable draft; caching this attempt would falsely block an edit as
    // an uncertain delivery after storage recovers.
    if (previous) requestCache().set(key, previous);
    else requestCache().delete(key);
  }
  return verified;
}

export function retireSteeringRequest(key: string, id: string) {
  const request = requestCache().get(key);
  if (request?.id !== id) return;
  requestCache().delete(key);
  const legacyKey = JSON.stringify(["inline", request.sessionId, request.turnId]);
  if (requestCache().get(legacyKey)?.id === id) requestCache().delete(legacyKey);
  persistRequests();
}

// Upgrade recovery only. No new inline drafts are created by the current UI.
export function legacySteeringRequests(sessionId: string): Array<[string, SteeringRequest]> {
  return [...requestCache()].filter(([key, request]) => request.sessionId === sessionId
    && key === JSON.stringify(["inline", sessionId, request.turnId]));
}

export function restoreLegacySteeringRequest(key: string, legacyKey: string, sessionId: string): SteeringRequest | undefined {
  const request = legacySteeringRequests(sessionId).find(([candidate]) => candidate === legacyKey)?.[1];
  if (!request) return undefined;
  const current = readSteeringRequest(key);
  if (current && (current.id !== request.id || current.sessionId !== request.sessionId
    || current.turnId !== request.turnId || current.text !== request.text)) return undefined;
  // Copy durably before retiring the old location. A failed copy preserves both
  // page recovery and the old durable entry; dispatch still requires readback.
  if (retainSteeringRequest(key, request)) retireSteeringRequest(legacyKey, request.id);
  return request;
}
