/** User-login service registration for one Workbench state directory. */
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

type CommandResult = { code: number; stdout: string };
export type ServiceRun = (command: string, args: string[]) => Promise<CommandResult>;
export type ServicePlan = { platform: 'darwin' | 'linux'; state: string; label: string; file: string;
  args: string[]; environment: { HOME: string; PATH: string; WORKBENCH_HOST: string }; content: string };
export type ServiceObservation = { configured: boolean; enabled: boolean | null; loaded: boolean | null;
  running: boolean | null; pid?: number; state?: string; failure?: string; manager?: string; unit?: string; error?: string };

const run: ServiceRun = (command, args) => new Promise((resolveResult, reject) => {
  execFile(command, args, { encoding: 'utf8', timeout: 8000, maxBuffer: 512 * 1024 }, (error, stdout) => {
    if (error && typeof error.code !== 'number') return reject(new Error(`${command} could not answer; service state is unknown.`));
    resolveResult({ code: error ? Number(error.code) : 0, stdout });
  });
});
const xml = (text: string) => text.replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&apos;');
const unitQuote = (text: string, command = false) => '"' + text.replaceAll('\\', '\\\\').replaceAll('"', '\\"').replaceAll('%', '%%').replaceAll('$', () => command ? '$$' : '$') + '"';
const validateText = (text: unknown): string => {
  if (typeof text !== 'string' || !text || /[\0\r\n]/.test(text)) throw new Error('Invalid service path or environment value.');
  return text;
};
export function privateWrite(file: string, content: string): void {
  mkdirSync(dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${randomUUID()}.tmp`;
  try { writeFileSync(temporary, content, { mode: 0o600, flag: 'wx' }); renameSync(temporary, file); }
  finally { rmSync(temporary, { force: true }); }
}

export function servicePlan(state: string, options: { platform?: string; home?: string; configHome?: string;
  node?: string; runner?: string; path?: string; host?: string } = {}): ServicePlan {
  const platform = options.platform ?? process.platform;
  if (platform !== 'darwin' && platform !== 'linux') throw new Error('Autostart requires macOS launchd or Linux systemd --user.');
  state = existsSync(state) ? realpathSync(state) : resolve(state);
  const home = options.home ?? homedir();
  const id = createHash('sha256').update(state).digest('hex').slice(0, 16);
  const label = platform === 'darwin' ? `org.loopx.workbench.${id}` : `loopx-workbench-${id}.service`;
  const file = platform === 'darwin' ? join(home, 'Library/LaunchAgents', `${label}.plist`)
    : join(options.configHome ?? process.env.XDG_CONFIG_HOME ?? join(home, '.config'), 'systemd/user', label);
  const args = [options.node ?? realpathSync(process.execPath), '--no-warnings', '--experimental-strip-types',
    options.runner ?? fileURLToPath(new URL('./workbench_service.ts', import.meta.url)), state];
  const environment = { HOME: home, PATH: options.path ?? process.env.PATH ?? '/usr/bin:/bin', WORKBENCH_HOST: options.host ?? '127.0.0.1' };
  [state, file, ...args, ...Object.values(environment)].forEach(validateText);
  const log = join(state, 'daemon.log');
  const content = platform === 'darwin' ? `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>${xml(label)}</string>
<key>ProgramArguments</key><array>${args.map(value => `<string>${xml(value)}</string>`).join('')}</array>
<key>EnvironmentVariables</key><dict>${Object.entries(environment).map(([key, value]) => `<key>${key}</key><string>${xml(value)}</string>`).join('')}</dict>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>ThrottleInterval</key><integer>5</integer>
<key>ExitTimeOut</key><integer>20</integer>
<key>StandardOutPath</key><string>${xml(log)}</string>
<key>StandardErrorPath</key><string>${xml(log)}</string>
</dict></plist>
` : `[Unit]
Description=LoopX Workbench (${id})
StartLimitIntervalSec=60
StartLimitBurst=5
[Service]
Type=exec
ExecStart=${args.map(arg => unitQuote(arg, true)).join(' ')}
${Object.entries(environment).map(([key, value]) => `Environment=${unitQuote(`${key}=${value}`)}`).join('\n')}
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
KillMode=control-group
UMask=0077
[Install]
WantedBy=default.target
`;
  return { platform, state, label, file, args, environment, content };
}

export class WorkbenchAutostart {
  private state: string;
  private options: Parameters<typeof servicePlan>[1];
  private execute: ServiceRun;
  private uid: number;
  constructor(state: string, options: Parameters<typeof servicePlan>[1] & { run?: ServiceRun; uid?: number } = {}) {
    this.state = state; this.options = options; this.execute = options.run ?? run; this.uid = options.uid ?? process.getuid!();
  }
  private recordFile() { return join(this.state, 'autostart.json'); }
  registered() { return existsSync(this.recordFile()); }
  plan(host?: string) { return servicePlan(this.state, { ...this.options, host }); }
  private target(plan: ServicePlan) { return `gui/${this.uid}/${plan.label}`; }
  private async checked(command: string, args: string[]) {
    const result = await this.execute(command, args);
    if (result.code) throw new Error(`${command} ${args[0]} failed (exit ${result.code}); inspect the user service manager and retry.`);
    return result.stdout;
  }
  async preflight(): Promise<void> {
    const plan = this.plan();
    if (this.registered()) {
      const state = await this.inspect(); if (state.error) throw new Error(state.error);
    }
    else if (existsSync(plan.file)) throw new Error('An unowned native service file already exists; no service changed.');
    await this.checked(plan.platform === 'darwin' ? 'launchctl' : 'systemctl', plan.platform === 'darwin'
      ? ['print', `gui/${this.uid}`] : ['--user', 'show', '--property=Version', '--value']);
    if (plan.platform === 'linux') {
      const paths = await this.checked('systemctl', ['--user', 'show', '--property=UnitPath', '--value']);
      const canonical = (path: string): string => existsSync(path) ? realpathSync(path) : join(canonical(dirname(resolve(path))), basename(path));
      if (!paths.trim().split(/\s+/).some(path => canonical(path) === canonical(dirname(plan.file))))
        throw new Error('The user service manager does not read this configuration directory; check HOME/XDG_CONFIG_HOME.');
    }
    if (!this.registered()) {
      const loaded = await this.execute(plan.platform === 'darwin' ? 'launchctl' : 'systemctl', plan.platform === 'darwin'
        ? ['print', this.target(plan)] : ['--user', 'show', plan.label, '--property=LoadState', '--value']);
      if (plan.platform === 'darwin' ? loaded.code !== 113 : loaded.code !== 0 || loaded.stdout.trim() !== 'not-found')
        throw new Error('A native job already exists or could not be verified; no service changed.');
    }
  }
  private owned(): ServicePlan | null {
    if (!this.registered()) return null;
    const record = JSON.parse(readFileSync(this.recordFile(), 'utf8')) as ServicePlan;
    if (!Array.isArray(record.args) || record.args.length !== 5 || !record.environment) throw new Error('Autostart registration is unreadable; no service changed.');
    const expected = servicePlan(this.state, { ...this.options, node: record.args[0], runner: record.args[3],
      home: record.environment.HOME, path: record.environment.PATH, host: record.environment.WORKBENCH_HOST });
    const location = this.plan();
    if (record.file !== location.file || record.label !== location.label || record.platform !== location.platform ||
        JSON.stringify(record) !== JSON.stringify(expected)) throw new Error('Autostart registration no longer matches this instance; no service changed.');
    if (!existsSync(record.file) || readFileSync(record.file, 'utf8') !== record.content)
      throw new Error('The native service file is missing or was changed outside LoopX; no service changed.');
    return record;
  }
  async inspect(): Promise<ServiceObservation> {
    if (!this.registered()) return { configured: false, enabled: false, loaded: false, running: false };
    try {
      const plan = this.owned()!;
      if (plan.platform === 'linux') {
        const result = await this.checked('systemctl', ['--user', 'show', plan.label, '--property=LoadState,ActiveState,UnitFileState,MainPID,FragmentPath,Result,ExecMainStatus', '--no-pager']);
        const fields = Object.fromEntries(result.trim().split('\n').map(line => { const at = line.indexOf('='); return [line.slice(0, at), line.slice(at + 1)]; }));
        if (fields.LoadState !== 'loaded' || fields.FragmentPath !== plan.file) throw new Error('Loaded systemd service does not match its owned file.');
        const pid = Number(fields.MainPID);
        if (!Number.isSafeInteger(pid) || pid < 0) throw new Error('Invalid native service PID.');
        if (pid) {
          const actual = readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').filter(Boolean);
          if (JSON.stringify(actual) !== JSON.stringify(plan.args)) throw new Error('Loaded service process identity changed; no service changed.');
        }
        return { configured: true, enabled: fields.UnitFileState === 'enabled', loaded: true,
          running: pid > 0, pid, state: fields.ActiveState,
          ...(fields.ActiveState === 'failed' ? { failure: `Native service failed (${fields.Result}, exit ${fields.ExecMainStatus}); inspect daemon.log and retry start after fixing configuration.` } : {}), manager: 'systemd', unit: plan.label };
      }
      const disabledText = await this.checked('launchctl', ['print-disabled', `gui/${this.uid}`]);
      const disabled = disabledText.split('\n').some(line => line.trim() === `"${plan.label}" => true`);
      const loaded = await this.execute('launchctl', ['print', this.target(plan)]);
      if (loaded.code !== 0 && loaded.code !== 113) throw new Error('launchd could not verify the loaded job.');
      let pid = 0;
      if (loaded.code === 0) {
        const args = /^\s*arguments = \{\n([\s\S]*?)^\s*\}/m.exec(loaded.stdout);
        if (!args || JSON.stringify(args[1].split('\n').map(line => line.trim()).filter(Boolean)) !== JSON.stringify(plan.args))
          throw new Error('Loaded launchd arguments differ from this registration.');
        const match = /^\s*pid = (\d+)\s*$/m.exec(loaded.stdout);
        pid = match ? Number(match[1]) : 0;
      }
      const exitCode = /^\s*last exit code = (\d+)\s*$/m.exec(loaded.stdout)?.[1];
      return { configured: true, enabled: !disabled, loaded: loaded.code === 0, running: pid > 0, pid,
        ...(pid === 0 && exitCode && exitCode !== '0' ? { failure: `Native service exited with ${exitCode}; inspect daemon.log.` } : {}),
        manager: 'launchd', unit: plan.label };
    } catch (error) { return { configured: true, enabled: null, loaded: null, running: null, error: (error as Error).message }; }
  }
  async install(host: string): Promise<void> {
    await this.preflight();
    if (this.registered()) { this.owned(); return; }
    const plan = this.plan(host);
    if (existsSync(plan.file)) throw new Error('An unowned native service file already exists; no service changed.');
    privateWrite(plan.file, plan.content);
    privateWrite(this.recordFile(), JSON.stringify(plan));
  }
  async start(enable: boolean): Promise<void> {
    const plan = this.owned();
    if (!plan) throw new Error('Autostart is not registered.');
    // A newly written unit must be loaded before its process identity can be read.
    if (plan.platform === 'linux') {
      await this.checked('systemctl', ['--user', 'daemon-reload']);
      const state = await this.inspect(); if (state.error) throw new Error(state.error);
      if (state.state === 'failed') await this.checked('systemctl', ['--user', 'reset-failed', plan.label]);
      if (enable) await this.checked('systemctl', ['--user', 'enable', plan.label]);
      await this.checked('systemctl', ['--user', 'start', plan.label]);
    } else {
      const state = await this.inspect(); if (state.error) throw new Error(state.error);
      if (enable) await this.checked('launchctl', ['enable', this.target(plan)]);
      if (!state.loaded) await this.checked('launchctl', ['bootstrap', `gui/${this.uid}`, plan.file]);
      else if (!state.running) await this.checked('launchctl', ['kickstart', this.target(plan)]);
    }
  }
  async stop(disable: boolean): Promise<void> {
    const plan = this.owned(); if (!plan) return;
    const state = await this.inspect(); if (state.error) throw new Error(state.error);
    if (plan.platform === 'linux') {
      await this.checked('systemctl', ['--user', 'stop', plan.label]);
      if (disable) await this.checked('systemctl', ['--user', 'disable', plan.label]);
    } else if (state.loaded) await this.checked('launchctl', ['bootout', this.target(plan)]);
    if (disable) {
      // Remove only files whose exact content was verified above.
      this.owned(); rmSync(plan.file); rmSync(this.recordFile());
      if (plan.platform === 'linux') await this.checked('systemctl', ['--user', 'daemon-reload']);
    }
  }
}
