import { request as httpRequest, type IncomingMessage, type ServerResponse } from 'node:http';
import type { Plugin } from 'vite';

/**
 * The one place a request can leave the workbench for a LoopX service. Route matching, the method
 * check and the forwarding are the same code, so there is no second opinion about what is allowed:
 * a request that matches no route below is answered here and never reaches an upstream. Vite's own
 * `server.proxy` / `preview.proxy` are not used.
 */
export type Upstream = 'status' | 'chat';

export interface Route {
  methods: readonly string[];
  path: RegExp;
  upstream: Upstream;
}

/** A session id is one plain segment: no dots-only names, no encoded separators. */
const SEGMENT = '(?!\\.{1,2}$)[A-Za-z0-9._~-]+';

export const ALLOWED_ROUTES: readonly Route[] = [
  { methods: ['GET', 'HEAD'], path: /^\/status\.json$/, upstream: 'status' },
  { methods: ['GET', 'HEAD'], path: /^\/ssh-hosts$/, upstream: 'chat' },
  { methods: ['GET', 'HEAD'], path: /^\/api\/chat\/(capabilities|endpoints|goals\/contexts)$/, upstream: 'chat' },
  { methods: ['GET', 'HEAD'], path: /^\/api\/chat\/sessions$/, upstream: 'chat' },
  { methods: ['GET', 'HEAD'], path: new RegExp(`^/api/chat/sessions/${SEGMENT}$`), upstream: 'chat' },
];

/**
 * Paths that belong to the services. A request here that matches no route gets a local 404 instead
 * of the page's index.html. This only chooses a status for the local answer: whatever it misses
 * falls through to Vite's static server, which has nothing to forward to.
 */
const SERVICE_NAMESPACE = /^\/(status\.json|ssh-hosts|api)/i;

export interface ForwardTargets {
  statusBase: string;
  chatBase: string;
}

export const DEFAULT_TARGETS: ForwardTargets = {
  statusBase: process.env.LOOPX_STATUS_BASE ?? 'http://127.0.0.1:8766',
  chatBase: process.env.LOOPX_CHAT_BASE ?? 'http://127.0.0.1:8767',
};

export type Decision =
  | { kind: 'forward'; upstream: Upstream; target: string }
  | { kind: 'reject'; status: 404 | 405 }
  | { kind: 'pass' };

/** Decides from the raw request line alone: nothing is decoded or normalized before matching. */
export function decide(method: string | undefined, url: string | undefined): Decision {
  const raw = url ?? '';
  if (!raw.startsWith('/') || raw.startsWith('//')) return { kind: 'reject', status: 404 };
  const [beforeFragment] = raw.split('#');
  const cut = beforeFragment.indexOf('?');
  const path = cut < 0 ? beforeFragment : beforeFragment.slice(0, cut);
  const route = ALLOWED_ROUTES.find((candidate) => candidate.path.test(path));
  if (!route) return SERVICE_NAMESPACE.test(path) ? { kind: 'reject', status: 404 } : { kind: 'pass' };
  if (!route.methods.includes((method ?? 'GET').toUpperCase())) return { kind: 'reject', status: 405 };
  return { kind: 'forward', upstream: route.upstream, target: beforeFragment };
}

const HOP_BY_HOP = new Set(['connection', 'keep-alive', 'transfer-encoding', 'upgrade', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer']);

function refuse(response: ServerResponse, status: number, message = 'read-only workbench proxy') {
  response.statusCode = status;
  response.setHeader('content-type', 'text/plain; charset=utf-8');
  response.end(message);
}

export function createReadOnlyForwarder(targets: ForwardTargets = DEFAULT_TARGETS) {
  return (request: IncomingMessage, response: ServerResponse, next: () => void): void => {
    const decision = decide(request.method, request.url);
    if (decision.kind === 'pass') return next();
    if (decision.kind === 'reject') return refuse(response, decision.status);
    const base = new URL(decision.upstream === 'status' ? targets.statusBase : targets.chatBase);
    const headers: Record<string, string> = {};
    for (const name of ['accept', 'accept-language']) {
      const value = request.headers[name];
      if (typeof value === 'string') headers[name] = value;
    }
    const upstream = httpRequest({
      host: base.hostname,
      port: base.port || 80,
      method: request.method === 'HEAD' ? 'HEAD' : 'GET',
      path: decision.target,
      headers,
      timeout: 15_000,
    }, (answer) => {
      response.statusCode = answer.statusCode ?? 502;
      for (const [name, value] of Object.entries(answer.headers)) {
        if (value !== undefined && !HOP_BY_HOP.has(name)) response.setHeader(name, value);
      }
      answer.pipe(response);
    });
    upstream.on('timeout', () => upstream.destroy(new Error('timeout')));
    upstream.on('error', () => { if (!response.headersSent) refuse(response, 502, 'LoopX service unavailable'); else response.destroy(); });
    upstream.end();
  };
}

/** The same forwarder in `vite` and in `vite preview`. */
export function readOnlyForward(targets?: ForwardTargets): Plugin {
  const forwarder = createReadOnlyForwarder(targets);
  return {
    name: 'loopx-readonly-forward',
    configureServer(server) { server.middlewares.use(forwarder); },
    configurePreviewServer(server) { server.middlewares.use(forwarder); },
  };
}
