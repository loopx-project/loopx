import { request as httpRequest, type IncomingMessage, type ServerResponse } from 'node:http';

/**
 * `/api/loopx/<core path>` is forwarded to the LoopX core service (the Python chat server), which
 * keeps owning goals, todos, acceptance, the steward and the status projection. loochat adds its own
 * auth in front and nothing else: the page talks to one origin, and the core stays on loopback.
 *
 * Only the core's own namespaces are forwarded (`/status.json`, `/api/...`, `/healthz`); anything
 * else under `/api/loopx/` belongs to loochat's own routes.
 */
export const CORE_URL_DEFAULT = 'http://127.0.0.1:8792';
const PREFIX = '/api/loopx';

/** The core path a loochat path maps to, or null when it is not a core route. */
export function corePath(path: string): string | null {
  if (!path.startsWith(`${PREFIX}/`)) return null;
  const rest = path.slice(PREFIX.length);
  if (rest === '/status.json' || rest === '/healthz' || rest.startsWith('/api/')) return rest;
  return null;
}

const DROP_REQUEST = new Set(['host', 'authorization', 'cookie', 'connection', 'content-length', 'origin', 'referer']);
const DROP_RESPONSE = new Set(['connection', 'transfer-encoding', 'keep-alive']);

export function coreUrl(env: NodeJS.ProcessEnv = process.env): URL {
  return new URL(env.LOOCHAT_LOOPX_CORE_URL || CORE_URL_DEFAULT);
}

/** Streams the request to the core and the answer back, so event streams (SSE) keep flowing. */
export function proxyToCore(req: IncomingMessage, res: ServerResponse, path: string, search: string, core = coreUrl()): Promise<void> {
  return new Promise<void>((resolve) => {
    const headers: Record<string, string | string[]> = {};
    for (const [k, v] of Object.entries(req.headers)) if (v !== undefined && !DROP_REQUEST.has(k)) headers[k] = v;
    // The core is Python's http.server, which does not read a chunked request body. The body is buffered
    // (these are small JSON requests) and sent with its length; only the answer is streamed.
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('error', () => { res.destroy(); resolve(); });
    req.on('end', () => send(Buffer.concat(chunks)));
    const send = (payload: Buffer) => {
    const upstream = httpRequest(
      { hostname: core.hostname, port: core.port || 80, method: req.method, path: path + search,
        headers: { ...headers, host: core.host, ...(payload.length || (req.method !== 'GET' && req.method !== 'HEAD') ? { 'content-length': String(payload.length) } : {}) } },
      (answer) => {
        const out: Record<string, string | string[]> = {};
        for (const [k, v] of Object.entries(answer.headers)) if (v !== undefined && !DROP_RESPONSE.has(k)) out[k] = v;
        res.writeHead(answer.statusCode ?? 502, out);
        answer.pipe(res);
        answer.on('end', resolve);
        answer.on('error', () => { res.destroy(); resolve(); });
      },
    );
    upstream.on('error', (err) => {
      if (!res.headersSent) {
        res.writeHead(502, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'loopx_core_unavailable', message: `LoopX core is not reachable at ${core.origin}: ${err.message}` }));
      } else res.destroy();
      resolve();
    });
    // A page that goes away must not leave the core streaming to nobody.
    res.on('close', () => upstream.destroy());
    upstream.end(payload);
    };
  });
}
