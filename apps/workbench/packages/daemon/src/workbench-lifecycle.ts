/** Local CLI lifecycle, on its own private socket. Never exposed over HTTP or relay. */
import { createServer, type Server } from 'node:http';
import { randomUUID } from 'node:crypto';
import { listenPrivate } from './http.js';

export async function serveLifecycle(socket: string, deps: {
  url: string; appDir: string; dataDir: string; coreUrl: string;
  running: () => number;
  loginLink: () => Promise<string>;
  shutdown: () => Promise<void>;
}): Promise<Server> {
  const instance = randomUUID();
  let stopping = false;
  const server = createServer((req, res) => {
    const reply = (status: number, body: object) => {
      res.writeHead(status, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(body));
    };
    void (async () => {
      if (req.headers.origin) return reply(403, { ok: false, error: 'Local CLI only.' });
      if (req.method === 'GET' && req.url === '/status') {
        const reachable = await fetch(new URL('/healthz', deps.coreUrl), { signal: AbortSignal.timeout(1500) })
          .then(r => r.ok, () => false);
        return reply(200, { ok: true, service: 'loopx-workbench', instance_id: instance,
          state: stopping ? 'stopping' : 'running', pid: process.pid, parent_pid: process.ppid, url: deps.url,
          app_dir: deps.appDir, data_dir: deps.dataDir, running_threads: deps.running(),
          core: { url: new URL(deps.coreUrl).origin, reachable } });
      }
      if (req.headers['x-loopx-instance'] !== instance) return reply(409, { ok: false, error: 'Workbench instance changed; retry status.' });
      if (stopping) return reply(409, { ok: false, error: 'Workbench is stopping.' });
      if (req.method === 'POST' && req.url === '/access') {
        return reply(200, { ok: true, access_url: await deps.loginLink() });
      }
      if (req.method === 'POST' && (req.url === '/stop' || req.url === '/stop?force=1')) {
        const running = deps.running();
        if (running && req.url !== '/stop?force=1') return reply(409, { ok: false,
          error: `${running} running turn(s); wait for them or use stop --force to interrupt them.` });
        stopping = true;
        res.once('finish', () => { void deps.shutdown(); });
        return reply(200, { ok: true, state: 'stopping', instance_id: instance });
      }
      reply(404, { ok: false, error: 'Unknown lifecycle operation.' });
    })().catch(() => reply(500, { ok: false, error: 'Lifecycle operation failed; see daemon.log.' }));
  });
  if (!await listenPrivate(server, socket)) throw new Error('Another lifecycle owner already uses this socket.');
  return server;
}
