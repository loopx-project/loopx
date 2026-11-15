import { spawn } from 'node:child_process';
import { accessSync, constants, existsSync, mkdirSync, readFileSync, readlinkSync, rmSync } from 'node:fs';
import { hostname } from 'node:os';
import { delimiter, join } from 'node:path';
import type { DesktopTarget } from './desktop.js';
import { HttpError } from './errors.js';

/**
 * The person's own browser on this machine: one headed Chrome with a lasting profile, shown on the
 * desktop a VNC server already serves. The person signs in there once, through the desktop panel;
 * an agent that asks for it drives the same browser with agent-browser over CDP, so sign-ins are
 * shared and pages load from this machine's network. Chrome outlives the daemon on purpose: a
 * restart must not sign anyone out or close the tabs someone is working in.
 */

const PROFILE_HELD = 'This browser profile is open in a Chrome that Calli did not start and cannot reach; close that Chrome and try again.';

export interface PersonalBrowserOptions {
  /** Chrome's user data directory. Holds sign-ins, so it is created private. */
  profileDir: string;
  /** argv that starts Chrome; the profile, debugging and window flags are appended. */
  command?: string[];
  /** The X display to show Chrome on, or undefined when this machine has none. */
  display: () => Promise<string | undefined>;
  /** How long a fresh Chrome may take to open its debugging port. */
  startTimeoutMs?: number;
}

export interface PersonalBrowserStatus {
  available: boolean;
  reason?: string;
  running: boolean;
  /** CDP endpoint on loopback, when running. */
  cdp?: string;
  port?: number;
  display?: string;
  agentBrowser: string | null;
}

/** The Chrome to use: an explicit one, else Google Chrome before Chromium, because Google sign-in trusts it. */
export function personalBrowserExecutable(env = process.env): string | undefined {
  if (env.CALLI_BROWSER_EXECUTABLE) return existsSync(env.CALLI_BROWSER_EXECUTABLE) ? env.CALLI_BROWSER_EXECUTABLE : undefined;
  return ['/usr/bin/google-chrome', '/usr/bin/google-chrome-stable', '/opt/google/chrome/chrome', '/usr/bin/chromium',
    '/usr/bin/chromium-browser', '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'].find(p => existsSync(p));
}

/** agent-browser is not bundled (it ships every platform's binary); `CALLI_AGENT_BROWSER` or PATH names it. */
export function agentBrowserExecutable(env = process.env): string | null {
  const explicit = env.CALLI_AGENT_BROWSER?.trim();
  if (explicit) return isExecutable(explicit) ? explicit : null;
  for (const dir of (env.PATH ?? '').split(delimiter)) {
    const candidate = dir && join(dir, 'agent-browser');
    if (candidate && isExecutable(candidate)) return candidate;
  }
  return null;
}

/**
 * Whether a live Chrome on this machine holds the profile: Chrome links SingletonLock to
 * `host-pid`. One started without a port file cannot be driven, and a second launch would only
 * hand itself to it and open another window there.
 */
export function profileHeld(profileDir: string): boolean {
  let target: string;
  try { target = readlinkSync(join(profileDir, 'SingletonLock')); } catch { return false; }
  const m = /^(.*)-(\d+)$/.exec(target);
  if (!m || m[1] !== hostname()) return false;
  try { process.kill(Number(m[2]), 0); return true; } catch (err) { return (err as NodeJS.ErrnoException).code === 'EPERM'; }
}

function isExecutable(path: string): boolean {
  try { accessSync(path, constants.X_OK); return true; } catch { return false; }
}

/** The X display a VNC server on this machine shows: TigerVNC and friends serve display N on 5900 + N. */
export function displayForDesktop(target: DesktopTarget | null): string | undefined {
  if (!target || !['127.0.0.1', 'localhost', '::1'].includes(target.host)) return undefined;
  const n = target.port - 5900;
  return n >= 0 && n < 100 && existsSync(`/tmp/.X11-unix/X${n}`) ? `:${n}` : undefined;
}

export class PersonalBrowser {
  private starting: Promise<number> | null = null;

  constructor(private readonly opts: PersonalBrowserOptions) {}

  /** The port of the Chrome already running on this profile, or null. A stale port file is not a browser. */
  private async runningPort(): Promise<number | null> {
    let port: number;
    try {
      port = Number(readFileSync(join(this.opts.profileDir, 'DevToolsActivePort'), 'utf8').split('\n')[0]);
    } catch { return null; }
    if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;
    try {
      const res = await fetch(`http://127.0.0.1:${port}/json/version`, { signal: AbortSignal.timeout(1500) });
      const body = await res.json() as { Browser?: unknown };
      return res.ok && typeof body.Browser === 'string' ? port : null;
    } catch { return null; }
  }

  async status(): Promise<PersonalBrowserStatus> {
    const agentBrowser = agentBrowserExecutable();
    const port = await this.runningPort();
    if (port) return { available: true, running: true, cdp: `http://127.0.0.1:${port}`, port, agentBrowser };
    const display = await this.opts.display();
    const reason = !this.command() ? 'Chrome or Chromium is not installed on this machine; set CALLI_BROWSER_EXECUTABLE.'
      : !display ? 'This machine has no desktop to show the browser on. Start a VNC server, or set CALLI_BROWSER_DISPLAY.'
      : undefined;
    return { available: !reason, ...(reason ? { reason } : {}), running: false, ...(display ? { display } : {}), agentBrowser };
  }

  /** Starts Chrome when it is not running; concurrent callers share one start. Returns the CDP port. */
  async ensure(): Promise<number> {
    const port = await this.runningPort();
    if (port) return port;
    this.starting ??= this.start().finally(() => { this.starting = null; });
    return this.starting;
  }

  /** Opens a page in a new tab of the person's browser. Only web addresses; nothing local. */
  async open(raw: unknown): Promise<{ port: number; url: string }> {
    const url = webUrl(raw);
    const port = await this.ensure();
    // Chrome unescapes what follows `?`; escaped whole, `&` and `#` stay part of the address.
    const res = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent(url)}`, { method: 'PUT', signal: AbortSignal.timeout(10000) });
    if (!res.ok) throw new HttpError(502, `the browser did not open the page (HTTP ${res.status})`);
    return { port, url };
  }

  private command(): string[] | undefined {
    if (this.opts.command) return this.opts.command;
    const exe = personalBrowserExecutable();
    return exe ? [exe] : undefined;
  }

  private async start(): Promise<number> {
    const command = this.command();
    if (!command) throw new HttpError(503, 'Chrome or Chromium is not installed on this machine; set CALLI_BROWSER_EXECUTABLE.');
    const display = await this.opts.display();
    if (!display) throw new HttpError(503, 'This machine has no desktop to show the browser on. Start a VNC server, or set CALLI_BROWSER_DISPLAY.');
    if (profileHeld(this.opts.profileDir)) throw new HttpError(409, PROFILE_HELD);
    mkdirSync(this.opts.profileDir, { recursive: true, mode: 0o700 });
    const portFile = join(this.opts.profileDir, 'DevToolsActivePort');
    rmSync(portFile, { force: true });
    // Port 0 lets Chrome pick a free port on loopback and write it to DevToolsActivePort.
    const child = spawn(command[0], [...command.slice(1), `--user-data-dir=${this.opts.profileDir}`,
      '--remote-debugging-port=0', '--no-first-run', '--no-default-browser-check', '--start-maximized'], {
      // The hub's own credentials stay with the hub; nothing Chrome starts needs them.
      detached: true, stdio: 'ignore', env: { ...process.env, DISPLAY: display, CALLI_TOKEN: undefined,
        CALLI_TOOLS_SOCKET: undefined, CALLI_TOOLS_KEY: undefined },
    });
    let exitedAt = 0;
    child.once('exit', () => { exitedAt = Date.now(); });
    child.once('error', () => { exitedAt = Date.now(); });
    child.unref();
    const until = Date.now() + (this.opts.startTimeoutMs ?? 20000);
    while (Date.now() < until) {
      const port = await this.runningPort();
      if (port) return port;
      // Chrome hands a second launch to an instance already using the profile and exits; without
      // a debugging port that instance cannot be driven, so stop waiting soon after.
      if (exitedAt && Date.now() - exitedAt > 2000) break;
      await new Promise(r => setTimeout(r, 200));
    }
    throw new HttpError(503, exitedAt
      ? `Chrome exited without opening its debugging port. ${PROFILE_HELD}`
      : 'Chrome did not open its debugging port in time.');
  }
}

function webUrl(raw: unknown): string {
  if (typeof raw !== 'string') throw new HttpError(400, 'url is required');
  let url: URL;
  try { url = new URL(raw.trim()); } catch { throw new HttpError(400, 'url must be an http(s) address'); }
  if (!['http:', 'https:'].includes(url.protocol)) throw new HttpError(400, 'url must be an http(s) address');
  return url.href;
}
