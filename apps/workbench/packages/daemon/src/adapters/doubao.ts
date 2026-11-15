import { spawn, type ChildProcessByStdio } from 'node:child_process';
import type { Readable } from 'node:stream';
import { cliDiagnostic } from './diagnostics.js';
import { DOUBAO_STREAM_BRIDGE, prepareDoubaoStreamRuntime, validateDoubaoStreamRuntime, type DoubaoStreamRuntime } from './doubao-runtime.js';
import { DoubaoStream } from './doubao-stream.js';
import { readDoubaoModels } from './doubao-models.js';
import { DOUBAO_ICON } from './icons.js';
import { quoteArg } from './session-location.js';
import { sessionEnvironment, type AgentAdapter, type AgentSession, type EffortLevel, type ModelOption, type PermissionMode, type StartOptions, type TurnHooks, type TurnInput, type TurnResult } from './types.js';
import { commandExists } from '../util.js';

/**
 * Doubao CLI's public automation surface is one `-p` process per turn. Its text response is on
 * stdout; stderr announces the native conversation as {type:"session",session_id:"..."}.
 * The CLI exposes no ACP or output-format flag. An opt-in stream bridge enables the shipped JSON
 * printer in an isolated runtime copy, never modifying the installation. Compatibility follows
 * the patch anchors rather than a pinned version; incompatible installs fall back to text output.
 */
export const DOUBAO_METADATA = {
  adapter: 'doubao' as const,
  label: 'Doubao CLI',
  icon: DOUBAO_ICON,
  installGuide: 'npm install -g @bytedance/doubao-cli',
};

const BYPASS = '--dangerously-bypass-approvals-and-sandbox';
const VALUE_OPTIONS = new Set(['-C', '-p', '--resume', '--model', '--effort', '--permission-mode']);
const CANCEL_GRACE_MS = 5_000;
const STDERR_LIMIT = 8_192;
const STREAM_LINE_LIMIT = 8 * 1024 * 1024;

/** Replace only overridden business flags; keep executable-wrapper arguments and native defaults. */
function withoutOptions(args: string[], removed: Set<string>): string[] {
  const out: string[] = [];
  for (let i = 0; i < args.length; i++) {
    const name = args[i].split('=')[0];
    if (!removed.has(name)) out.push(args[i]);
    else if (VALUE_OPTIONS.has(name) && !args[i].includes('=')) i++;
  }
  return out;
}

/** The CLI rejects duplicate options, including defaults supplied in an instance's command. */
export function doubaoArgs(opts: StartOptions, text: string, base: string[] = []): string[] {
  const removed = new Set(['-C', '-p', '--resume', '--list', '--service-start', '--permission-mode', BYPASS]);
  if (opts.model) removed.add('--model');
  if (opts.effort) removed.add('--effort');
  if (opts.permissionMode === 'ask') throw new Error('Doubao 无界面模式不能请求人工审批');
  return [
    ...withoutOptions(base, removed),
    '-C', opts.cwd,
    ...(opts.permissionMode === 'full_access' ? [BYPASS] : ['--permission-mode', 'ask_risky']),
    ...(opts.model ? ['--model', opts.model] : []),
    ...(opts.effort ? ['--effort', opts.effort === 'off' ? 'none' : opts.effort] : []),
    ...(opts.resumeId ? ['--resume', opts.resumeId] : []),
    '-p', text,
  ];
}

export class DoubaoAdapter implements AgentAdapter {
  readonly id = 'doubao';
  readonly label = DOUBAO_METADATA.label;
  readonly icon = DOUBAO_METADATA.icon;
  readonly installGuide = DOUBAO_METADATA.installGuide;
  readonly capabilities = {
    coordination: true,
    streamBridge: DOUBAO_STREAM_BRIDGE,
    resume: true,
    approvals: false,
    permissionModes: ['auto_edit', 'full_access'] as const,
    defaultPermissionMode: 'auto_edit' as const,
    // A guarded temporary copy calls the native read-only loader, without opening a conversation.
    modelCatalog: true,
    modelSwitch: true,
    effortLevels: [] as const,
    images: false,
    deleteSession: false,
    history: false,
    quota: false,
    fork: false,
    forkAt: false,
    forkInto: false,
    multiAccount: false,
  };

  private readonly command: string[];
  constructor(command: string[] = ['doubao'], private readonly env: Record<string, string> = {}) {
    this.command = command.length ? [...command] : ['doubao'];
  }

  private modelCache: { at: number; value: ModelOption[] } | null = null;
  private modelLoad: Promise<ModelOption[]> | null = null;
  onModelsChanged?: (models: ModelOption[]) => void;

  available(): Promise<boolean> { return Promise.resolve(commandExists(this.command[0])); }
  models(refresh = false): Promise<ModelOption[]> {
    if (this.modelLoad) return this.modelLoad;
    if (!refresh && this.modelCache && Date.now() - this.modelCache.at < 60_000) return Promise.resolve(this.modelCache.value);
    // Strip every business flag: a catalog process cannot inherit a prompt or resume a conversation.
    this.modelLoad = readDoubaoModels([this.command[0], ...this.cliPrefix()], sessionEnvironment(this.env), process.cwd())
      .then(models => {
        const configured = this.command.slice(1).flatMap((arg, index, args) =>
          arg === '--model' ? [args[index + 1]] : arg.startsWith('--model=') ? [arg.slice(8)] : []).at(-1);
        if (configured) models = models.map(model => ({ ...model, isDefault: model.id === configured || model.label === configured }));
        const previous = this.modelCache;
        this.modelCache = { at: Date.now(), value: models };
        if (previous && JSON.stringify(previous.value) !== JSON.stringify(models)) this.onModelsChanged?.(models);
        return models;
      }).finally(() => { this.modelLoad = null; });
    return this.modelLoad;
  }
  commands() { return Promise.resolve([]); }
  get streamBridgeEnabled(): boolean { return (this.env.CALLI_DOUBAO_STREAM ?? process.env.CALLI_DOUBAO_STREAM) === '1'; }
  /** Saving consent never fails: `incompatible` reports why turns will run in text output. */
  async streamBridgeEnvironment(enabled: boolean): Promise<{ environment: Record<string, string>; incompatible?: string }> {
    const environment = { CALLI_DOUBAO_STREAM: enabled ? '1' : '0' };
    if (!enabled) return { environment };
    try {
      await validateDoubaoStreamRuntime(this.command, sessionEnvironment(this.env), process.cwd());
      return { environment };
    } catch (error) {
      return { environment, incompatible: error instanceof Error ? error.message : String(error) };
    }
  }
  diagnose() { return cliDiagnostic([this.command[0], ...this.cliPrefix()], this.env, this.streamBridgeEnabled ? 'doubao stream-json bridge (experimental)' : 'doubao -p (text)'); }

  /** Account-directory settings only; neither shell command carries scoped Calli credentials. */
  private cliPrefix(): string[] {
    return withoutOptions(this.command.slice(1), new Set([...VALUE_OPTIONS, '--list', '--service-start', BYPASS]));
  }
  loginCommand(): string {
    const variables = ['HOME', 'XDG_DATA_HOME', 'LOCALAPPDATA'].flatMap(key => this.env[key] ? [`${key}=${quoteArg(this.env[key])}`] : []);
    return `${variables.length ? `env ${variables.join(' ')} ` : ''}${[this.command[0], ...this.cliPrefix(), 'login'].map(quoteArg).join(' ')}`;
  }
  resumeCommand(id: string): string {
    // Preserve configured model/effort defaults but never inherit a different conversation.
    const base = withoutOptions(this.command.slice(1), new Set(['-C', '-p', '--resume', '--list', '--service-start']));
    const variables = ['HOME', 'XDG_DATA_HOME', 'LOCALAPPDATA'].flatMap(key => this.env[key] ? [`${key}=${quoteArg(this.env[key])}`] : []);
    return `${variables.length ? `env ${variables.join(' ')} ` : ''}${[this.command[0], ...base, '--resume', id].map(quoteArg).join(' ')}`;
  }

  async start(opts: StartOptions): Promise<AgentSession> {
    if (opts.workMode === 'plan') throw new Error('此 agent 不支持计划模式');
    if (opts.permissionMode === 'ask') throw new Error('Doubao 无界面模式不能请求人工审批');
    // A new native id exists only after the first real prompt, never an empty probing session.
    // A bridge that cannot be prepared right now degrades to the public text surface, not an error.
    let runtime: DoubaoStreamRuntime | undefined;
    if (this.streamBridgeEnabled) {
      try { runtime = await prepareDoubaoStreamRuntime(this.command, sessionEnvironment(this.env, opts.coordinationEnv), opts.cwd); }
      catch (error) { console.warn(`[doubao] stream bridge unavailable, falling back to text output: ${error instanceof Error ? error.message : error}`); }
    }
    return new DoubaoSession(runtime?.command ?? this.command, { ...opts }, this.env, runtime);
  }
}

interface RunningTurn {
  child: ChildProcessByStdio<null, Readable, Readable>;
  done: Promise<TurnResult>;
  cancelled: boolean;
  force?: NodeJS.Timeout;
}

class DoubaoSession implements AgentSession {
  agentSessionId: string;
  private active: RunningTurn | null = null;
  private closed = false;

  constructor(private readonly command: string[], private readonly opts: StartOptions, private readonly env: Record<string, string>, private readonly runtime?: DoubaoStreamRuntime) {
    this.agentSessionId = opts.resumeId ?? '';
  }

  /** Flags apply to the next process, which resumes this conversation rather than opening a new one. */
  async setPermissionMode(mode: PermissionMode): Promise<void> {
    if (mode === 'ask') throw new Error('Doubao 无界面模式不能请求人工审批');
    this.opts.permissionMode = mode;
  }

  async setModel(model: string): Promise<boolean> { this.opts.model = model; return true; }
  async setEffort(effort: EffortLevel): Promise<boolean> { this.opts.effort = effort; return true; }

  async prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    if (this.closed) throw new Error('Doubao 会话已关闭');
    if (this.active) throw new Error('a turn is already running');
    if (input.images.length) throw new Error('Doubao CLI 的公开接口不支持图片输入');
    if (!input.text.trim()) throw new Error('Doubao CLI 需要非空提示词');
    if (/^\/[a-z][\w-]*(?:\s|$)/i.test(input.text.trim())) {
      return { stopReason: 'error', error: 'Doubao 的斜杠命令仅在原生终端界面可用，请在终端恢复会话后使用。' };
    }

    const [bin, ...base] = this.command;
    const child = spawn(bin, doubaoArgs({ ...this.opts, resumeId: this.agentSessionId || undefined }, input.text, base), {
      cwd: this.opts.cwd,
      env: sessionEnvironment(this.env, this.opts.coordinationEnv),
      // Native print mode rejects a closed pipe during startup. A null input works without
      // supplying answers to an updater or login prompt, and never holds the process open.
      stdio: ['ignore', 'pipe', 'pipe'],
      // Own the launcher and its native child as one group, never the CLI's shared service.
      detached: process.platform !== 'win32',
    });
    let finish!: (result: TurnResult) => void;
    const done = new Promise<TurnResult>(resolve => { finish = resolve; });
    const running: RunningTurn = { child, done, cancelled: false };
    this.active = running;
    let stderr = '', stdout = '', tail = '', error: string | undefined, replied = false;
    const identity = (id: string) => {
      if (this.agentSessionId && this.agentSessionId !== id) throw new Error('Doubao 返回了不同的原生会话 ID，已停止本轮以避免混用会话');
      if (!this.agentSessionId) {
        this.agentSessionId = id;
        try { this.opts.onSessionId?.(id); }
        catch (error) { this.closed = true; throw error; }
      }
    };
    const stream = this.runtime ? new DoubaoStream(event => hooks.emit(event), identity) : undefined;
    const nativeRecord = (record: string) => {
      if (error || !record.trim()) return;
      try { stream!.record(record); }
      catch (err) { error = err instanceof Error ? err.message : String(err); this.stop(running); }
    };
    const line = (value: string) => {
      let record;
      try { record = JSON.parse(value); } catch { /* Native diagnostics are plain text. */ }
      if (record?.type === 'session' && typeof record.session_id === 'string' && /^[a-z0-9_-]{1,256}$/i.test(record.session_id)) {
        try { identity(record.session_id); }
        catch (err) { error = err instanceof Error ? err.message : String(err); this.stop(running); }
      } else tail = (tail + value + '\n').slice(-STDERR_LIMIT);
    };
    child.stdout.setEncoding('utf8');
    child.stdout.on('data', (text: string) => {
      if (error) return;
      if (!stream) { if (text) { replied = true; hooks.emit({ type: 'message.delta', data: { text } }); } return; }
      stdout += text;
      let index;
      while ((index = stdout.indexOf('\n')) >= 0) {
        if (index > STREAM_LINE_LIMIT) { error = 'Doubao 流式记录超过大小限制'; this.stop(running); break; }
        nativeRecord(stdout.slice(0, index).replace(/\r$/, ''));
        stdout = stdout.slice(index + 1);
      }
      if (stdout.length > STREAM_LINE_LIMIT) { error = 'Doubao 流式记录超过大小限制'; this.stop(running); stdout = ''; }
    });
    child.stderr.setEncoding('utf8');
    child.stderr.on('data', (text: string) => {
      stderr += text;
      let index;
      while ((index = stderr.indexOf('\n')) >= 0) {
        line(stderr.slice(0, index).replace(/\r$/, ''));
        stderr = stderr.slice(index + 1);
      }
      if (stderr.length > STDERR_LIMIT) { tail = (tail + stderr).slice(-STDERR_LIMIT); stderr = ''; }
    });
    child.once('error', err => { error = `Doubao CLI 无法启动：${err.message}`; });
    // Wait for the pipes too: a native child's last session announcement can trail launcher exit.
    child.once('close', (code, signal) => {
      if (stderr) line(stderr.replace(/\r$/, ''));
      if (stream && stdout) nativeRecord(stdout.replace(/\r$/, ''));
      const result = stream?.finish();
      clearTimeout(running.force);
      if (this.active === running) this.active = null;
      if (running.cancelled) finish({ stopReason: 'cancelled' });
      else if (error) finish({ stopReason: 'error', error });
      else if (result?.stopReason === 'error' || result?.stopReason === 'cancelled') finish(result.error === 'Doubao 流式桥在最终结果到达前断开' && tail.trim() ? { ...result, error: tail.trim() } : result);
      else if (code !== 0) finish({ stopReason: 'error', error: tail.trim() || `Doubao CLI exited (code=${code}, signal=${signal})` });
      else if (!this.agentSessionId) {
        // Retrying on this handle would silently create a second native conversation.
        this.closed = true;
        finish({ stopReason: 'error', error: 'Doubao CLI 未返回原生会话 ID，请检查 CLI 版本后重试。' });
      } else if (result) finish(result);
      else if (!replied) finish({ stopReason: 'error', error: tail.trim() || 'Doubao CLI 未返回回复' });
      else finish({ stopReason: 'end_turn' });
    });
    return done;
  }

  private stop(running: RunningTurn): void {
    if (running.force) return;
    running.child.kill('SIGINT'); // The npm launcher forwards this to its native process.
    running.force = setTimeout(() => {
      if (process.platform !== 'win32' && running.child.pid) {
        try { process.kill(-running.child.pid, 'SIGKILL'); } catch { /* The owned process group already exited. */ }
      } else running.child.kill('SIGKILL');
    }, CANCEL_GRACE_MS);
    running.force.unref();
  }
  async cancel(): Promise<void> {
    const running = this.active;
    if (!running) return;
    running.cancelled = true;
    this.stop(running);
    await running.done;
  }
  async close(): Promise<void> {
    this.closed = true;
    await this.cancel();
    await this.runtime?.close();
  }
}
