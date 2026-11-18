/**
 * Login through a link (`?token=<token>` / `#token=<token>`): the link carries a temporary token the daemon
 * issued (see the daemon's LinkTokens). The page script in index.html runs `takeLinkToken` before
 * anything renders, keeps the token where the app keeps its credential, and scrubs the address bar
 * at once — the token then never reaches history, bookmarks or a copied link.
 *
 * The rest of this module talks to the daemon about what the current credential is: the sidebar's
 * notice and Settings > Access read it, and a token that has expired or been revoked says so, so the
 * login page can tell an expired link from a mistyped token.
 */

import { saveToken } from './api';

/** Persona's rule, kept and taught hash routing: the token sits in the real query (`?token=<token>`),
 * in the fragment's own query (`#/goals?token=<token>`), or bare in the fragment (`#token=<token>`, which never
 * reaches a server). Returns the token and the link without it, for the address bar at once. */
export function takeLinkToken(href: string): { token: string; rest: string } | null {
  const u = new URL(href);
  let token = u.searchParams.get('token')?.trim() ?? '';
  u.searchParams.delete('token');
  const rawHash = u.hash.startsWith('#') ? u.hash.slice(1) : u.hash;
  const at = rawHash.indexOf('?');
  const route = at === -1 ? rawHash : rawHash.slice(0, at);
  const bare = at === -1 && route.startsWith('token=');
  const params = new URLSearchParams(bare ? rawHash : at === -1 ? '' : rawHash.slice(at + 1));
  if (params.has('token')) {
    token ||= params.get('token')?.trim() ?? '';
    params.delete('token');
  }
  const kept = bare ? params.toString() : [route, params.toString() ? `?${params.toString()}` : ''].join('');
  const hash = kept ? `#${kept}` : '';
  if (!token && !href.includes('token=')) return null;
  return { token, rest: u.pathname + u.search + hash };
}

/**
 * Extracts a bare token from what a person pastes into a token field: a login link
 * (`http://…/#token=…` or `?token=…`), an `.env` line (`LOOCHAT_TOKEN=…`), a `Bearer …` header,
 * or a chat message containing surrounding text around the token.
 */
export function normalizeTokenInput(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return '';
  for (const part of trimmed.split(/\s+/)) {
    if (/^https?:\/\//i.test(part) || part.includes('token=')) {
      try {
        const candidate = /^https?:\/\//i.test(part) ? part : `http://localhost/${part.startsWith('/') ? part.slice(1) : part}`;
        const found = takeLinkToken(candidate);
        if (found?.token) return found.token.trim();
      } catch { /* fall through */ }
    }
  }
  const envMatch = /(?:^|\s)(?:LOOCHAT_TOKEN|CALLI_TOKEN|RAH_TOKEN|Bearer)\s*[=:\s]\s*['"]?([A-Za-z0-9._~+/-]+)['"]?/i.exec(trimmed);
  if (envMatch?.[1]) return envMatch[1];
  if (/[^\x21-\x7e]/.test(trimmed)) {
    const linkMatch = /\b(wl_[A-Za-z0-9]{16,})\b/.exec(trimmed);
    if (linkMatch?.[1]) return linkMatch[1];
    const tokenMatch = /\b([A-Za-z0-9_-]{24,128})\b/.exec(trimmed);
    if (tokenMatch?.[1]) return tokenMatch[1];
  }
  return trimmed;
}

/** True when a token can be sent in an HTTP Authorization header without throwing TypeError. */
export function isHeaderSafeToken(token: string): boolean {
  return token.length > 0 && !/[^\x21-\x7e]/.test(token);
}

/** Runs before the app: keep the token, scrub the bar. Returns what happened, for the log only. */
export function captureLinkToken(href: string = location.href): 'kept' | 'none' | 'dropped' {
  const found = takeLinkToken(href);
  if (!found) return 'none';
  // An empty `?token=` (a link that was scrubbed and then shared) must not erase a working login.
  if (!found.token) return 'dropped';
  try {
    saveToken(found.token);
  } catch {
    return 'dropped';
  }
  history.replaceState(null, '', found.rest || location.pathname);
  return 'kept';
}

export type AuthKind =
  | { kind: 'permanent' }
  | { kind: 'link'; linkId: string; expiresAt: string };

export class AuthKindError extends Error {
  constructor(readonly reason: 'link_expired' | 'link_revoked' | 'unauthorized' | 'unreachable') {
    super(reason);
  }
}

/** What this credential is. A dead link token is an error naming the reason. */
export async function fetchAuthKind(token: string | undefined, signal?: AbortSignal): Promise<AuthKind> {
  if (token !== undefined && !isHeaderSafeToken(token)) throw new AuthKindError('unauthorized');
  let res: Response;
  try {
    res = await fetch('/api/auth/kind', { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === 'AbortError') throw cause;
    throw new AuthKindError('unreachable');
  }
  if (res.status === 401) {
    const body = await res.json().catch(() => ({})) as { auth_error?: string };
    if (body.auth_error === 'link_expired') throw new AuthKindError('link_expired');
    if (body.auth_error === 'link_revoked') throw new AuthKindError('link_revoked');
    throw new AuthKindError('unauthorized');
  }
  if (!res.ok) throw new AuthKindError('unreachable');
  const body = await res.json().catch(() => ({})) as { kind?: string; linkId?: string; expiresAt?: string };
  if (body.kind === 'permanent') return { kind: 'permanent' };
  if (body.kind === 'link' && typeof body.linkId === 'string' && typeof body.expiresAt === 'string') {
    return { kind: 'link', linkId: body.linkId, expiresAt: body.expiresAt };
  }
  throw new AuthKindError('unreachable');
}

export interface IssuedLink {
  id: string;
  createdAt: string;
  expiresAt: string;
  revokedAt: string | null;
  lastUsedAt: string | null;
}

/** Issues a login link. Only the permanent token may call this; the daemon enforces the same. */
export async function createLoginLink(token: string, ttlDays?: number): Promise<{ token: string; record: IssuedLink }> {
  const res = await fetch('/api/auth/link-tokens', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(ttlDays ? { ttlDays } : {}),
  });
  if (!res.ok) throw new Error(`could not issue a login link (${res.status})`);
  const body = await res.json() as { token: string; id: string; createdAt: string; expiresAt: string; revokedAt: null; lastUsedAt: null };
  return { token: body.token, record: { id: body.id, createdAt: body.createdAt, expiresAt: body.expiresAt, revokedAt: null, lastUsedAt: null } };
}

export async function listLoginLinks(token: string): Promise<IssuedLink[]> {
  const res = await fetch('/api/auth/link-tokens', { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error(`could not read the login links (${res.status})`);
  const body = await res.json() as { links?: IssuedLink[] };
  return Array.isArray(body.links) ? body.links : [];
}

export async function revokeLoginLink(token: string, id: string): Promise<boolean> {
  const res = await fetch(`/api/auth/link-tokens/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) throw new Error(`could not revoke the login link (${res.status})`);
  const body = await res.json() as { revoked?: boolean };
  return body.revoked === true;
}

/** The address that opens this Workbench logged in. The link token lives in the fragment: it never reaches a server log. */
export function loginLinkUrl(linkToken: string): string {
  return `${location.origin}/#token=${encodeURIComponent(linkToken)}`;
}
