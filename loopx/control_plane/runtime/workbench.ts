/** Optional Workbench process lifecycle. No Goal or Core lifecycle authority. */
import { spawn } from 'node:child_process';
import { request } from 'node:http';
import { connect } from 'node:net';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync, openSync, closeSync, writeFileSync, lstatSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { parseEnv } from 'node:util';
import { isSupportedNodeVersion } from './node_version.ts';

type Options = { action: 'start' | 'status' | 'stop'; stateDir?: string; appDir?: string;
  envFile?: string; port?: number; force?: boolean; sourceRoot: string };
type Report = { ok: boolean; state?: string; service?: string; instance_id?: string; pid?: number;
  url?: string; app_dir?: string; data_dir?: string; error?: string; [key: string]: unknown };
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const absent = (error: unknown) => ['ENOENT', 'ECONNREFUSED'].includes((error as NodeJS.ErrnoException).code ?? '');

export function localRequest(socketPath: string, path: string, method = 'GET', instance?: string): Promise<Report | null> {
  if (!existsSync(socketPath)) return Promise.resolve(null);
  return new Promise((resolve, reject) => {
    const req = request({ socketPath, path, method, agent: false, headers: instance ? { 'x-loopx-instance': instance } : {} }, res => {
      let text = '';
      res.setEncoding('utf8');
      res.on('data', chunk => {
        text += chunk;
        if (text.length > 65536) req.destroy(new Error('Oversized lifecycle reply.'));
      });
      res.on('error', reject);
      res.on('end', () => {
        try { resolve(JSON.parse(text) as Report); } catch { reject(new Error('Invalid lifecycle reply.')); }
      });
    });
    req.setTimeout(4000, () => req.destroy(new Error('Workbench is not answering; state is unknown.')));
    req.on('error', error => absent(error) ? resolve(null) : reject(error));
    req.end();
  });
}

async function socketAlive(path: string): Promise<boolean> {
  if (!existsSync(path)) return false;
  return new Promise((resolve, reject) => {
    const socket = connect(path);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.setTimeout(1000, () => socket.destroy(new Error('Socket probe timed out; state is unknown.')));
    socket.once('error', error => absent(error) ? resolve(false) : reject(error));
  });
}

function appDirectory(options: Options): string {
  const explicit = options.appDir || process.env.LOOPX_WORKBENCH_DIR;
  const candidates = explicit ? [explicit] : [
    ...(process.env.LOOCHAT_HOME ? [join(process.env.LOOCHAT_HOME, 'apps/workbench')] : []),
    join(options.sourceRoot, 'apps/workbench'),
    join(homedir(), '.local/share/loochat/apps/workbench'),
  ];
  const app = candidates.find(path => existsSync(join(path, 'packages/daemon/package.json')));
  if (!app) throw new Error('Workbench is installed separately. Follow apps/workbench/docs/install-agent.md, then pass --workbench-dir.');
  return realpathSync(app);
}

/** Keep another Workbench/agent session's runtime wiring out of the new process. */
export function launchEnvironment(config: NodeJS.ProcessEnv, state: string, app: string): NodeJS.ProcessEnv {
  const env = { ...process.env };
  for (const key of Object.keys(env)) {
    if (/^(LOOCHAT_|CALLI_|RAH_|LIMI_|WORKBENCH_)/.test(key) ||
        ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_EXECPATH', 'CLAUDECODE'].includes(key)) delete env[key];
  }
  Object.assign(env, config);
  // Explicit .env settings can retain providers, credentials, ports and data.
  // These paths identify the daemon controlled by this state directory.
  env.LOOCHAT_LIFECYCLE_SOCKET = join(state, 'lifecycle.sock');
  env.LOOCHAT_SOCKET = join(state, 'daemon.sock');
  env.LOOCHAT_WEB_DIR = join(app, 'packages/web/dist');
  env.LOOCHAT_DATA_DIR = resolve(app, 'packages/daemon', config.LOOCHAT_DATA_DIR || join(state, 'data'));
  env.LOOCHAT_WORKDIRS = config.LOOCHAT_WORKDIRS || dirname(dirname(app));
  env.LOOCHAT_ENV_FILE = '/dev/null'; // config was loaded explicitly, never cwd/.env
  return env;
}

export async function workbench(options: Options): Promise<Report> {
  if (!isSupportedNodeVersion(process.versions.node)) throw new Error('Node.js 22.22.3 or newer is required.');
  if (process.platform === 'win32') throw new Error('Workbench currently supports Linux and macOS.');
  const state = resolve(options.stateDir || process.env.WORKBENCH_STATE_DIR ||
    join(process.env.XDG_STATE_HOME || join(homedir(), '.local/state'), 'loochat-workbench'));
  const socket = join(state, 'lifecycle.sock');
  const log = join(state, 'daemon.log');
  const observed = async () => {
    const result = await localRequest(socket, '/status');
    if (result && (result.service !== 'loopx-workbench' || !result.instance_id))
      throw new Error('This socket is not a managed Workbench; no action taken.');
    return result;
  };
  const stopped = { ok: true, state: 'stopped', state_dir: state, log };
  const access = async (report: Report): Promise<Report> => {
    if (report.state !== 'running') return { ...report, ok: false, error: 'Workbench is stopping; retry start later.' };
    const granted = await localRequest(socket, '/access', 'POST', report.instance_id);
    if (!granted?.ok) return { ok: false, state: 'unverified', state_dir: state, log,
      error: granted?.error || 'Workbench disappeared before issuing the login link; retry status.' };
    return { ...report, ...granted, state_dir: state, log };
  };
  const legacy = async () => {
    if (await socketAlive(join(state, 'daemon.sock')) || await socketAlive(join(state, 'data/coordination/calli.sock')))
      throw new Error('An independently managed Workbench uses this state. Use its original launcher/service; it has not been stopped or replaced.');
  };
  const current = await observed();
  if (!current && options.action !== 'start' && existsSync(join(state, 'lifecycle.lock')))
    return { ok: false, state: 'unverified', state_dir: state, error: 'A startup lock exists; wait for startup or inspect daemon.log before recovering a stale lock.' };
  if (options.action === 'status') {
    if (!current) await legacy();
    return current ? { ...current, state_dir: state, log } : stopped;
  }
  if (options.action === 'stop') {
    if (!current) { await legacy(); return stopped; }
    const result = await localRequest(socket, options.force ? '/stop?force=1' : '/stop', 'POST', current.instance_id);
    if (!result?.ok) return result || { ok: false, error: 'Workbench disappeared before the stop request.' };
    for (let i = 0; i < 100; i++) {
      await delay(100);
      if (!await socketAlive(socket)) return stopped;
    }
    return { ok: false, state: 'stopping', error: 'Shutdown has not finished; retry status.', state_dir: state, log };
  }
  if (options.action !== 'start') throw new Error('Unknown workbench action.');
  if (current) {
    const savedPath = join(state, 'launch.json');
    const saved = options.envFile && existsSync(savedPath) ? JSON.parse(readFileSync(savedPath, 'utf8')) as Partial<Options> : {};
    if ((options.port != null && options.port !== Number(new URL(current.url!).port || 80)) ||
        (options.appDir && realpathSync(options.appDir) !== current.app_dir) ||
        (options.envFile && resolve(options.envFile) !== saved.envFile))
      throw new Error('Workbench already runs with its saved configuration. Stop it before changing launch options.');
    return access(current);
  }
  await legacy();
  const savedPath = join(state, 'launch.json');
  const saved = existsSync(savedPath) ? JSON.parse(readFileSync(savedPath, 'utf8')) as Partial<Options> : {};
  const app = appDirectory({ ...options, appDir: options.appDir || process.env.LOOPX_WORKBENCH_DIR || saved.appDir });
  const entry = join(app, 'packages/daemon/dist/index.js');
  if (!existsSync(entry) || !existsSync(join(app, 'packages/daemon/dist/workbench-lifecycle.js')) || !existsSync(join(app, 'packages/web/dist/index.html')))
    throw new Error(`Workbench build is missing or predates this CLI. Run npm ci && npm run build in ${app}, then retry.`);
  if (!existsSync(join(app, 'node_modules')))
    throw new Error(`Workbench dependencies are missing. Run npm ci && npm run build in ${app}.`);
  // No implicit installs, builds, extension enablement, or Core start/stop.
  mkdirSync(state, { recursive: true, mode: 0o700 });
  const lock = join(state, 'lifecycle.lock');
  try { mkdirSync(lock, { mode: 0o700 }); }
  catch { throw new Error(`Another lifecycle start is in progress. Retry; if it crashed, inspect and remove ${lock}.`); }
  let child: ReturnType<typeof spawn> | undefined;
  try {
    const raced = await observed();
    if (raced) return access(raced);
    await legacy();
    const envFile = resolve(options.envFile || saved.envFile || join(state, '.env'));
    if (options.envFile && !existsSync(envFile)) throw new Error('The selected --env-file does not exist.');
    if (!existsSync(envFile)) writeFileSync(envFile, `LOOCHAT_TOKEN=${randomBytes(24).toString('hex')}\n`, { mode: 0o600, flag: 'wx' });
    const config = parseEnv(readFileSync(envFile, 'utf8'));
    const { adoptLegacyEnv } = await import(pathToFileURL(join(app, 'packages/daemon/dist/config.js')).href);
    adoptLegacyEnv(config);
    const port = options.port ?? Number(process.env.WORKBENCH_PORT || saved.port || config.LOOCHAT_PORT || 8796);
    if (!Number.isInteger(port) || port < 1 || port > 65534) throw new Error('Port must be between 1 and 65534 (preview uses the next port).');
    const env = launchEnvironment(config, state, app);
    env.LOOCHAT_PORT = String(port);
    env.LOOCHAT_HOST = config.LOOCHAT_HOST || process.env.WORKBENCH_HOST || '127.0.0.1';
    // Check configured data too, before recovery can touch live threads.
    if (await socketAlive(join(env.LOOCHAT_DATA_DIR!, 'coordination/calli.sock')))
      throw new Error('The selected data directory is already in use; no action taken.');
    if (existsSync(socket)) {
      if (!lstatSync(socket).isSocket()) throw new Error('Lifecycle socket path contains a non-socket file; no action taken.');
      rmSync(socket);
    }
    const fd = openSync(log, 'a', 0o600);
    try {
      child = spawn(process.execPath, [entry], { cwd: join(app, 'packages/daemon'), env,
        detached: true, stdio: ['ignore', fd, fd] });
    } finally { closeSync(fd); }
    let failure: Error | undefined;
    child.on('error', error => { failure = error; });
    for (let i = 0; i < 150; i++) {
      await delay(100);
      if (failure || child.exitCode !== null || child.signalCode !== null) throw new Error(`Workbench failed to start; inspect ${log}.`);
      const ready = await observed();
      if (ready) {
        if (ready.pid !== child.pid) throw new Error('Another process answered startup; no ownership was assumed.');
        writeFileSync(`${savedPath}.tmp`, JSON.stringify({ appDir: app, envFile, port }) + '\n', { mode: 0o600 });
        renameSync(`${savedPath}.tmp`, savedPath);
        child.unref();
        child = undefined;
        return access(ready);
      }
    }
    throw new Error(`Workbench did not become ready; inspect ${log}.`);
  } catch (error) {
    // Only the child created by this invocation, never a PID read from disk.
    child?.kill('SIGTERM');
    throw error;
  } finally { rmSync(lock, { recursive: true }); }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const options = JSON.parse(readFileSync(0, 'utf8')) as Options;
    const result = await workbench(options);
    console.log(JSON.stringify(result));
    process.exitCode = result.ok ? 0 : 1;
  } catch (error) {
    console.log(JSON.stringify({ ok: false, error: (error as Error).message }));
    process.exitCode = 1;
  }
}
