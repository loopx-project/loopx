import type { PushService } from './push.js';
import type { FeishuChat } from './feishu-chat.js';
import { DEFAULT_RULES } from './autotitle.js';
import { createHash, createHmac, timingSafeEqual } from 'node:crypto';
import { createReadStream, existsSync, mkdirSync, readdirSync, rmSync, statSync } from 'node:fs';
import { createServer, type IncomingMessage, type Server, type ServerResponse } from 'node:http';
import { connect } from 'node:net';
import { dirname, extname, join, normalize, resolve, sep } from 'node:path';
import { gzipSync } from 'node:zlib';
import type { ApprovalDecision } from './adapters/types.js';
import type { AgentRegistry } from './agents.js';
import type { StoredEvent } from './events.js';
import type { HistoryEvent } from './history.js';
import type { LocalSessions } from './local-sessions.js';
import type { MachineUsageMeter } from './machine-usage.js';
import { refuse, type Desktop } from './desktop.js';
import { HttpError } from './errors.js';
import type { ExtensionHost } from './extension-host.js';
import type { HostRegistry } from './hosts.js';
import { PREVIEW_PICKER } from './preview-picker.js';
import { checkGrant, cookieValue, LEGACY_PREVIEW_COOKIE, makeGrant, PREVIEW_COOKIE, readPreviewFile } from './preview.js';
import type { Scheduler } from './scheduler.js';
import type { TerminalEvent, TerminalManager } from './terminals.js';
import type { ThreadManager } from './threads.js';
import { isWithinWorkdirs } from './workdirs.js';
import { listProjectDirectories } from './project-directories.js';
import { MAX_FILE_BYTES, MAX_VIDEO_BYTES } from './videos.js';
import type { VoiceService } from './voice-service.js';
import { sendToolArtifact } from './borrowing-server.js';
import { PetService } from './pets.js';
import { corePath, proxyToCore } from './adapters/loopx/proxy.js';
import { todoStages } from './todo-stage.js';
import { teamMessage } from './teams.js';

export interface HttpDeps {
  push?: PushService;
  feishu?: FeishuChat;
  manager: ThreadManager;
  /** Agent instances beyond the built-in ones. Optional so simple setups and tests need none. */
  registry?: AgentRegistry;
  /** Web terminals. Optional so deployments and tests can run without them. */
  terminals?: TerminalManager;
  /** This machine's VNC desktop, passed through to the page. Absent on the preview port and in tests without one. */
  desktop?: Desktop;
  /** Other machines this hub shows. Optional so simple setups and tests need none. */
  hosts?: HostRegistry;
  /** Scheduled tasks. Optional so tests can run without one. */
  scheduler?: Scheduler;
  /** LoopX extensions on this machine. Optional so tests can run without them. */
  extensions?: ExtensionHost;
  /** The built-in 自动取名 extension's daemon side, when it ships. */
  autotitle?: import('./autotitle.js').AutoTitle;
  /** This machine's agent sessions that are not threads here. Optional so tests need none. */
  localSessions?: LocalSessions;
  /** This machine's token usage, metered off the agents' transcripts. */
  machineUsage?: MachineUsageMeter;
  /** Host-owned native voice; absent on deployments that do not provide it. */
  voice?: VoiceService;
  /** The desk's companion and its downloaded frames. Defaults to the one under the LoopX home directory. */
  pets?: PetService;
  /**
   * Requests need no token: the server listens on a socket only this user can open, and a hub
   * reaches it through the local relay client. Such a server shows this machine only, never the machines added to it.
   */
  trusted?: boolean;
  token: string;
  webDir: string | null;
  version: string;
  /**
   * The port previews are served on. The iframe needs an origin of its own: on the app's port it
   * would be either opaque (and the browser would block its subresources) or same-origin with
   * the app (and its pages could reach into it). Null when no preview port could be opened.
   */
  previewPort?: number | null;
}

const MAX_BODY = 1024 * 1024;
/** Turns can carry images as base64, so they get a larger limit than other requests. */
const MAX_TURN_BODY = 45 * 1024 * 1024;
// Everything that changes what the thread list shows: a thread's state and pending user requests.
const GLOBAL_EVENT_TYPES = new Set([
  'thread.created',
  'thread.updated',
  'turn.started',
  'turn.done',
  'turn.error',
  'approval.request',
  'approval.resolved',
  'input.request',
  'input.resolved',
  'todo.updated',
  'agent.modelsChanged',
  // An account's plan usage moved: what the agent said while answering, or a probe.
  'quota.updated',
  // Not a thread event: written by the extension host's own subscription below, like schedules.changed.
  'extensions.changed',
]);

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.webmanifest': 'application/manifest+json',
};

/** Constant-time check of a bearer token. Hashing first makes the length comparison safe. */
export function tokenMatches(given: string | undefined, expected: string): boolean {
  if (!given) return false;
  const a = createHash('sha256').update(given).digest();
  const b = createHash('sha256').update(expected).digest();
  return timingSafeEqual(a, b);
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  const payload = JSON.stringify(body);
  // Thread logs reach megabytes of JSON; a browser that accepts gzip gets many times less to
  // transfer. `res.req` is the request this response belongs to.
  const accepts = String(res.req?.headers['accept-encoding'] ?? '');
  if (accepts.includes('gzip') && payload.length > 1024) {
    const gzipped = gzipSync(payload);
    if (gzipped.length < payload.length) {
      res.writeHead(status, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Encoding': 'gzip',
        Vary: 'Accept-Encoding',
        'Cache-Control': 'no-store',
      });
      res.end(gzipped);
      return;
    }
  }
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(payload),
    'Cache-Control': 'no-store',
  });
  res.end(payload);
}

function sendError(res: ServerResponse, err: unknown): void {
  if (err instanceof HttpError) {
    sendJson(res, err.status, { ...err.details, error: err.message });
    return;
  }
  const message = err instanceof Error ? err.message : 'internal error';
  console.error('[http]', err);
  sendJson(res, 500, { error: message });
}

async function readJson(req: IncomingMessage, limit = MAX_BODY): Promise<any> {
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > limit) throw new HttpError(413, 'request body too large');
    chunks.push(chunk as Buffer);
  }
  if (chunks.length === 0) return {};
  try {
    return JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new HttpError(400, 'invalid JSON body');
  }
}

function openSse(res: ServerResponse, privateStream = false): void {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream; charset=utf-8',
    'Cache-Control': privateStream ? 'no-store' : 'no-cache',
    Connection: 'keep-alive',
    'X-Accel-Buffering': 'no',
  });
  res.write(': connected\n\n');
}

function writeEvent(res: ServerResponse, ev: StoredEvent | HistoryEvent): void {
  res.write(`id: ${ev.seq}\nevent: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`);
}

/** Terminal frames carry no seq, so they get their own simpler SSE writer. */
function writeTerminalEvent(res: ServerResponse, ev: TerminalEvent): void {
  res.write(`event: ${ev.type}\ndata: ${JSON.stringify(ev)}\n\n`);
}

function serveStatic(res: ServerResponse, webDir: string, urlPath: string): void {
  const rel = urlPath === '/' ? 'index.html' : decodeURIComponent(urlPath.slice(1));
  let file = normalize(join(webDir, rel));
  if (!file.startsWith(webDir + sep) && file !== webDir) {
    res.writeHead(403).end();
    return;
  }
  if (!existsSync(file) || statSync(file).isDirectory()) file = join(webDir, 'index.html'); // SPA fallback
  if (!existsSync(file)) {
    res.writeHead(404).end('web UI not built');
    return;
  }
  // The build gives files under assets/ content-hashed names, so they are safe to cache forever.
  // Everything else — index.html above all — must be revalidated, or a phone keeps running the
  // build it loaded and never even hears about a newer one.
  const immutable = file.startsWith(join(webDir, 'assets') + sep);
  res.writeHead(200, {
    'Content-Type': MIME[extname(file)] ?? 'application/octet-stream',
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
  });
  createReadStream(file).pipe(res);
}

/** How long a signed download link works. Long enough to watch or seek a long video in one sitting. */
const DOWNLOAD_GRANT_S = 6 * 60 * 60;

/**
 * A short-lived signature for one file of one thread, so a `<video>` tag or a save link — neither
 * of which can send an Authorization header — can still fetch it. Tied to the path as well as the
 * thread, so it opens that file and nothing else.
 */
function downloadSignature(secret: string, threadId: string, path: string, exp: number): string {
  return createHmac('sha256', secret).update(`${threadId}\n${path}\n${exp}`).digest('hex');
}

function makeDownloadGrant(secret: string, threadId: string, path: string, now = Date.now()): { exp: number; sig: string } {
  const exp = Math.floor((now + DOWNLOAD_GRANT_S * 1000) / 1000);
  return { exp, sig: downloadSignature(secret, threadId, path, exp) };
}

function checkDownloadGrant(secret: string, threadId: string, path: string, exp: string | null, sig: string | null, now = Date.now()): void {
  const expired = new HttpError(403, 'this download link has expired; open it again from the thread');
  if (!exp || !sig || !/^\d+$/.test(exp) || Number(exp) * 1000 < now) throw expired;
  const want = Buffer.from(downloadSignature(secret, threadId, path, Number(exp)), 'utf8');
  const got = Buffer.from(sig, 'utf8');
  if (want.length !== got.length || !timingSafeEqual(want, got)) throw expired;
}

/**
 * Serves one of a thread's files for downloading or playing. Range requests get a 206 so a video
 * can be seeked and a phone can start before the whole file has arrived; a file sent without one
 * is served whole. `save` (from `?dl=1`) forces the download prompt even for a type the browser
 * would rather open, such as a video or a PDF.
 */
function serveDownload(
  req: IncomingMessage,
  res: ServerResponse,
  file: { path: string; mimeType: string; size: number; name: string },
  save: boolean,
): void {
  // A file name may hold anything a person typed: quotes, a newline, a semicolon. The plain name
  // is stripped to what a header can carry, and the exact one follows as UTF-8 (RFC 5987), which
  // is how browsers keep a Chinese file name intact.
  const plain = file.name.replace(/[^\w.\- ]/g, '_').replace(/["\\]/g, '_') || 'file';
  const utf8 = encodeURIComponent(file.name).replace(/['()*]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`);
  const headers: Record<string, string | number> = {
    'Content-Type': file.mimeType,
    'Accept-Ranges': 'bytes',
    'Content-Disposition': `${save ? 'attachment' : 'inline'}; filename="${plain}"; filename*=UTF-8''${utf8}`,
    'X-Content-Type-Options': 'nosniff',
    // The agent may rewrite the file any minute: what arrives is what is on disk now.
    'Cache-Control': 'private, no-store',
  };

  const range = req.headers.range;
  const match = typeof range === 'string' ? /^bytes=(\d*)-(\d*)$/.exec(range.trim()) : null;
  if (!match) {
    res.writeHead(200, { ...headers, 'Content-Length': file.size });
    createReadStream(file.path).pipe(res);
    return;
  }

  // A suffix range (`-500`) counts back from the end; any other range runs from its start to its
  // end or, with none given, to the last byte.
  const [, startText, endText] = match;
  let start = startText === '' ? file.size - Number(endText) : Number(startText);
  let end = startText === '' || endText === '' ? file.size - 1 : Number(endText);
  start = Math.max(0, start);
  end = Math.min(end, file.size - 1);
  if (!Number.isFinite(start) || !Number.isFinite(end) || start > end || start >= file.size) {
    res.writeHead(416, { 'Content-Range': `bytes */${file.size}` }).end();
    return;
  }
  res.writeHead(206, {
    ...headers,
    'Content-Range': `bytes ${start}-${end}/${file.size}`,
    'Content-Length': end - start + 1,
  });
  createReadStream(file.path, { start, end }).pipe(res);
}

export function createHttpServer(deps: HttpDeps): Server {
  const { manager, registry, terminals, desktop, hosts, scheduler, extensions, voice, trusted, token, webDir, version, autotitle, localSessions, machineUsage } = deps;
  const pets = deps.pets ?? new PetService();

  const handle = async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    const path = url.pathname;
    const method = req.method ?? 'GET';

    if (path.startsWith('/pets/') && (method === 'GET' || method === 'HEAD') && pets.serve(path, res)) return;
    if (!path.startsWith('/api/')) {
      if (webDir && (method === 'GET' || method === 'HEAD')) return serveStatic(res, webDir, path);
      res.writeHead(404).end();
      return;
    }

    if (path === '/api/preview-bridge.js' && method === 'GET') { res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }); res.end(PREVIEW_PICKER); return; }
    if (path === '/api/health') return sendJson(res, 200, { ok: true, version });
    // A machine that connects out to this hub, with its own join token rather than the hub's.
    if (path.startsWith('/api/relay/') && hosts && !trusted) return hosts.relay.handle(req, res, path);

    const auth = req.headers.authorization ?? '';
    const bearer = auth.startsWith('Bearer ') ? auth.slice(7) : undefined;
    // One route carries its own auth instead: preview files are fetched by an iframe, which cannot
    // send headers, so it opens with a short-lived signature verified inside the route. A download
    // signed for a `<video>` tag or a save link loses the header the same way and verifies the same.
    const signedFileRoute = /^\/api\/threads\/[^/]+\/files\//.test(path);
    const signedDownloadRoute = /^\/api\/threads\/[^/]+\/download$/.test(path) && url.searchParams.has('sig');
    if (!trusted && !signedFileRoute && !signedDownloadRoute && !tokenMatches(bearer, token)) return sendJson(res, 401, { error: 'unauthorized' });

    // LoopX core (goals, todos, steward, status): forwarded as is, behind this hub's own auth.
    const core = corePath(path);
    if (core !== null) return proxyToCore(req, res, core, url.search);

    const parts = path.split('/').filter(Boolean); // ['api', ...]
    /** Everything that would break if this instance disappeared from the registry. */
    const agentReferences = (id: string) => {
      const threads = [...manager.listThreads(), ...manager.listThreads({ archived: true })].filter((t) => t.agent === id).length;
      const schedules = scheduler?.list().filter((s) => s.agent === id).length ?? 0;
      const teams = manager.coordination?.definitions.list().filter((team) => team.members.some((m) => m.agent === id)).length ?? 0;
      const orders = manager.autoHandover.settings().orders;
      const handoverOrders = Object.entries(orders).filter(([source, targets]) => source === id || targets.some((t) => t.agent === id)).length;
      return { threads, schedules, teams, handoverOrders };
    };

    if (path === '/api/general-settings') {
      if (method === 'GET') return sendJson(res, 200, manager.generalSettings());
      if (method === 'PUT') return sendJson(res, 200, manager.saveGeneralSettings(await readJson(req)));
    }
    if (path === '/api/draft-model-preferences') {
      if (method === 'GET') return sendJson(res, 200, await manager.draftModelPreferences());
      if (method === 'PUT') return sendJson(res, 200, await manager.saveDraftModelPreferences(await readJson(req)));
      if (method === 'PATCH') return sendJson(res, 200, await manager.patchDraftModelPreference(await readJson(req)));
    }
    if (path === '/api/personal/plan' && method === 'GET') return sendJson(res, 200, await manager.personal.plan.get(url.searchParams.get('range') ?? 'day'));
    // POST /api/personal/agent: switch her agent in place; a conversation under way is handed over.
    if (path === '/api/personal/agent' && method === 'POST') return sendJson(res, 200, await manager.personal.switchAgent(await readJson(req)));
    if (path === '/api/personal/settings') {
      if (method === 'GET') return sendJson(res, 200, await manager.personal.settingsView());
      if (method === 'PUT') { manager.personal.putSettings(await readJson(req)); return sendJson(res, 200, await manager.personal.settingsView()); }
    }
    if (path === '/api/personal/proactive' && method === 'GET') {
      const proactive = manager.personal.proactive;
      return sendJson(res, 200, url.searchParams.get('summary') === 'true'
        ? { settings: proactive.settings(), counts: proactive.counts(), asking: proactive.asking() }
        : { settings: proactive.settings(), runs: proactive.runs(url.searchParams.get('matterId') ?? undefined) });
    }
    if (path === '/api/personal/proactive/settings' && method === 'PUT') return sendJson(res, 200, await manager.personal.proactive.save(await readJson(req)));
    if (parts[1] === 'personal' && parts[2] === 'proactive' && parts[3] === 'runs' && parts[4] && parts.length === 6) {
      if (parts[5] === 'diff' && method === 'GET') return sendJson(res, 200, await manager.personal.proactive.diff(parts[4]));
      if (['merge', 'discard', 'revert'].includes(parts[5]) && method === 'POST') return sendJson(res, 200, await manager.personal.proactive.act(parts[4], parts[5] as 'merge' | 'discard' | 'revert'));
      if (parts[5] === 'start' && method === 'POST') return sendJson(res, 200, await manager.personal.proactive.startDispatch(parts[4]));
      if (parts[5] === 'answer' && method === 'POST') return sendJson(res, 200, await manager.personal.proactive.answer(parts[4], (await readJson(req))?.answer));
      if (parts[5] === 'close-matter' && method === 'POST') return sendJson(res, 200, manager.personal.proactive.closeMatter(parts[4]));
      if (parts[5] === 'dismiss' && method === 'POST') return sendJson(res, 200, manager.personal.proactive.dismiss(parts[4]));
      if (parts[5] === 'allow-repo' && method === 'POST') return sendJson(res, 200, await manager.personal.proactive.allowRepo(parts[4]));
      // Marking an observation as "this one is fine": a record of the person's judgement, never a start.
      if (parts[5] === 'approve' && method === 'POST') return sendJson(res, 200, manager.personal.proactive.approve(parts[4]));
    }
    if (path === '/api/personal/brief' && method === 'GET') return sendJson(res, 200, manager.briefs.preview(url.searchParams.get('kind')));
    if (path === '/api/personal/brief/last' && method === 'GET') return sendJson(res, 200, manager.briefs.last(url.searchParams.get('kind')));
    if (path === '/api/personal/brief/settings') {
      if (method === 'GET') return sendJson(res, 200, manager.briefs.settings());
      if (method === 'PUT') return sendJson(res, 200, manager.briefs.save(await readJson(req)));
    }
    if (path === '/api/personal/channels/lark') {
      if (method === 'GET') return sendJson(res, 200, manager.lark.settings());
      if (method === 'PUT') return sendJson(res, 200, manager.lark.save(await readJson(req)));
    }
    if (path === '/api/personal/channels/lark/status' && method === 'GET') return sendJson(res, 200, await manager.lark.status());
    // What has reached a matter: the archive the channel keeps of every signal and where it went.
    if (path === '/api/personal/signals' && method === 'GET') {
      const matterId = url.searchParams.get('matterId') ?? undefined;
      const rows = manager.personal.store.listSignals({ ...(matterId ? { matterId } : {}), limit: 50, materialScope: manager.lark.materialScope() });
      return sendJson(res, 200, { signals: rows.map(({ payload, disposition, ...rest }) => {
        const signal = payload as { summary?: unknown; source?: unknown; meeting?: unknown };
        return { ...rest, summary: signal.summary ?? null, source: signal.source ?? null, ...(signal.meeting ? { meeting: signal.meeting } : {}), disposition };
      }) });
    }
    if (parts[1] === 'personal' && parts[2] === 'signals' && parts[3] && parts[4] === 'material' && parts.length === 5 && method === 'GET') {
      let key: string;
      try { key = decodeURIComponent(parts[3]); } catch { throw new HttpError(400, 'invalid signal key'); }
      const read = await manager.lark.readMaterial(key);
      return sendJson(res, 200, { key, ...read });
    }
    if (path === '/api/personal/channels/lark/run' && method === 'POST') return sendJson(res, 200, await manager.lark.run());
    if (path === '/api/personal/channels/lark/search' && method === 'GET') return sendJson(res, 200, await manager.lark.search(url.searchParams.get('kind'), url.searchParams.get('q')));
    if (path === '/api/personal/say' && method === 'POST') return sendJson(res, 201, await manager.personal.router.say(await readJson(req)));
    if (path === '/api/personal/router' && method === 'GET') return sendJson(res, 200, manager.personal.router.status());
    if (path === '/api/personal/profile') {
      if (method === 'GET') return sendJson(res, 200, manager.personal.profile());
      if (method === 'PUT') return sendJson(res, 200, await manager.personal.saveProfile(await readJson(req)));
    }
    // The onboarding explore: readonly sources, one model judgement, a validated recommendation.
    if (path === '/api/personal/explore/sources' && method === 'GET') return sendJson(res, 200, await manager.personal.explore.sources());
    if (path === '/api/personal/explore') {
      if (method === 'GET') return sendJson(res, 200, manager.personal.explore.status());
      if (method === 'POST') {
        const started = manager.personal.explore.start(await readJson(req));
        return sendJson(res, started.running ? 200 : 202, { id: started.id });
      }
      if (method === 'DELETE') return sendJson(res, 200, manager.personal.explore.cancel());
    }
    if (path === '/api/wishes' && method === 'GET') return sendJson(res, 200, url.searchParams.get('summary') === 'true' ? { count: manager.personal.store.wishCount() } : manager.personal.store.listWishes());
    if (path === '/api/todos/summary') {
      if (method === 'GET') return sendJson(res, 200, manager.personal.summary.status());
      if (method === 'POST') return sendJson(res, 201, await manager.personal.summary.start(await readJson(req)));
    }
    if (parts[1] === 'todos' && parts[2] && parts[3] === 'delegate' && method === 'POST') return sendJson(res, 201, await manager.personal.delegate(parts[2], await readJson(req)));
    if (parts[1] === 'todos' && parts[2] && parts[3] === 'note') {
      if (method === 'GET') return sendJson(res, 200, manager.personal.note(parts[2]));
      if (method === 'PUT') return sendJson(res, 200, manager.personal.writeNote(parts[2], await readJson(req)));
    }
    if (parts[1] === 'todos' && parts[2] && parts[3] === 'proposals' && parts[4] && ['accept', 'dismiss'].includes(parts[5]) && method === 'POST') {
      return sendJson(res, 200, manager.personal.resolveProposal(parts[2], parts[4], parts[5] as 'accept' | 'dismiss', (await readJson(req)).rev));
    }

    if (path.startsWith('/api/push/')) {
      if (!deps.push) throw new HttpError(503, 'push is unavailable');
      if (path === '/api/push/settings' && method === 'GET') return sendJson(res, 200, await deps.push.getSettings());
      if (path === '/api/push/settings' && method === 'PUT') return sendJson(res, 200, deps.push.putSettings(await readJson(req)));
      if (path === '/api/push/status' && method === 'GET') return sendJson(res, 200, await deps.push.status(url.searchParams.get('summary') === 'true'));
      if (path === '/api/push/test' && method === 'POST') return sendJson(res, 202, deps.push.test());
    }

    if (path.startsWith('/api/feishu/')) {
      if (!deps.feishu) throw new HttpError(503, 'Feishu is unavailable');
      if (path === '/api/feishu/settings' && method === 'GET') return sendJson(res, 200, deps.feishu.getSettings());
      if (path === '/api/feishu/settings' && method === 'PUT') return sendJson(res, 200, deps.feishu.putSettings(await readJson(req)));
      if (path === '/api/feishu/status' && method === 'GET') return sendJson(res, 200, await deps.feishu.status(url.searchParams.get('summary') === 'true'));
      if (path === '/api/feishu/setup' && method === 'POST') return sendJson(res, 200, await deps.feishu.startSetup());
      if (path === '/api/feishu/login' && method === 'POST') return sendJson(res, 200, await deps.feishu.startLogin());
      if (path === '/api/feishu/setup' && method === 'DELETE') return sendJson(res, 200, await deps.feishu.cancelSetup());
      if (path === '/api/feishu/test' && method === 'POST') return sendJson(res, 202, await deps.feishu.test());
    }

    if (path === '/api/tools/command' && method === 'POST') {
      if (!manager.borrowing) throw new HttpError(503, 'tool borrowing is unavailable');
      return sendJson(res, 200, await manager.borrowing.command('operator', await readJson(req)));
    }
    if (parts[1] === 'tool-artifacts' && parts.length === 4 && method === 'GET') {
      if (!manager.borrowing) throw new HttpError(503, 'tool borrowing is unavailable');
      return sendToolArtifact(manager.borrowing, 'operator', parts[2], parts[3], res);
    }

    // Other machines. One level only: a machine reached through its socket does not pass requests on.
    if (parts[1] === 'hosts' && hosts && !trusted) {
      // GET /api/hosts, POST /api/hosts { label }
      if (path === '/api/hosts') {
        if (method === 'GET') return sendJson(res, 200, hosts.list());
        if (method === 'POST') return sendJson(res, 201, await hosts.add(await readJson(req)));
      }
      // Operator-only execution for maintenance scripts; node tokens cannot access this route.
      if (method === 'POST' && parts[2] && parts[3] === 'exec' && !parts[4]) {
        const body = await readJson(req);
        if (!body || typeof body.command !== 'string' || !body.command.trim()) throw new HttpError(400, 'command is required');
        if (body.input !== undefined && typeof body.input !== 'string') throw new HttpError(400, 'input must be a string');
        const result = await hosts.exec(parts[2], body.command, { input: body.input });
        return sendJson(res, 200, { stdout: result.stdout.toString('base64'), stderr: result.stderr, code: result.code });
      }
      // DELETE /api/hosts/:id
      if (method === 'DELETE' && parts[2] && !parts[3]) {
        hosts.remove(parts[2]);
        res.writeHead(204).end();
        return;
      }
      // POST /api/hosts/:id/reconnect: refresh the node connection state
      if (method === 'POST' && parts[2] && parts[3] === 'reconnect' && !parts[4]) {
        return sendJson(res, 200, await hosts.reconnect(parts[2]));
      }
      // /api/hosts/:id/proxy/...: the same API, answered by that machine's daemon
      if (parts[2] && parts[3] === 'proxy' && parts[4]) {
        if (parts[4] === 'hosts') throw new HttpError(400, 'a machine does not pass requests on to others');
        const rest = path.slice(`/api/hosts/${parts[2]}/proxy/`.length);
        return hosts.proxy(decodeURIComponent(parts[2]), `/api/${rest}${url.search}`, req, res);
      }
    }

    if (parts[1] === 'voice') {
      if (!voice) throw new HttpError(501, 'voice_unavailable');
      if (method === 'GET' && parts[2] === 'providers' && !parts[3]) {
        return sendJson(res, 200, { providers: await voice.refresh() });
      }
      // Separate browser ownership from the shared Hub bearer. Never accept it from a URL or body.
      // Pages loaded before the rename to LoopX still send the old header.
      const ownerId = req.headers['x-loochat-voice-owner'] ?? req.headers['x-rah-voice-owner'];
      if (typeof ownerId !== 'string' || !/^[a-zA-Z0-9_-]{20,128}$/.test(ownerId)) {
        throw new HttpError(400, 'voice_owner_required');
      }
      if (method === 'POST' && !parts[2]) return sendJson(res, 201, await voice.open(ownerId, await readJson(req, 128 * 1024)));
      const access = { id: parts[2] ?? '', extensionId: 'voice', ownerId };
      if (method === 'DELETE' && parts[2] && !parts[3]) {
        await voice.sessions.stop(access);
        res.writeHead(204).end();
        return;
      }
      if (method === 'POST' && parts[2] && !parts[4]) {
        if (parts[3] === 'text') {
          const body = await readJson(req);
          await voice.sessions.appendText(access, body?.text, { images: body?.images, videos: body?.videos });
          return sendJson(res, 200, {});
        }
        if (parts[3] === 'heartbeat') return sendJson(res, 200, voice.sessions.heartbeat(access));
        if (parts[3] === 'connected') return sendJson(res, 200, voice.sessions.mediaConnected(access));
      }
      if (method === 'GET' && parts[2] && parts[3] === 'events' && !parts[4]) {
        voice.sessions.get(access); // Reject another browser before opening any response stream.
        openSse(res, true);
        let off = () => {};
        let closed = false;
        const close = () => {
          if (closed) return;
          closed = true;
          off();
          void voice.sessions.stop(access);
        };
        res.once('close', close);
        off = voice.sessions.subscribe(access, event => {
          if (closed) return;
          // A stalled browser cannot accumulate unbounded transcript or SDP buffers.
          if (res.writableLength > 128 * 1024) { close(); res.destroy(); return; }
          res.write(`data: ${JSON.stringify(event)}\n\n`);
          if (event.type === 'state' && (event.session.state === 'closed' || event.session.state === 'failed')) res.end();
        });
        if (closed) off();
        return;
      }
      throw new HttpError(404, 'voice_route_not_found');
    }

    // `tz` is the viewer's IANA zone, so a day starts at their midnight rather than the host's.
    if (method === 'GET' && path === '/api/usage/daily') {
      const tz = url.searchParams.get('tz') || undefined;
      try { if (tz) new Intl.DateTimeFormat('en-CA', { timeZone: tz }); } catch { throw new HttpError(400, '时区无效'); }
      return sendJson(res, 200, manager.usageDaily(url.searchParams.get('agent') ?? '', tz));
    }
    if (method === 'GET' && (path === '/api/usage' || path === '/api/usage/costs')) {
      const hours = Number(url.searchParams.get('hours') ?? 24);
      if (![1, 24, 168].includes(hours)) throw new HttpError(400, '统计范围须为 1、24 或 168 小时');
      const agent = url.searchParams.get('agent') ?? '';
      return sendJson(res, 200, path === '/api/usage/costs' ? manager.usageCosts(hours, agent) : manager.usageReport(hours, agent));
    }
    // The daily digest of what was asked and what was called. `days` clamps to 1..90.
    if (method === 'GET' && path === '/api/activity') {
      const days = Number(url.searchParams.get('days') ?? 14);
      if (!Number.isFinite(days)) throw new HttpError(400, '天数须为数字');
      return sendJson(res, 200, { days: manager.activityReport(days, url.searchParams.get('agent') ?? '') });
    }
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'diagnostics' && !parts[4]) {
      return sendJson(res, 200, await manager.agentDiagnostic(parts[2], url.searchParams.get('refresh') === '1'));
    }

    // GET /api/agents
    if (method === 'GET' && path === '/api/agents') return sendJson(res, 200, await manager.listAgents());

    // GET /api/agents/supported: all agent types supported by the hub
    if (method === 'GET' && path === '/api/agents/supported') {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      return sendJson(res, 200, registry.supported());
    }

    // GET /api/agents/discover: CLIs on PATH that look like another copy of a known agent
    if (method === 'GET' && path === '/api/agents/discover') {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      return sendJson(res, 200, await registry.discover());
    }

    // GET /api/agents/accounts/discover: signed-in accounts of multi-account agents, ready to link
    if (method === 'GET' && path === '/api/agents/accounts/discover') {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      return sendJson(res, 200, await registry.discoverAccounts());
    }

    // POST /api/agents: add an instance (another CLI, another account)
    if (method === 'POST' && path === '/api/agents/accounts') {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      return sendJson(res, 201, await registry.createAccount(await readJson(req)));
    }

    if (parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'login' && !parts[5]) {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      res.setHeader('Cache-Control', 'no-store');
      if (method === 'POST' && !parts[4]) return sendJson(res, 201, registry.startLogin(parts[2]));
      if (parts[4]) {
        const login = registry.login(parts[2], parts[4]);
        if (method === 'GET') return sendJson(res, 200, { id: parts[4], ...login.state() });
        if (method === 'POST') {
          const body = await readJson(req);
          if (typeof body?.code !== 'string') throw new HttpError(400, 'a code is required');
          login.submit(body.code.trim());
          return sendJson(res, 200, { id: parts[4], ...login.state() });
        }
        if (method === 'DELETE') {
          login.cancel();
          res.writeHead(204).end();
          return;
        }
      }
    }

    if (method === 'POST' && path === '/api/agents') {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      const body = await readJson(req);
      return sendJson(res, 201, await registry.add(body));
    }

    // PATCH /api/agents/:id: change hub-owned settings without changing the instance id.
    if (method === 'PATCH' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && !parts[3]) {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      const body = await readJson(req);
      const instance = await registry.update(parts[2], { label: body.label, hidden: body.hidden, streamBridge: body.streamBridge });
      if (body.streamBridge !== undefined) manager.agentSettingsChanged(parts[2]);
      return sendJson(res, 200, instance);
    }

    // DELETE /api/agents/:id: remove only the hub's registration. Native files stay untouched.
    if (method === 'DELETE' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && !parts[3]) {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      registry.get(parts[2]);
      const references = agentReferences(parts[2]);
      if (Object.values(references).some((n) => n > 0)) {
        throw new HttpError(409, 'agent is still in use', { references });
      }
      await registry.remove(parts[2]);
      res.writeHead(204).end();
      return;
    }

    // GET /api/agents/:id/references: what would block removing this instance
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'references' && !parts[4]) {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      registry.get(parts[2]);
      return sendJson(res, 200, agentReferences(parts[2]));
    }

    // POST /api/agents/:id/login-terminal: start the CLI's login in a web terminal, in this
    // instance's own account environment. The command is built on the daemon, never sent by a page.
    if (method === 'POST' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'login-terminal' && !parts[4]) {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      if (!terminals) throw new HttpError(501, 'terminals are not enabled');
      const adapter = registry.adapters.get(parts[2]);
      if (!adapter) throw new HttpError(404, `unknown agent: ${parts[2]}`);
      const command = adapter.loginCommand?.();
      if (!command) throw new HttpError(400, 'this agent cannot be signed in from the hub');
      const cwd = manager.allowedWorkdirs[0] ?? process.cwd();
      const terminal = terminals.create(cwd);
      terminals.input(terminal.id, `${command}\r`);
      return sendJson(res, 201, { terminalId: terminal.id });
    }

    // GET /api/agents/:id/sessions: conversations the agent already has, for importing
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'sessions') {
      return sendJson(res, 200, await manager.listAgentSessions(parts[2]));
    }

    // GET /api/search?q=: turns in which the words were said, across every thread.
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'search' && !parts[2]) {
      return sendJson(res, 200, manager.search(url.searchParams.get('q')));
    }

    if (parts.join('/') === 'api/handover-settings') {
      if (method === 'GET') return sendJson(res, 200, manager.autoHandover.settings());
      if (method === 'PUT') return sendJson(res, 200, manager.autoHandover.configure(await readJson(req)));
    }

    // Auto-compact: quota-aware context compression ahead of the handover. The agent named in the
    // settings writes the brief (a bridge to third-party models spends no official-account quota).
    if (parts.join('/') === 'api/autocompact-settings') {
      if (method === 'GET') return sendJson(res, 200, { settings: manager.autoCompact.settings(), status: manager.autoCompact.status() });
      if (method === 'PUT') return sendJson(res, 200, manager.autoCompact.configure(await readJson(req)));
    }

    // GET /api/agents/:id/quota: plan usage, read outside any session and cached briefly.
    // ?refresh=1 skips the cache: the dashboard's refresh button means "ask again now".
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'quota') {
      return sendJson(res, 200, await manager.agentQuota(parts[2], url.searchParams.get('refresh') === '1'));
    }

    // GET /api/agents/:id/stream-bridge: would the experimental bridge work on this install
    // right now? Advisory status for the settings sheet; turns degrade on their own anyway.
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'stream-bridge' && !parts[4]) {
      if (!registry) throw new HttpError(404, 'this daemon has no agent registry');
      const adapter = registry.adapters.get(parts[2]);
      if (!adapter) throw new HttpError(404, `unknown agent: ${parts[2]}`);
      if (!adapter.capabilities.streamBridge || !adapter.streamBridgeEnvironment) throw new HttpError(400, 'this agent has no experimental stream bridge');
      const { incompatible } = await adapter.streamBridgeEnvironment(true);
      return sendJson(res, 200, incompatible === undefined ? { compatible: true } : { compatible: false, reason: incompatible });
    }

    // GET /api/agents/:id/commands
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'commands') {
      return sendJson(res, 200, await manager.listCommands(parts[2], url.searchParams.get('cwd') ?? undefined));
    }

    // GET /api/agents/:id/models
    if (method === 'GET' && parts[0] === 'api' && parts[1] === 'agents' && parts[2] && parts[3] === 'models') {
      return sendJson(res, 200, await manager.listModels(parts[2], url.searchParams.get('refresh') === '1'));
    }

    // Skills: what each agent instance has, where it came from, and how a copy moves between
    // agents and machines. The page drives cross-machine sync itself, through these same routes.
    if (parts[1] === 'skills' && !parts[2]) {
      if (method === 'GET') return sendJson(res, 200, manager.skills.listing());
      if (method === 'DELETE') {
        manager.skills.remove(url.searchParams.get('name') ?? '', url.searchParams.get('path'));
        res.writeHead(204).end();
        return;
      }
    }
    // GET /api/skills/detail?name=&path=: one skill and its SKILL.md, for the page to read
    if (method === 'GET' && parts[1] === 'skills' && parts[2] === 'detail' && !parts[3]) {
      return sendJson(res, 200, manager.skills.detail(url.searchParams.get('name') ?? '', url.searchParams.get('path')));
    }
    // GET /api/skills/export?name=&path=: a skill packed to carry to another machine
    if (method === 'GET' && parts[1] === 'skills' && parts[2] === 'export' && !parts[3]) {
      return sendJson(res, 200, manager.skills.payload(url.searchParams.get('name') ?? '', url.searchParams.get('path')));
    }
    // POST /api/skills/install { from?: { name, path }, skill?: payload, targets: [agent], overwrite? }
    if (method === 'POST' && parts[1] === 'skills' && parts[2] === 'install' && !parts[3]) {
      return sendJson(res, 200, await manager.skills.install(await readJson(req, 24 * 1024 * 1024)));
    }

    // MCP: what each agent instance's own config says, plus the one-entry writes the page asks
    // for. Cross-machine installs are driven by the page, machine by machine, like skill sync.
    // GET /api/mcp: this machine's scan
    if (method === 'GET' && parts[1] === 'mcp' && !parts[2]) return sendJson(res, 200, manager.mcp.listing());
    // DELETE /api/mcp?agent=&name=: take one server out of one instance's config
    if (method === 'DELETE' && parts[1] === 'mcp' && !parts[2]) {
      await manager.mcp.remove(url.searchParams.get('agent') ?? '', url.searchParams.get('name') ?? '');
      res.writeHead(204).end();
      return;
    }
    // POST /api/mcp/install { targets: [agent], name, config, overwrite? }
    if (method === 'POST' && parts[1] === 'mcp' && parts[2] === 'install' && !parts[3]) {
      return sendJson(res, 200, await manager.mcp.install(await readJson(req, 128 * 1024)));
    }
    // Registry entries: the business line and purpose in words, plus manually registered servers.
    // They belong to the hub the page talks to; the scan above stays per machine.
    if (parts[1] === 'mcp' && parts[2] === 'entries' && !parts[3]) {
      if (method === 'GET') return sendJson(res, 200, manager.mcp.entries.list());
      if (method === 'PUT') return sendJson(res, 200, manager.mcp.entries.save(await readJson(req, 128 * 1024)));
    }
    // DELETE /api/mcp/entries/:name
    if (method === 'DELETE' && parts[1] === 'mcp' && parts[2] === 'entries' && parts[3] && !parts[4]) {
      if (!manager.mcp.entries.remove(parts[3])) throw new HttpError(404, `没有名为 ${parts[3]} 的登记条目`);
      res.writeHead(204).end();
      return;
    }

    if (parts[1] === 'work-sources' && method === 'GET') {
      const consumer = url.searchParams.get('consumer') || undefined;
      if (consumer && !extensions?.list().some(s => s.manifest.id === consumer && s.enabled && s.granted.includes('worksource.read'))) throw new HttpError(403, 'work_source_permission_required');
      const result = await extensions?.external?.projections(consumer, url.searchParams.get('threadId') || undefined, url.searchParams.get('refresh') === '1') ?? [];
      if (consumer && !extensions?.list().some(s => s.manifest.id === consumer && s.enabled && s.granted.includes('worksource.read'))) throw new HttpError(403, 'work_source_permission_required');
      return sendJson(res, 200, result);
    }
    // LoopX extensions: the built-in catalog and what is installed on this machine.
    if (parts[1] === 'extensions') {
      if (!extensions) throw new HttpError(501, '此 daemon 未启用扩展');
      if (method === 'GET' && !parts[2]) await voice?.refresh();
      const external = extensions.external;
      if (parts[2] === 'prepare' && method === 'POST') {
        if (!external) throw new HttpError(501, 'external_extensions_unavailable');
        const body = await readJson(req);
        return sendJson(res, 200, await external.prepare(body?.origin, body?.sha256));
      }
      if (parts[2] === 'commit' && method === 'POST') {
        if (!external) throw new HttpError(501, 'external_extensions_unavailable');
        const body = await readJson(req);
        return sendJson(res, 200, await external.install(body?.token, body?.granted));
      }
      if (external?.has(parts[2])) {
        if (parts[3] === 'configuration') {
          if (method === 'GET') return sendJson(res, 200, external.configuration(parts[2]));
          if (method === 'PUT') return sendJson(res, 200, await external.configure(parts[2], await readJson(req)));
        }
        if (parts[3] === 'detect' && method === 'POST') return sendJson(res, 200, await external.detect(parts[2]));
        if (parts[3] === 'rollback' && method === 'POST') return sendJson(res, 200, external.rollback(parts[2]));
      }
      // GET /api/extensions
      if (method === 'GET' && !parts[2]) return sendJson(res, 200, extensions.list());
      // 自动取名: its settings, and a dry run of its rules on one thread.
      if (autotitle && parts[2] === 'autotitle' && parts[3] === 'settings' && !parts[4]) {
        if (method === 'GET') { extensions.demand('autotitle', 'events.summary'); return sendJson(res, 200, { settings: autotitle.settings(), status: autotitle.status(), pass: autotitle.passState(), defaultRules: DEFAULT_RULES }); }
        if (method === 'PUT') { extensions.demand('autotitle', 'events.summary'); return sendJson(res, 200, autotitle.configure(await readJson(req))); }
      }
      if (autotitle && method === 'POST' && parts[2] === 'autotitle' && parts[3] === 'preview' && !parts[4]) {
        const body = await readJson(req);
        if (typeof body?.threadId !== 'string') throw new HttpError(400, 'threadId missing');
        return sendJson(res, 200, await autotitle.name(body.threadId, { apply: false }));
      }
      // POST /api/extensions/autotitle/organize: name the backlog of default-titled threads.
      if (autotitle && method === 'POST' && parts[2] === 'autotitle' && parts[3] === 'organize' && !parts[4]) {
        extensions.demand('autotitle', 'events.summary');
        return sendJson(res, 200, autotitle.organize());
      }
      // Only the installed extension with a current grant may inspect native memory.
      if (method === 'GET' && parts[2] && parts[3] === 'memory') {
        if (parts[4] === 'sources' && !parts[5]) return sendJson(res, 200, await extensions.memorySources(parts[2]));
        if (parts[4] && parts[5] === 'files' && !parts[6]) return sendJson(res, 200, await extensions.memoryList(parts[2], parts[4]));
        if (parts[4] && parts[5] === 'document' && !parts[6]) return sendJson(res, 200, await extensions.memoryRead(parts[2], parts[4], url.searchParams.get('id') ?? ''));
      }
      // POST /api/extensions/:id/install { granted }: exactly the permissions the manifest asks for
      if (method === 'POST' && parts[2] && parts[3] === 'install' && !parts[4]) {
        if (extensions.list().find(e => e.manifest.id === parts[2])?.manifest.requires?.agentCapabilities?.includes('realtimeVoice')) await voice?.refresh(true);
        return sendJson(res, 200, extensions.install(parts[2], (await readJson(req))?.granted));
      }
      // PATCH /api/extensions/:id { enabled }
      if (method === 'PATCH' && parts[2] && !parts[3]) {
        const body = await readJson(req);
        // Disabling must revoke immediately, even while a provider probe is stalled.
        if (body?.enabled === true && extensions.list().find(e => e.manifest.id === parts[2])?.manifest.requires?.agentCapabilities?.includes('realtimeVoice')) await voice?.refresh(true);
        return sendJson(res, 200, extensions.setEnabled(parts[2], body?.enabled));
      }
      // DELETE /api/extensions/:id
      if (method === 'DELETE' && parts[2] && !parts[3]) {
        extensions.uninstall(parts[2]);
        res.writeHead(204).end();
        return;
      }
    }

    // GET /api/config: the project roots, and whether threads can work outside a project
    if (method === 'GET' && path === '/api/config') return sendJson(res, 200, manager.workdirsConfig());

    // Browse candidate project folders without widening file access or allowed working directories.
    if (method === 'POST' && path === '/api/context-references') {
      return sendJson(res, 201, manager.references.create((await readJson(req)) ?? {}));
    }

    if (method === 'GET' && path === '/api/project-directories') {
      return sendJson(res, 200, listProjectDirectories(manager.allowedWorkdirs, url.searchParams.get('path') ?? undefined));
    }

    // POST /api/workdirs { path }, DELETE /api/workdirs?path=: project roots added from the page
    if (path === '/api/workdirs') {
      if (method === 'POST') return sendJson(res, 200, manager.addWorkdir((await readJson(req))?.path));
      if (method === 'DELETE') return sendJson(res, 200, manager.removeWorkdir(url.searchParams.get('path')));
    }

    // POST /api/projects { parent, name, git }: a new project folder
    if (method === 'POST' && path === '/api/projects') return sendJson(res, 201, await manager.createProject((await readJson(req)) ?? {}));

    // GET /api/directories?path=...
    if (method === 'GET' && path.startsWith('/api/directories')) {
      const dirPath = url.searchParams.get('path') ?? '';
      return sendJson(res, 200, listDirectory(manager.allowedWorkdirs, dirPath));
    }

    // GET /api/directory-tree?root=...
    if (method === 'GET' && path.startsWith('/api/directory-tree')) {
      const root = url.searchParams.get('root') ?? '';
      const maxDepth = Number(url.searchParams.get('maxDepth') ?? 3) || 3;
      return sendJson(res, 200, buildDirectoryTree(manager.allowedWorkdirs, root, maxDepth));
    }

    // GET /api/local-sessions?limit=&cursor=: this machine's agent conversations that are not
    // threads here, newest first, one page at a time. A visible page keeps the background scan
    // fresh; the rows leave the list the moment one becomes a thread (POST /api/threads/import).
    if (method === 'GET' && path === '/api/local-sessions') {
      if (!localSessions) throw new HttpError(404, 'this daemon does not list local sessions');
      localSessions.ensureFresh();
      return sendJson(res, 200, localSessions.page(url.searchParams.get('limit'), url.searchParams.get('cursor')));
    }

    // GET /api/machine-usage?hours=1|24|168&model=&launcher=: this machine's tokens by minute,
    // model and launcher, read off the agents' own transcripts.
    if (method === 'GET' && path === '/api/machine-usage') {
      if (!machineUsage) throw new HttpError(404, 'this daemon does not meter machine usage');
      const hours = Number(url.searchParams.get('hours') ?? 24);
      return sendJson(res, 200, machineUsage.report(hours, url.searchParams.get('model'), url.searchParams.get('launcher')));
    }

    // POST /api/threads/import: turn an existing agent session into a thread
    if (method === 'POST' && path === '/api/threads/import') {
      const body = await readJson(req);
      return sendJson(res, 201, await manager.importSession({ agent: body.agent, sessionId: body.sessionId }));
    }

    // GET /api/worktree-options?cwd=: whether a new thread there could start in a git worktree
    if (method === 'GET' && path === '/api/worktree-options') {
      return sendJson(res, 200, await manager.worktreeOptions(url.searchParams.get('cwd')));
    }

    // GET /api/threads, POST /api/threads
    if (path === '/api/threads') {
      if (method === 'GET') {
        return sendJson(res, 200, manager.listThreads({ archived: url.searchParams.get('archived') === '1' }));
      }
      if (method === 'POST') {
        const body = await readJson(req);
        return sendJson(
          res,
          201,
          await manager.createThread({
            agent: body.agent,
            cwd: body.cwd,
            title: body.title,
            workMode: body.workMode,
            permissionMode: body.permissionMode,
            model: body.model,
            effort: body.effort,
            speed: body.speed,
            sectionId: body.sectionId,
            worktree: body.worktree === true,
            scratch: body.scratch === true,
          }),
        );
      }
    }

    // Sections: user-made groups for the sidebar.
    if (path === '/api/sections') {
      if (method === 'GET') return sendJson(res, 200, manager.listSections());
      if (method === 'POST') return sendJson(res, 201, manager.createSection((await readJson(req)).name));
    }
    if (parts[1] === 'sections' && parts[2] && !parts[3]) {
      if (method === 'PATCH') return sendJson(res, 200, manager.renameSection(parts[2], (await readJson(req)).name));
      if (method === 'DELETE') {
        manager.deleteSection(parts[2]);
        res.writeHead(204).end();
        return;
      }
    }

    if (path === '/api/pets' && method === 'GET') return sendJson(res, 200, pets.status());
    if (path === '/api/pets/install' && method === 'POST') {
      // Answers at once; the page polls the status while the frames arrive.
      void pets.install().catch(() => undefined);
      return sendJson(res, 202, pets.status());
    }

    // Plan page: the user's own long-lived list, linked to threads.
    if (path === '/api/todos') {
      if (method === 'GET') {
        const counts = manager.personal.proactive.counts();
        const todos = manager.listTodos();
        const stages = todoStages(manager, todos);
        return sendJson(res, 200, todos.map(todo => ({ ...todo, proactiveRunCount: counts[todo.id] ?? 0, stage: stages.get(todo.id) ?? null })));
      }
      if (method === 'POST') {
        const body = await readJson(req);
        return sendJson(res, 201, manager.createTodo(body.text, body.parentId, body));
      }
    }
    if (parts[1] === 'todos' && parts[2] && !parts[3]) {
      if (method === 'PATCH') {
        const body = await readJson(req);
        return sendJson(res, 200, manager.updateTodo(parts[2], body));
      }
      if (method === 'DELETE') {
        manager.deleteTodo(parts[2], url.searchParams.has('rev') ? Number(url.searchParams.get('rev')) : undefined);
        res.writeHead(204).end();
        return;
      }
    }
    if (parts[1] === 'todos' && parts[2] && parts[3] === 'move' && method === 'POST') {
      const body = await readJson(req);
      manager.moveTodo(parts[2], body);
      res.writeHead(204).end();
      return;
    }
    if (parts[1] === 'todos' && parts[2] && parts[3] === 'threads') {
      if (method === 'POST') {
        const body = await readJson(req);
        manager.linkTodoThread(parts[2], body.threadId, body.rev);
        res.writeHead(204).end();
        return;
      }
      if (method === 'DELETE') {
        manager.unlinkTodoThread(parts[2], url.searchParams.get('threadId'), url.searchParams.has('rev') ? Number(url.searchParams.get('rev')) : undefined);
        res.writeHead(204).end();
        return;
      }
    }

    // Scheduled tasks: CRUD, manual run, run history, and a cron preview for the form.
    if (scheduler && parts[0] === 'api' && parts[1] === 'schedules') {
      if (method === 'GET' && !parts[2]) return sendJson(res, 200, scheduler.list());
      if (method === 'POST' && !parts[2]) return sendJson(res, 201, await scheduler.create(await readJson(req)));
      if (method === 'POST' && parts[2] === 'preview' && !parts[3]) {
        const body = await readJson(req);
        return sendJson(res, 200, scheduler.preview(body.cron, body.timezone));
      }
      if (method === 'GET' && parts[2] && parts[3] === 'runs' && !parts[4]) {
        const tail = Number(url.searchParams.get('tail'));
        return sendJson(res, 200, scheduler.runs(parts[2], Number.isFinite(tail) && tail > 0 ? tail : undefined));
      }
      if (method === 'POST' && parts[2] && parts[3] === 'run' && !parts[4]) return sendJson(res, 202, await scheduler.runNow(parts[2]));
      if (method === 'PATCH' && parts[2] && !parts[3]) return sendJson(res, 200, await scheduler.update(parts[2], await readJson(req)));
      if (method === 'DELETE' && parts[2] && !parts[3]) {
        scheduler.remove(parts[2]);
        res.writeHead(204).end();
        return;
      }
    }

    // GET /api/desktop: whether this machine shows a desktop (a VNC server is found on it)
    if (method === 'GET' && path === '/api/desktop') {
      const target = await desktop?.find();
      return sendJson(res, 200, target ? { available: true, port: target.port } : { available: false });
    }
    // GET /api/desktop/ticket?host=: a signed address for the desktop's WebSocket, on this machine or
    // another one. The hub signs and checks it: a machine behind it trusts its own socket.
    if (method === 'GET' && path === '/api/desktop/ticket') {
      if (!desktop) throw new HttpError(501, 'the desktop is not enabled');
      const host = url.searchParams.get('host');
      if (host && !hosts?.list().some((h) => h.id === host)) throw new HttpError(404, 'machine not found');
      const wsPath = host ? `/api/hosts/${encodeURIComponent(host)}/proxy/desktop/vnc` : '/api/desktop/vnc';
      return sendJson(res, 200, { url: `${wsPath}?${desktop.ticket(token, wsPath)}` });
    }

    // Web terminals: a real PTY each, output over SSE and keystrokes over POST.
    if (terminals) {
      if (path === '/api/terminals') {
        if (method === 'GET') return sendJson(res, 200, terminals.list());
        if (method === 'POST') {
          const body = await readJson(req);
          return sendJson(res, 201, terminals.create(body.cwd));
        }
      }
      if (parts[1] === 'terminals' && parts[2] && !parts[3]) {
        if (method === 'DELETE') {
          terminals.kill(parts[2]);
          res.writeHead(204).end();
          return;
        }
      }
      if (parts[1] === 'terminals' && parts[2] && parts[3] === 'input' && method === 'POST') {
        const body = await readJson(req);
        terminals.input(parts[2], body.data);
        res.writeHead(204).end();
        return;
      }
      if (parts[1] === 'terminals' && parts[2] && parts[3] === 'size' && method === 'POST') {
        const body = await readJson(req);
        terminals.resize(parts[2], body.cols, body.rows);
        res.writeHead(204).end();
        return;
      }
      if (method === 'GET' && parts[1] === 'terminals' && parts[2] && parts[3] === 'stream') {
        terminals.get(parts[2]); // 404 before opening the stream
        openSse(res);
        // Synchronous replay: no frame can slip in between the backlog and the subscription.
        const off = terminals.subscribe(parts[2], (ev) => writeTerminalEvent(res, ev));
        return finishStream(req, res, off);
      }
    }

    // GET /api/stream: lightweight stream of thread-list changes.
    if (method === 'GET' && path === '/api/stream') {
      openSse(res);
      const offThreads = manager.subscribe((ev) => {
        if (GLOBAL_EVENT_TYPES.has(ev.type)) writeEvent(res, ev);
      });
      // Schedule changes ride the same stream: the list page refreshes like the thread list does.
      const offSchedules = scheduler?.subscribe(() =>
        writeEvent(res, { threadId: '', seq: 0, turnId: null, type: 'schedules.changed', ts: new Date().toISOString(), data: {} }),
      );
      // So does the extension list: an install on one device shows up on the others.
      const offExtensions = extensions?.onChange(() =>
        writeEvent(res, { threadId: '', seq: 0, turnId: null, type: 'extensions.changed', ts: new Date().toISOString(), data: {} }),
      );
      return finishStream(req, res, () => {
        offThreads();
        offSchedules?.();
        offExtensions?.();
      });
    }

    if (parts[0] === 'api' && parts[1] === 'teams') {
      const teams = manager.coordination?.definitions;
      if (!teams) throw new HttpError(501, '团队服务未启用');
      const id = parts[2], sub = parts[3];
      if (!id && method === 'GET') return sendJson(res, 200, teams.list().map(({ id, name, description, entry, members }) => {
        const recipient = members.find(m => m.name === entry)!;
        return { id, name, description, entry, memberCount: members.length, entryAgent: recipient.agent, entryModel: recipient.model ?? null };
      }));
      if (!id && method === 'POST') return sendJson(res, 201, teams.save(await readJson(req)));
      if (id === 'template' && method === 'GET') return sendJson(res, 200, await teams.template());
      if (id === 'import' && method === 'POST') return sendJson(res, 201, teams.import(await readJson(req)));
      if (id && !sub && method === 'GET') return sendJson(res, 200, teams.get(id));
      if (id && !sub && method === 'PUT') return sendJson(res, 200, teams.save(await readJson(req), id));
      if (id && !sub && method === 'DELETE') { teams.remove(id); res.writeHead(204).end(); return; }
      if (id && sub === 'export' && method === 'GET') return sendJson(res, 200, teams.export(id));
      if (id && sub === 'tasks' && method === 'POST') {
        const body = await readJson(req, MAX_TURN_BODY);
        return sendJson(res, 202, await teams.dispatch({ teamId: id, text: body.text, images: body.images, cwd: body.cwd, source: { kind: body.source?.kind === 'web' ? 'web' : body.source?.kind === 'cli' ? 'cli' : 'http' } }));
      }
      throw new HttpError(404, 'not found');
    }

    // /api/threads/:id ...
    if (parts[0] === 'api' && parts[1] === 'threads' && parts[2]) {
      const threadId = parts[2];
      const sub = parts[3];

      if (sub === 'preview-notes') {
        if (!parts[4] && method === 'GET') return sendJson(res, 200, manager.previewNotes.list(threadId, url.searchParams.get('page')));
        if (!parts[4] && method === 'POST') return sendJson(res, 201, manager.previewNotes.create(threadId, await readJson(req, 32768)));
        if (parts[4] === 'send' && !parts[5] && method === 'POST') return sendJson(res, 202, await manager.previewNotes.send(threadId, await readJson(req, 8192)));
        if (parts[4] && !parts[5] && method === 'GET') return sendJson(res, 200, manager.previewNotes.read(threadId, parts[4]));
        if (parts[4] && !parts[5] && method === 'DELETE') { manager.previewNotes.remove(threadId, parts[4]); res.writeHead(204).end(); return; }
        throw new HttpError(404, 'not found');
      }

      // GET /api/threads/:id/coordination/result/:deliveryId: the hub's copy of a remote member's
      // result file. It is text the member wrote, so it downloads instead of rendering as a page.
      if (method === 'GET' && sub === 'coordination' && parts[4] === 'result' && parts[5]) {
        manager.threadView(threadId);
        const collaboration = manager.coordination;
        if (!collaboration) throw new HttpError(501, '此 daemon 尚未启用协作');
        const file = collaboration.resultFile(threadId, parts[5]);
        res.writeHead(200, {
          'Content-Type': 'text/markdown; charset=utf-8',
          'Content-Length': file.size,
          'Content-Disposition': `attachment; filename="${file.name.replace(/[^\x20-\x7e]/g, '_')}"`,
          'Cache-Control': 'no-store',
          'X-Content-Type-Options': 'nosniff',
        });
        createReadStream(file.path).pipe(res);
        return;
      }

      if (sub === 'coordination') {
        manager.threadView(threadId);
        const collaboration = manager.coordination;
        if (!collaboration) throw new HttpError(501, '此 daemon 尚未启用协作');
        if (method === 'GET') return sendJson(res, 200, collaboration.view(threadId));
        if (method === 'POST') {
          const body = await readJson(req);
          if (body.action === 'confirm') return sendJson(res, 200, await collaboration.confirm(threadId));
          if (body.action === 'resume') return sendJson(res, 200, await collaboration.resume(threadId));
          if (body.action === 'permission') return sendJson(res, 200, await collaboration.setPermission(threadId, body.permission));
          if (body.action === 'defaults') return sendJson(res, 200, await collaboration.setDefaults(threadId, body.defaults));
          if (body.action === 'stop') { await manager.cancelTurn(threadId); return sendJson(res, 200, collaboration.view(threadId)); }
          return sendJson(res, 200, await collaboration.enable(threadId, body));
        }
      }
      if (method === 'GET' && !sub) return sendJson(res, 200, manager.threadView(threadId));

      if (method === 'DELETE' && !sub) {
        await manager.deleteThread(threadId, {
          agentSession: url.searchParams.get('agentSession') === '1',
          worktree: url.searchParams.get('worktree') === '1',
          force: url.searchParams.get('force') === '1',
        });
        res.writeHead(204).end();
        return;
      }

      if (method === 'PATCH' && !sub) {
        const body = await readJson(req);
        return sendJson(
          res,
          200,
          await manager.updateThread(threadId, {
            rememberModelChoice: body.rememberModelChoice,
            title: body.title,
            pinned: body.pinned,
            workMode: body.workMode,
            permissionMode: body.permissionMode,
            model: body.model,
            effort: body.effort,
            speed: body.speed,
            archived: body.archived,
            sectionId: body.sectionId,
            unread: body.unread,
            done: body.done,
            stage: body.stage,
          }),
        );
      }

      if (method === 'GET' && sub === 'history') {
        const history = manager.history(threadId);
        const positive = (value: string | null, optional = false): number | undefined => {
          if (value === null && optional) return undefined;
          const n = Number(value);
          if (!Number.isSafeInteger(n) || n < 1) throw new HttpError(400, 'invalid history cursor');
          return n;
        };
        if (!parts[4] && url.searchParams.get('head') === '1') return sendJson(res, 200, { snapshotSeq: history.latest(threadId) });
        if (!parts[4]) return sendJson(res, 200, await history.page(threadId, positive(url.searchParams.get('before'), true), positive(url.searchParams.get('after'), true), url.searchParams.get('chain') === '1', url.searchParams.get('forward') === '1'));
        if (parts[4] === 'handover') return sendJson(res, 200, history.handoverInfo(threadId, url.searchParams.get('text') === '1'));
        if (parts[4] === 'voice') return sendJson(res, 200, history.voiceText(threadId, url.searchParams.get('session') ?? '', positive(url.searchParams.get('cursor'), true)));
        const turn = url.searchParams.get('turn');
        if (!turn) throw new HttpError(400, 'turn is required');
        if (parts[4] === 'summary') return sendJson(res, 200, history.summaryText(threadId, turn, positive(url.searchParams.get('cursor'), true), positive(url.searchParams.get('version'), true)));
        const id = positive(url.searchParams.get('id'))!;
        const cursor = positive(url.searchParams.get('cursor'), true);
        const version = positive(url.searchParams.get('version'), true);
        if (cursor !== undefined && version === undefined) throw new HttpError(400, 'version is required to continue');
        if (parts[4] === 'work') return sendJson(res, 200, await history.work(threadId, turn, id, cursor, version, url.searchParams.get('chain') === '1'));
        if (parts[4] === 'detail') {
          const detail = await history.detail(threadId, turn, id, cursor, version, url.searchParams.get('path') ?? undefined) as Record<string, unknown>;
          detail.fullAvailable = manager.canReadToolOutput(threadId);
          return sendJson(res, 200, detail);
        }
        if (parts[4] === 'tool-output') return sendJson(res, 200, await manager.threadToolOutput(threadId, turn, id));
        throw new HttpError(404, 'history resource not found');
      }

      if (method === 'GET' && sub === 'events') {
        // ?tail: the newest turns, what a reopened page renders first. ?before=S: the turns just
        // before what the page has. Both begin at a turn's start and merge streamed fragments.
        // ?all: the whole log, fragments merged, for copying a conversation out.
        // Plain ?after=S is the log as stored, one page at a time.
        if (url.searchParams.has('all')) return sendJson(res, 200, manager.listEventsSince(threadId, 0));
        if (url.searchParams.has('tail')) return sendJson(res, 200, manager.listEventsPage(threadId));
        const before = Number(url.searchParams.get('before') ?? 0) || 0;
        if (before > 0) return sendJson(res, 200, manager.listEventsPage(threadId, before));
        const after = Number(url.searchParams.get('after') ?? 0) || 0;
        return sendJson(res, 200, manager.listEvents(threadId, after));
      }

      if (method === 'GET' && sub === 'trace') {
        // ?turn=ID: that turn; without it, the thread's last one.
        return sendJson(res, 200, manager.turnTrace(threadId, url.searchParams.get('turn') || undefined));
      }

      if (method === 'GET' && sub === 'stats') {
        // Summary numbers for the top-bar readings: per-turn timing from storage, tokens from the
        // native samples, the running turn's progress computed fresh.
        return sendJson(res, 200, manager.threadStats(threadId));
      }

      if (method === 'GET' && sub === 'outputs') {
        // The overview beside the conversation: names and counts only, details are read when opened.
        return sendJson(res, 200, await manager.threadOutputs(threadId));
      }

      if (method === 'GET' && sub === 'system-prompt') {
        return sendJson(res, 200, await manager.threadSystemPrompt(threadId));
      }

      if (method === 'GET' && sub === 'stream') {
        const after = Number(url.searchParams.get('after') ?? 0) || 0;
        manager.getThread(threadId); // 404 before opening the stream
        if (['summary', 'history'].includes(url.searchParams.get('view') ?? '')) {
          const history = manager.history(threadId);
          let buffer: HistoryEvent[] | null = [];
          // Catch up with summaries, then deliver complete live events without waiting for clicks.
          const off = manager.subscribe(ev => {
            if (ev.threadId !== threadId) return;
            if (buffer) buffer.push(ev);
            else writeEvent(res, url.searchParams.get('chain') === '1' ? history.handoverEvent(history.summaryEvent(ev)) : history.summaryEvent(ev));
          });
          res.once('close', off);
          try {
            const backlog = await history.since(threadId, after, url.searchParams.get('chain') === '1');
            if (res.destroyed) return off();
            openSse(res);
            writeEvent(res, { threadId, turnId: null, type: 'history.reset', seq: backlog.snapshotSeq, ts: new Date().toISOString(), data: backlog });
            for (const ev of buffer) if (ev.seq > backlog.snapshotSeq) writeEvent(res, url.searchParams.get('chain') === '1' ? history.handoverEvent(history.summaryEvent(ev)) : history.summaryEvent(ev));
            buffer = null;
            return finishStream(req, res, off);
          } catch (err) { off(); throw err; }
        }
        openSse(res);
        // Synchronous: no event can slip in between the backlog and the subscription.
        for (const ev of manager.listEventsSince(threadId, after)) writeEvent(res, ev);
        const off = manager.subscribe((ev) => {
          if (ev.threadId === threadId) writeEvent(res, ev);
        });
        return finishStream(req, res, off);
      }

      if (sub === 'goal' && method === 'GET') {
        manager.getThread(threadId);
        return sendJson(res, 200, { goal: await manager.goalView(threadId) });
      }

      if (sub === 'auto-handover') {
        manager.getThread(threadId);
        if (method === 'GET') return sendJson(res, 200, { ...manager.autoHandover.view(threadId), resume: manager.quotaResume.view(threadId), targets: await manager.autoHandover.targets(threadId) });
        if (method === 'PATCH') {
          const body = await readJson(req);
          if (body.resume !== undefined) return sendJson(res, 200, await manager.setResume(threadId, body.resume));
          return sendJson(res, 200, manager.autoHandover.setOverride(threadId, body.enabled));
        }
        if (method === 'DELETE') { manager.autoHandover.cancel(threadId); return sendJson(res, 200, manager.autoHandover.view(threadId)); }
      }
      if (sub === 'collaboration-handover' && method === 'PATCH') {
        return sendJson(res, 200, manager.coordination?.setAutoHandover(threadId, (await readJson(req)).enabled));
      }

      if (method === 'POST' && sub === 'handover-thread') {
        const body = await readJson(req);
        return sendJson(res, 201, await manager.createHandoverThread(threadId, body.agent, body.model, body.mode));
      }
      // POST /api/threads/:id/autotitle: the person's 「重新取名」, through the 自动取名 extension.
      if (method === 'POST' && sub === 'autotitle') {
        if (!autotitle) throw new HttpError(501, '此 daemon 没有自动取名扩展');
        return sendJson(res, 200, await autotitle.name(threadId, { apply: true }));
      }
      // GET /api/threads/:id/brief: the facts behind a thread that wants the person (what waits for approval,
      // whether a failed turn changed files, how a finished one ended), for the companion to word.
      if (method === 'GET' && sub === 'brief') return sendJson(res, 200, manager.threadBrief(threadId));
      // POST /api/threads/:id/retry: send the failed ask again, the person's 「接着做」.
      if (method === 'POST' && sub === 'retry') return sendJson(res, 200, await manager.retryFailedTurn(threadId));
      if (method === 'POST' && sub === 'takeover') {
        const body = await readJson(req);
        return sendJson(res, 201, await manager.takeOver(threadId, body.agent, body.model, body.note));
      }
      if (method === 'DELETE' && sub === 'handover-link') {
        return sendJson(res, 200, manager.detachAutomaticHandover(threadId));
      }

      if (method === 'POST' && sub === 'handover') {
        // Asks the thread's agent to write a summary for another agent; the GET reads it back.
        const body = await readJson(req);
        return sendJson(res, 202, await manager.startHandover(threadId, String(body.target ?? '')));
      }
      if (method === 'GET' && sub === 'handover') {
        return sendJson(
          res,
          200,
          // The clean brief of the auto handover: where to look with `loochat`, a few lines of progress,
          // and the interrupted ask when the thread ends on a failure.
          await manager.handoverSummary(threadId, url.searchParams.get('turn'), { carryFailed: true }),
        );
      }

      // POST /api/threads/:id/turns/:turnId/edit: edit an earlier message and send it again from there.
      if (method === 'POST' && sub === 'turns' && parts[4] && parts[5] === 'edit' && !parts[6]) {
        const body = await readJson(req, MAX_TURN_BODY);
        return sendJson(res, 202, await manager.editTurn(threadId, parts[4], {
          text: body.text, images: body.images, videos: body.videos, revision: body.revision, requestId: body.requestId,
        }));
      }

      if (method === 'POST' && sub === 'turns') {
        const body = await readJson(req, MAX_TURN_BODY);
        if (manager.coordination?.metadata(threadId)?.team) {
          const { images } = teamMessage(body);
          const thread = manager.threadView(threadId);
          await manager.checkImages(thread.agent, thread.model, images);
        }
        await manager.coordination?.previewMentions(threadId, typeof body.text === 'string' ? body.text : '', body.mentions, body.collaboration);
        await manager.coordination?.userSpoke(threadId);
        return sendJson(res, 202, await manager.startTurn(threadId, { text: body.text, images: body.images, videos: body.videos, queue: body.queue, mentions: body.mentions, collaboration: body.collaboration, workMode: body.workMode, fromUser: true }));
      }

      if (method === 'POST' && sub === 'videos' && !parts[4]) {
        if (Number(req.headers['content-length']) > MAX_VIDEO_BYTES) throw new HttpError(413, '视频不能超过 100 MB');
        let name: string;
        try { name = decodeURIComponent(String(req.headers['x-file-name'] ?? '')); } catch { throw new HttpError(400, 'invalid file name'); }
        return sendJson(res, 201, await manager.saveVideo(threadId, req, name));
      }

      if (method === 'POST' && sub === 'files' && !parts[4]) {
        if (Number(req.headers['content-length']) > MAX_FILE_BYTES) throw new HttpError(413, '文件不能超过 100 MB');
        let name: string;
        try { name = decodeURIComponent(String(req.headers['x-file-name'] ?? '')); } catch { throw new HttpError(400, 'invalid file name'); }
        return sendJson(res, 201, await manager.saveFile(threadId, req, name));
      }

      // DELETE /api/threads/:id/queue/:queuedId: take back a message that is waiting; it comes back
      // whole (text and images), for putting it in the box to edit
      if (method === 'DELETE' && sub === 'queue' && parts[4] && !parts[5]) {
        return sendJson(res, 200, manager.removeQueued(threadId, parts[4]));
      }

      // POST /api/threads/:id/queue/:queuedId/now: interrupt the running turn and send this one next
      if (method === 'POST' && sub === 'queue' && parts[4] && parts[5] === 'now') {
        await manager.sendQueuedNow(threadId, parts[4]);
        res.writeHead(204).end();
        return;
      }

      // GET /api/threads/:id/files?path=: an image the agent made or mentioned. (Without a
      // sub-path: /files/<path> below is the signed preview route of the same name.)
      if (method === 'GET' && sub === 'files' && !parts[4]) {
        const file = manager.getThreadImage(threadId, url.searchParams.get('path'));
        res.writeHead(200, {
          'Content-Type': file.mimeType,
          'Content-Length': file.size,
          // Unlike attachments, these are the agent's working files and can change.
          'Cache-Control': 'private, max-age=30',
          'X-Content-Type-Options': 'nosniff',
          // An SVG is a document that can carry scripts. Opened on its own it runs none of them
          // and reaches nothing of the hub's.
          'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        });
        createReadStream(file.path).pipe(res);
        return;
      }

      // GET /api/threads/:id/download?path=: any file the thread may read, for saving or playing it.
      // Range is served (206) so a video can seek and a phone can play it; `?dl=1` forces a save,
      // otherwise a type the browser knows (video, PDF) opens inline.
      if (method === 'GET' && sub === 'download') {
        const path = url.searchParams.get('path');
        // Without a Bearer token (a <video> tag, a save link) the request must carry its own grant.
        if (!tokenMatches(bearer, token)) {
          checkDownloadGrant(token, threadId, typeof path === 'string' ? path : '', url.searchParams.get('exp'), url.searchParams.get('sig'));
        }
        const file = manager.getThreadDownload(threadId, path);
        serveDownload(req, res, file, url.searchParams.get('dl') === '1');
        return;
      }

      // GET /api/threads/:id/download-link?path=&dl=: a signed address for the download route, for
      // a <video> or a save link, which cannot send the token as a header.
      if (method === 'GET' && sub === 'download-link') {
        const path = url.searchParams.get('path');
        // Resolved once here, so the link is only ever made for a file the thread may read.
        const file = manager.getThreadDownload(threadId, path);
        const grant = makeDownloadGrant(token, threadId, file.path);
        const query = new URLSearchParams({ path: file.path, exp: String(grant.exp), sig: grant.sig });
        if (url.searchParams.get('dl') === '1') query.set('dl', '1');
        return sendJson(res, 200, { url: `/api/threads/${threadId}/download?${query}`, name: file.name, size: file.size, mimeType: file.mimeType });
      }

      // Git is scoped to the authenticated thread's current checkout. Heavy details are separate reads.
      if (sub === 'git' && !parts[5]) {
        const action = parts[4];
        if (method === 'GET' && action === 'status') return sendJson(res, 200, await manager.git.status(threadId));
        if (method === 'GET' && action === 'log') return sendJson(res, 200, await manager.git.log(threadId, url.searchParams.get('head'), Number(url.searchParams.get('skip') ?? 0)));
        if (method === 'GET' && action === 'commit') return sendJson(res, 200, await manager.git.commitFiles(threadId, url.searchParams.get('commit') ?? ''));
        if (method === 'GET' && action === 'diff') return sendJson(res, 200, await manager.git.diff(threadId, url.searchParams.get('path'), url.searchParams.get('staged') === '1', url.searchParams.get('commit')));
        if (method === 'POST' && (action === 'fetch' || action === 'pull')) return sendJson(res, 200, await manager.git.operate(threadId, action));
      }

      // GET /api/threads/:id/roots: the folders the file browser can stand on, for the root switcher
      if (method === 'GET' && sub === 'roots') {
        return sendJson(res, 200, manager.threadRoots(threadId));
      }

      // GET /api/threads/:id/tree?path=: one folder of the thread's directory, for the file browser
      if (method === 'GET' && sub === 'tree') {
        return sendJson(res, 200, await manager.listThreadDir(threadId, url.searchParams.get('path')));
      }

      // GET /api/threads/:id/find?q=&path=: files under a browse root whose path matches, for the browser's filter
      if (method === 'GET' && sub === 'find') {
        const under = url.searchParams.get('path');
        return sendJson(res, 200, await manager.findThreadFiles(threadId, url.searchParams.get('q') ?? '', under === null ? undefined : under));
      }

      // GET /api/threads/:id/text?path=: a text file's contents, for the file viewer (read only)
      if (method === 'GET' && sub === 'text') {
        return sendJson(res, 200, manager.readThreadText(threadId, url.searchParams.get('path')));
      }

      // PUT /api/threads/:id/writing {stage}: start writing mode or change its stage; DELETE ends it
      if (sub === 'writing' && !parts[4]) {
        if (method === 'PUT') return sendJson(res, 200, manager.setWriting(threadId, (await readJson(req)).stage));
        if (method === 'DELETE') return sendJson(res, 200, manager.endWriting(threadId));
      }

      // PUT /api/threads/:id/writing/text?path= {text, baseHash}: the person's own edit of an article
      if (method === 'PUT' && sub === 'writing' && parts[4] === 'text' && !parts[5]) {
        const body = await readJson(req, 4 * MAX_BODY);
        return sendJson(res, 200, manager.saveWritingText(threadId, url.searchParams.get('path'), body.text, body.baseHash));
      }

      // GET /api/threads/:id/preview-link?path=: where the preview of this file lives, plus the
      // short-lived cookie that opens it. The iframe loads from the preview port and the cookie
      // rides along on every request the page makes under its own path, signature or not.
      if (method === 'GET' && sub === 'preview-link') {
        const { rel } = manager.threadFile(threadId, url.searchParams.get('path'));
        if (!deps.previewPort) throw new HttpError(503, 'the preview port is not available on this daemon');
        const host = (req.headers.host ?? '').replace(/:\d+$/, '');
        if (!host) throw new HttpError(400, 'the request names no host');
        const grant = makeGrant(token, threadId);
        res.setHeader(
          'Set-Cookie',
          `${PREVIEW_COOKIE}=${grant.value}; Path=/api/threads/${threadId}/files; Max-Age=${grant.maxAge}; HttpOnly; SameSite=Lax`,
        );
        const file = rel.split('/').map(encodeURIComponent).join('/');
        return sendJson(res, 200, { url: `http://${host}:${deps.previewPort}/api/threads/${threadId}/files/${file}` });
      }

      // GET /api/threads/:id/files/<path>: the file itself, on the preview port. The cookie from
      // preview-link is the auth here — subresources of a page carry no query string, but a
      // cookie scoped to this thread's path comes along on its own.
      if (method === 'GET' && sub === 'files' && parts[4]) {
        const rel = parts
          .slice(4)
          .map((p) => {
            try {
              return decodeURIComponent(p);
            } catch {
              return p;
            }
          })
          .join('/');
        checkGrant(token, threadId, cookieValue(req.headers.cookie, PREVIEW_COOKIE) ?? cookieValue(req.headers.cookie, LEGACY_PREVIEW_COOKIE));
        const { real } = manager.threadFile(threadId, rel);
        const file = readPreviewFile(real);
        let body = file.body;
        const contentType = file.contentType;
        if (contentType.startsWith('text/html')) {
          const injection = '<script src="/api/preview-bridge.js"></script>';
          const html = body.toString('utf8');
          body = Buffer.from(html.includes('</body>') ? html.replace('</body>', injection + '</body>') : html + injection);
        }
        res.writeHead(200, {
          'Content-Type': contentType,
          'X-Content-Type-Options': 'nosniff',
          // Previews are pages an agent may rewrite any minute: what is shown is what is on disk.
          'Cache-Control': 'no-store',
          'Content-Length': body.length,
        });
        res.end(body);
        return;
      }

      // GET /api/threads/:id/attachments/:attachmentId
      if (method === 'GET' && sub === 'attachments' && parts[4]) {
        const file = manager.getAttachment(threadId, parts[4]);
        res.writeHead(200, {
          'Content-Type': file.mimeType,
          'Content-Length': file.size,
          // Attachments never change once stored. "private" keeps them out of shared caches.
          'Cache-Control': 'private, max-age=31536000, immutable',
          'X-Content-Type-Options': 'nosniff',
        });
        createReadStream(file.path).pipe(res);
        return;
      }

      if (method === 'POST' && sub === 'sync') {
        return sendJson(res, 200, await manager.syncThread(threadId));
      }

      if (method === 'GET' && sub === 'fork-options') return sendJson(res, 200, await manager.forkOptions(threadId));

      if (method === 'POST' && sub === 'fork') {
        // An empty body copies the whole thread; `turnId` names the turn the copy ends with.
        const body = await readJson(req);
        return sendJson(
          res,
          201,
          await manager.forkThread(threadId, {
            ...(typeof body?.turnId === 'string' ? { turnId: body.turnId } : {}),
            worktree: body?.worktree === true,
          }),
        );
      }

      if (method === 'POST' && sub === 'cancel') {
        return sendJson(res, 200, await manager.cancelTurn(threadId));
      }
    }

    if (method === 'POST' && parts[1] === 'inputs' && parts[2] && parts.length === 3) {
      if (!manager.inputs.answer(parts[2], await readJson(req))) throw new HttpError(404, '问题已结束或已经回答');
      res.writeHead(204).end();
      return;
    }

    // POST /api/approvals/:id
    if (method === 'POST' && parts[1] === 'approvals' && parts[2]) {
      const body = await readJson(req);
      const decision: ApprovalDecision = body.cancel ? { cancelled: true } : { optionId: String(body.optionId ?? '') };
      if (!('cancelled' in decision) && !decision.optionId) throw new HttpError(400, 'optionId is required');
      if (!manager.resolveApproval(parts[2], decision)) throw new HttpError(404, 'approval not found or already resolved');
      res.writeHead(204).end();
      return;
    }

    sendJson(res, 404, { error: 'not found' });
  };

  const server = createServer((req, res) => {
    handle(req, res).catch((err) => sendError(res, err));
  });
  // Two kinds of upgrade: machines connecting out to this hub, and the desktop's VNC stream, here
  // or on another machine through the hub.
  if (desktop || (hosts && !trusted)) {
    server.on('upgrade', (req: IncomingMessage, socket, head: Buffer) => {
      socket.on('error', () => socket.destroy());
      if (hosts && !trusted && hosts.relay.upgrade(req, socket, head)) return;
      if (!desktop) return refuse(socket, 404, 'not found');
      const url = new URL(req.url ?? '/', 'http://localhost');
      const remote = /^\/api\/hosts\/([^/]+)\/proxy\/desktop\/vnc$/.exec(url.pathname);
      if (url.pathname !== '/api/desktop/vnc' && !remote) return refuse(socket, 404, 'not found');
      if (!trusted && !desktop.checkTicket(token, url.pathname, url.searchParams.get('exp'), url.searchParams.get('n'), url.searchParams.get('sig'))) {
        return refuse(socket, 403, 'this desktop link has expired; open the desktop again');
      }
      if (remote) {
        if (!hosts?.upgrade(decodeURIComponent(remote[1]), '/api/desktop/vnc', req, socket, head)) refuse(socket, 502, 'this machine is not connected');
        return;
      }
      desktop.accept(req, socket, head).catch(() => socket.destroy());
    });
  }
  return server;
}

/**
 * List directory contents, with validation against allowed workdirs.
 * Returns { path, files: [{name, type, size?, modified?}], parent? }
 */
function listDirectory(allowedWorkdirs: string[], dirPath: string) {
  const path = resolveAndValidate(allowedWorkdirs, dirPath);
  if (!existsSync(path) || !statSync(path).isDirectory()) {
    throw new HttpError(404, 'directory not found');
  }

  const entries = readdirSync(path, { withFileTypes: true });
  const files = entries.map((e) => {
    const stat = statSync(join(path, e.name));
    return {
      name: e.name,
      type: e.isDirectory() ? ('dir' as const) : ('file' as const),
      size: stat.size,
      modified: stat.mtime.toISOString(),
    };
  });

  const parent = path === normalize(allowedWorkdirs[0]) ? null : normalize(join(path, '..'));
  return { path, files, parent };
}

/**
 * Recursively build directory tree structure.
 * Returns { name, path, type: 'dir', children?: [] }
 */
function buildDirectoryTree(
  allowedWorkdirs: string[],
  rootPath: string,
  maxDepth: number,
  currentDepth = 0,
): any {
  const path = resolveAndValidate(allowedWorkdirs, rootPath);
  const stat = statSync(path);
  const name = path.split(sep).pop() || path;

  if (!stat.isDirectory()) {
    throw new HttpError(400, 'root must be a directory');
  }

  const node: any = { name, path, type: 'dir' };
  if (currentDepth >= maxDepth) return node;

  try {
    const entries = readdirSync(path, { withFileTypes: true });
    node.children = entries
      // Directories only, without hidden ones and dependency folders: nobody starts a thread in those,
      // and they can hold thousands of entries.
      .filter((e) => e.isDirectory() && !e.name.startsWith('.') && e.name !== 'node_modules')
      .map((e) => buildDirectoryTree(allowedWorkdirs, join(path, e.name), maxDepth, currentDepth + 1))
      .sort((a, b) => a.name.localeCompare(b.name));
    if (node.children.length === 0) delete node.children;
  } catch {
    // Permission denied or other errors: skip children
  }

  return node;
}

/**
 * Resolve a path and validate it's within allowed workdirs.
 * Prevents directory traversal attacks.
 */
function resolveAndValidate(allowedWorkdirs: string[], inputPath: string): string {
  const path = resolve(inputPath);
  if (!isWithinWorkdirs(path, allowedWorkdirs)) throw new HttpError(403, 'path is not in allowed workdirs');
  return path;
}

/**
 * Whether a live listener answers on this unix socket. That is how a running hub is told apart
 * from a leftover socket file, and how a second daemon is kept from starting beside the first.
 */
export function socketHeld(path: string): Promise<boolean> {
  if (!existsSync(path)) return Promise.resolve(false);
  return new Promise<boolean>((done) => {
    const probe = connect(path);
    probe.on('connect', () => {
      probe.destroy();
      done(true);
    });
    probe.on('error', () => done(false));
  });
}

/**
 * Listens on a unix socket that only this user can open, for the server whose requests need no
 * token. A socket another live daemon holds is left to it, and false is returned; one nobody
 * answers on is a leftover and is replaced.
 */
export async function listenPrivate(server: Server, path: string): Promise<boolean> {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  if (existsSync(path)) {
    const held = await socketHeld(path);
    if (held) return false;
    rmSync(path, { force: true });
  }
  // bind() happens inside listen(), so the mask is narrowed for that call alone: the socket is
  // never there with wider permissions, and nothing started meanwhile inherits the mask.
  const umask = process.umask(0o077);
  let listening: Promise<void>;
  try {
    listening = new Promise<void>((done, fail) => {
      server.once('error', fail);
      server.listen(path, () => {
        server.off('error', fail);
        done();
      });
    });
  } finally {
    process.umask(umask);
  }
  await listening;
  server.on('close', () => rmSync(path, { force: true }));
  return true;
}

function finishStream(req: IncomingMessage, res: ServerResponse, off: () => void): void {
  const ping = setInterval(() => res.write(': ping\n\n'), 15_000);
  const close = () => {
    clearInterval(ping);
    off();
    res.end();
  };
  req.on('close', close);
}
