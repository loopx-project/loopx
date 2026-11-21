import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

export interface Config {
  host: string;
  port: number;
  token: string;
  dataDir: string;
  /** Directories the UI may start threads in. Empty means the daemon's cwd only. */
  workdirs: string[];
  /** Directories whose agent sessions the session list leaves out. Every other one is listed. */
  hiddenSessionDirs: string[];
  /** Full argv used to start the Claude ACP adapter. */
  claudeCommand: string[];
  /** Executable used to start pi in RPC mode. */
  piCommand: string[];
  /** Executable of the Antigravity CLI. */
  agyCommand: string[];
  /** argv that starts TRAE CLI as an ACP server. */
  traexCommand: string[];
  /** argv that starts Codex's app server. */
  codexCommand: string[];
  /** argv that starts DeepSeek Harness as an ACP server. */
  dshCommand: string[];
  /** Executable that runs in a web terminal. */
  termShell: string | null;
  /** Directory of the built web UI, served at `/` when present. */
  webDir: string | null;
  /** Private Unix socket used by the local relay client. Null when turned off. */
  socket: string | null;
  /** A hub this machine connects out to, with the join token it gave (see relay.ts). Null when not joined. */
  hub: { url: string; token: string } | null;
  /** LOOCHAT_DESKTOP_VNC as given: unset looks for a VNC server here, `off` turns the desktop off. */
  desktopVnc: string | undefined;
  /** Chrome profile of the person's own browser: `LOOCHAT_BROWSER_PROFILE`, else under the data directory. */
  browserProfile: string;
  /** `LOOCHAT_BROWSER_DISPLAY`: the X display to show it on; unset follows the VNC desktop, `off` turns it off. */
  browserDisplay: string | undefined;
}

/**
 * The default private socket is fixed under the home directory rather than XDG state: local
 * clients may run with a different environment. LOOCHAT_SOCKET can override it.
 */
export const SOCKET_IN_HOME = '.local/state/loochat/daemon.sock';
/** Where the socket lived as Calli, and before that as remote-agent-hub. Kept directories still use them. */
export const CALLI_SOCKET_IN_HOME = '.local/state/calli/daemon.sock';
export const LEGACY_SOCKET_IN_HOME = '.local/state/remote-agent-hub/daemon.sock';

/** The new place, unless only the place from before the rename exists. Nothing is moved. */
export function renamedPath(current: string, legacy: string): string {
  return !existsSync(current) && existsSync(legacy) ? legacy : current;
}

/** The first of these paths that exists, or the first one outright. */
function firstOf(...paths: string[]): string {
  return paths.find((p) => existsSync(p)) ?? paths[0];
}

/** The directory under the home directory for notes and personal data: `~/.loopx/loochat`, or `~/.calli` / `~/.limi` from before. */
export function homeDirectory(home = process.env.HOME || homedir()): string {
  return firstOf(join(home, '.loopx', 'loochat'), join(home, '.calli'), join(home, '.limi'));
}

/** Variables from before the rename whose new name is not the prefix swapped. */
const RENAMED: Record<string, string> = { LIMI_SOCKET: 'LOOCHAT_TEAM_SOCKET', LIMI_KEY: 'LOOCHAT_TEAM_KEY' };

/**
 * Accepts the `RAH_*`, `LIMI_*` and `LOOCHAT_*` names from before the rename: each one sets its
 * `LOOCHAT_*` name, unless that is set already, and is then dropped, so no session or child
 * inherits it under a name nothing masks. Run it before and after loading `.env`, which may use either.
 */
export function adoptLegacyEnv(env: NodeJS.ProcessEnv = process.env): void {
  for (const [key, value] of Object.entries(env)) {
    const name = RENAMED[key] ?? (/^(RAH|LIMI|CALLI)_/.test(key) ? key.replace(/^(RAH|LIMI|CALLI)_/, 'LOOCHAT_') : null);
    if (!name) continue;
    if (value !== undefined && env[name] === undefined) env[name] = value;
    delete env[key];
  }
}

/**
 * Loads `.env` from the current directory if it exists, without overriding
 * variables that are already set in the environment.
 */
export function loadDotEnv(file = process.env.LOOCHAT_ENV_FILE ?? '.env'): void {
  const path = resolve(file);
  if (!existsSync(path)) return;
  for (const raw of readFileSync(path, 'utf8').split('\n')) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    let value = line.slice(eq + 1).trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

/** Resolves the Claude ACP adapter from this package's own dependencies. */
function defaultClaudeCommand(): string[] {
  const require = createRequire(import.meta.url);
  const pkgJson = require.resolve('@agentclientprotocol/claude-agent-acp/package.json');
  const pkg = JSON.parse(readFileSync(pkgJson, 'utf8')) as { bin?: Record<string, string> };
  const rel = pkg.bin?.['claude-agent-acp'];
  if (!rel) throw new Error('claude-agent-acp package has no bin entry');
  return [process.execPath, join(dirname(pkgJson), rel)];
}

function splitList(value: string | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const token = env.LOOCHAT_TOKEN ?? '';
  if (token.length < 16) {
    throw new Error('LOOCHAT_TOKEN is missing or shorter than 16 characters. Set it in .env (see .env.example).');
  }

  const dataDir = resolve(env.LOOCHAT_DATA_DIR || './data');

  const workdirs = splitList(env.LOOCHAT_WORKDIRS).map((d) => (isAbsolute(d) ? d : resolve(d)));
  const hiddenSessionDirs = splitList(env.LOOCHAT_HIDDEN_SESSION_DIRS).map((d) => (isAbsolute(d) ? d : resolve(d)));

  const claudeCommand = env.LOOCHAT_CLAUDE_ACP_BIN ? [env.LOOCHAT_CLAUDE_ACP_BIN] : defaultClaudeCommand();
  const piCommand = [env.LOOCHAT_PI_BIN || 'pi'];
  const agyCommand = [env.LOOCHAT_AGY_BIN || 'agy'];
  const traexCommand = [env.LOOCHAT_TRAEX_BIN || 'traex', 'acp', 'serve'];
  const codexCommand = [env.LOOCHAT_CODEX_BIN || 'codex', 'app-server'];
  const dshCommand = [env.LOOCHAT_DSH_BIN || 'dsh', '--profile', 'acp'];
  const termShell = env.LOOCHAT_TERM_SHELL || null;

  // Serve the built UI from the sibling web package when it exists; LOOCHAT_WEB_DIR overrides.
  const bundledWebDir = fileURLToPath(new URL('../../web/dist', import.meta.url));
  const webDirEnv = env.LOOCHAT_WEB_DIR;
  const webDir = webDirEnv ? resolve(webDirEnv) : existsSync(bundledWebDir) ? bundledWebDir : null;

  const socketEnv = env.LOOCHAT_SOCKET?.trim();
  const home = env.HOME || homedir();
  // The socket's directory decides, not the socket: it is missing whenever the daemon is stopped.
  const stateDir = firstOf(join(home, dirname(SOCKET_IN_HOME)), join(home, dirname(CALLI_SOCKET_IN_HOME)), join(home, dirname(LEGACY_SOCKET_IN_HOME)));
  const socket = socketEnv === 'off' ? null : socketEnv ? resolve(socketEnv) : join(stateDir, 'daemon.sock');

  return {
    host: env.LOOCHAT_HOST || '127.0.0.1',
    port: Number(env.LOOCHAT_PORT || 8787),
    token,
    dataDir,
    workdirs,
    hiddenSessionDirs,
    claudeCommand,
    piCommand,
    agyCommand,
    traexCommand,
    codexCommand,
    dshCommand,
    termShell,
    webDir,
    socket,
    hub: env.LOOCHAT_HUB_URL?.trim() && env.LOOCHAT_HUB_TOKEN?.trim() ? { url: env.LOOCHAT_HUB_URL.trim(), token: env.LOOCHAT_HUB_TOKEN.trim() } : null,
    desktopVnc: env.LOOCHAT_DESKTOP_VNC,
    browserProfile: env.LOOCHAT_BROWSER_PROFILE ? resolve(env.LOOCHAT_BROWSER_PROFILE) : join(dataDir, 'browser', 'profile'),
    browserDisplay: env.LOOCHAT_BROWSER_DISPLAY?.trim() || undefined,
  };
}
