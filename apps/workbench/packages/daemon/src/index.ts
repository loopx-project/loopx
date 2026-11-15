import { personalServer } from './personal.js';
import { PushService } from './push.js';
import { FeishuChat } from './feishu-chat.js';
import { CoordinationManager, coordinationServer } from './coordination.js';
import { adoptLegacyEnv, loadConfig, loadDotEnv } from './config.js';
import { ExtensionHost } from './extension-host.js';
import { loadCatalog } from './extensions.js';
import { AUTOTITLE_ID, AutoTitle } from './autotitle.js';
import { loadSupportedExternalCatalog } from './external-extensions.js';
import { Desktop, parseDesktopSetting } from './desktop.js';
import { createHttpServer, listenPrivate, socketHeld } from './http.js';
import { HostRegistry } from './hosts.js';
import { RelayClient } from './relay-client.js';
import { AgentRegistry, defaultInstances } from './agents.js';
import { AttachmentStore } from './attachments.js';
import { LocalSessions } from './local-sessions.js';
import { MachineUsageMeter } from './machine-usage.js';
import { claudeConfigDirs, codexHomes, transcriptHome } from './adapters/transcript-usage.js';
import { sessionStoreKey } from './adapters/types.js';
import { Scheduler } from './scheduler.js';
import { Store } from './store.js';
import { TerminalManager } from './terminals.js';
import { ThreadManager } from './threads.js';
import { Workdirs } from './workdirs.js';
import { BorrowingService } from './borrowing.js';
import { borrowingServer } from './borrowing-server.js';
import { VoiceService } from './voice-service.js';
import { displayForDesktop, PersonalBrowser } from './personal-browser.js';
import { WorkerReports } from './worker-reports.js';
import { LarkEvents } from './channels/lark-events.js';
import { rmSync } from 'node:fs';
import { join } from 'node:path';

export const VERSION = '0.1.0';

async function main(): Promise<void> {
  adoptLegacyEnv();
  loadDotEnv();
  adoptLegacyEnv();
  const config = loadConfig();

  // Empty CALLI_WORKDIRS means "only the directory the daemon was started in".
  const workdirs = config.workdirs.length > 0 ? config.workdirs : [process.cwd()];

  // Agent instances come from agents.json (or the built-in defaults): one adapter each, so the
  // same protocol can back several accounts or CLIs.
  const registry = AgentRegistry.fromEnv({
    dataDir: config.dataDir,
    env: process.env,
    defaults: defaultInstances({
      claudeCommand: config.claudeCommand,
      piCommand: config.piCommand,
      agyCommand: config.agyCommand,
      traexCommand: config.traexCommand,
      codexCommand: config.codexCommand,
      dshCommand: config.dshCommand,
      doubaoCommand: config.doubaoCommand,
    }),
  });
  const adapters = registry.adapters;
  // Copies of known CLIs and signed-in accounts found on this machine join the list on their own
  // (every 10 minutes); what the person removed stays out. An inline CALLI_AGENTS_JSON hub (tests,
  // scripts) owns its list outright and joins nothing, and CALLI_AUTO_JOIN=off turns it off for
  // anyone. The page's 「发现/添加」 entries keep working alongside. dispose(), called on shutdown,
  // stops the timer.
  registry.startAutoJoin();

  // Model lists take a few seconds to probe, so start now instead of on the first page load.
  for (const a of adapters.values()) {
    void a
      .available()
      .then((ok) => (ok ? a.models() : []))
      .catch((err) => console.error(`[${a.id}] could not read the model list: ${err instanceof Error ? err.message : err}`));
  }

  // A live hub is marked by its collaboration socket, the first thing it binds. Probing before
  // anything is written keeps a second daemon from starting beside the first: recovery would end
  // turns the first one is still streaming, and the log would hold events after their turn's
  // ending. Seen once when two daemons overlapped; the view then showed a working box that never
  // stopped counting.
  const coordinationPath = join(config.dataDir, 'coordination', 'calli.sock');
  if (await socketHeld(coordinationPath)) throw new Error('another daemon is already serving this data directory; not starting a second one');

  const store = new Store(config.dataDir);
  // The two subsystems that read what the agents on this machine have been doing: the sessions
  // they already have (listed for the thread list, imported on a click) and the tokens their
  // transcripts gained (the machine's usage). Neither scans on a request's say-so alone, and
  // neither runs on a hub that owns no agent file of its own (an inline CALLI_AGENTS_JSON list is
  // a test or script machine, not the person's) or with CALLI_LOCAL_SCAN=off: the machine behind
  // a throwaway hub is not ours to read.
  const localScan = registry.ownsFile && process.env.CALLI_LOCAL_SCAN !== 'off';
  const localSessions = new LocalSessions({
    adapters,
    workdirs: () => manager.allowedWorkdirs,
    importedSessions: () => store.agentSessionOwners(),
    instances: () => registry.list().map((instance) => ({ id: instance.id, hidden: !!instance.hidden, store: sessionStoreKey(instance) })),
  }, { enabled: localScan });
  const machineUsage = localScan ? new MachineUsageMeter({
    db: store.db,
    sources: () => {
      const instances = registry.list();
      return {
        claudeDirs: claudeConfigDirs(transcriptHome(), instances.filter((i) => i.adapter === 'claude').map((i) => i.env ?? {})),
        codexHomes: codexHomes(instances.filter((i) => i.adapter === 'codex' || i.adapter === 'traex').map((i) => i.env ?? {})),
      };
    },
    launchers: () => store.agentSessionOwners(),
  }) : null;
  // Directories added from the page are kept next to the hub's data; threads in no project work in
  // a scratch area there too.
  const dirs = new Workdirs(workdirs, join(config.dataDir, 'workdirs.json'), join(config.dataDir, 'scratch'));
  const manager = new ThreadManager(store, adapters, dirs, new AttachmentStore(config.dataDir), () => registry.list());
  // The session list scans once here (it asks the manager for the allowed directories) and then
  // only when a request finds it stale; the meter keeps its own timer, files only. Stopped in
  // shutdown with everything else.
  localSessions.start();
  machineUsage?.start();
  manager.recoverInterrupted();
  manager.autoHandover.seedDefault();
  manager.personal.proactive.recover();
  // Started after recovery: turns a restart cut off are not news from a Worker.
  const workerReports = new WorkerReports(manager);
  workerReports.start();
  const push = new PushService(store, manager);
  const feishu = new FeishuChat(store, manager);
  void feishu.configure();
  const personalPath = join(config.dataDir, 'personal', 'calli.sock');
  manager.personal.socket = personalPath;
  const personal = personalServer(manager.personal);
  if (!await listenPrivate(personal, personalPath)) throw new Error('Personal socket is already in use');
  // The personal directory mirrors the plan page's todos as files; reconcile once at startup.
  manager.personal.syncMirror();
  // Scheduled tasks tick on their own, on the machine where their threads live.
  const scheduler = new Scheduler(store, manager, adapters, push);
  const coordination = new CoordinationManager(store, manager, coordinationPath, {
    exchangeRoot: '/tmp/calli-exchange',
    resultDir: join(config.dataDir, 'coordination', 'files'),
  });
  const collaborationServer = coordinationServer(coordination);
  if (!await listenPrivate(collaborationServer, coordinationPath)) throw new Error('Collaboration socket is already in use');
  const toolsPath = join(config.dataDir, 'tools', 'calli.sock');
  const borrowing = new BorrowingService(store, manager, adapters, config.dataDir, toolsPath);
  const toolsServer = borrowingServer(borrowing);
  if (!await listenPrivate(toolsServer, toolsPath)) throw new Error('Tool socket is already in use');
  // Extensions start after recovery, scheduling and collaboration, and before anything is served,
  // so the first thing they hear is a machine that is ready.
  const extensions = new ExtensionHost({
    store: store.extensions,
    source: manager,
    catalog: loadCatalog(),
    externalDir: join(config.dataDir, 'extensions'),
    externalCatalog: loadSupportedExternalCatalog(),
    adapters,
    turnStart: (threadId, turnId) => store.listEventsOfType(threadId, 'turn.started').find((e) => e.turnId === turnId),
  });
  // 自动取名 has a daemon side: it hears turns end and asks the chosen agent for a title.
  const autotitleManifest = extensions.builtin(AUTOTITLE_ID);
  const autotitle = autotitleManifest ? new AutoTitle(autotitleManifest, manager, store, extensions) : undefined;
  if (autotitle) extensions.register(autotitle);
  extensions.start();
  const voice = new VoiceService({ adapters, extensions, manager });
  voice.start();
  // The board's consent to ask agents for a progress line; checked per turn, so switching it off takes effect at once.
  manager.progressWanted = () => extensions.list().some((s) => s.enabled && s.granted.includes('progress.report'));
  // The desktop a VNC server on this machine shows, if any; looked for when a page asks.
  const desktop = new Desktop({ targets: parseDesktopSetting(config.desktopVnc) });
  // The person's own browser, shown on that desktop; started only when someone asks for it.
  if (config.browserDisplay !== 'off') {
    borrowing.browser = new PersonalBrowser({
      profileDir: config.browserProfile,
      display: async () => config.browserDisplay ?? displayForDesktop(await desktop.find()) ?? (process.env.DISPLAY || undefined),
    });
  }
  const terminals = new TerminalManager({ workdirs: () => dirs.all(), shell: config.termShell ?? undefined });
  // Other machines connect out to this hub through their relay clients.
  const hosts = new HostRegistry({ file: join(config.dataDir, 'hosts.json') });
  // Collaboration can then run members on the machines the hub reaches.
  coordination.hosts = hosts;
  // Catch-up runs need all thread-scoped sockets and runtime services to be ready.
  scheduler.start();
  manager.lark.start();
  const larkEvents = new LarkEvents(manager.lark);
  larkEvents.start();
  // Previews are served on a port of their own: the iframe needs an origin separate from the app
  // (see preview.ts). One port past the app's is the default; CALLI_PREVIEW_PORT picks another.
  const previewPort = Number(process.env.CALLI_PREVIEW_PORT) || config.port + 1;
  const server = createHttpServer({ manager, push, feishu, registry, terminals, desktop, hosts, scheduler, extensions, voice, autotitle, localSessions, machineUsage: machineUsage ?? undefined, token: config.token, webDir: config.webDir, version: VERSION, previewPort });
  // Same requests, other origin: preview frames load from here so their pages cannot reach the
  // app, while their own CSS and scripts load same-origin. Without the port, previews say so.
  const preview = createHttpServer({ manager, registry, terminals, hosts, scheduler, extensions, token: config.token, webDir: null, version: VERSION, previewPort });
  preview.on('error', (err: NodeJS.ErrnoException) => {
    console.warn(`warning: preview port ${previewPort} unavailable (${err.code}); previews disabled`);
  });
  preview.listen(previewPort, config.host, () => console.log(`previews on http://${config.host}:${previewPort}`));
  // The same API on a socket only this user can open: how a hub on another machine reaches this one.
  const local = config.socket ? createHttpServer({ manager, push, feishu, registry, terminals, desktop, scheduler, extensions, voice, autotitle, localSessions, machineUsage: machineUsage ?? undefined, trusted: true, token: config.token, webDir: null, version: VERSION }) : null;
  let ownsSocket = false;
  if (local && config.socket) {
    listenPrivate(local, config.socket)
      .then((ok) => {
        ownsSocket = ok;
        console.log(ok ? `socket: ${config.socket}` : `socket: ${config.socket} belongs to another daemon, not listening on it`);
      })
      .catch((err) => console.warn(`warning: could not listen on ${config.socket}: ${err instanceof Error ? err.message : err}`));
  }

  // A hub elsewhere this machine joined: connect out to it and answer its requests from the socket above.
  let relay: RelayClient | null = null;
  if (config.hub && !config.socket) console.warn('warning: CALLI_HUB_URL is set but CALLI_SOCKET is off; not joining the hub');
  else if (config.hub && config.socket) {
    relay = new RelayClient({ hubUrl: config.hub.url, token: config.hub.token, socket: config.socket, version: VERSION });
    relay.start();
    console.log(`hub: connecting out to ${new URL(config.hub.url).origin}`);
  }

  server.listen(config.port, config.host, () => {
    console.log(`Calli daemon ${VERSION} listening on http://${config.host}:${config.port}`);
    console.log(`workdirs: ${dirs.roots().join(', ')}`);
    if (config.host === '0.0.0.0' || config.host === '::') {
      console.warn('warning: listening on all interfaces. Bind to the VPN address instead.');
    }
  });

  // Logs written before tool snapshots were collapsed keep one row per streamed fragment; the
  // rewrite is pure deletion and safe beside live turns, but slow on old data, so it runs here,
  // after the hub is up. File space comes back when scripts/compact-hub-db.sh vacuums.
  setTimeout(() => {
    try {
      const began = Date.now();
      store.collapseStoredToolOutputs();
      if (Date.now() - began > 1000) console.log(`tool snapshot collapse finished in ${Math.round((Date.now() - began) / 1000)}s`);
    } catch (err) {
      console.warn(`warning: tool snapshot collapse failed: ${err instanceof Error ? err.message : err}`);
    }
  }, 3000).unref();

  const shutdown = async (signal: string) => {
    console.log(`${signal} received, shutting down`);
    server.close();
    preview.close();
    local?.close();
    // Open event streams keep the server from closing by itself; the socket goes now, so a hub
    // that looks does not take a stopped daemon for a running one.
    if (ownsSocket && config.socket) rmSync(config.socket, { force: true });
    hosts.dispose();
    relay?.stop();
    scheduler.dispose();
    push.dispose();
    feishu.dispose();
    await voice.dispose();
    extensions.dispose();
    coordination.dispose();
    collaborationServer.close();
    rmSync(coordinationPath, { force: true });
    await borrowing.dispose();
    toolsServer.close();
    rmSync(toolsPath, { force: true });
    personal.close();
    rmSync(personalPath, { force: true });
    registry.dispose();
    machineUsage?.stop();
    workerReports.stop();
    await larkEvents.dispose();
    await manager.shutdown();
    await manager.personal.proactive.flush();
    terminals.dispose();
    desktop.dispose();
    store.close();
    process.exit(0);
  };
  process.on('SIGINT', () => void shutdown('SIGINT'));
  process.on('SIGTERM', () => void shutdown('SIGTERM'));
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : err);
  process.exit(1);
});
