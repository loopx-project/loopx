import type { AvailableInstallation, Event, Runtime, Session, SessionOptions } from '@botiverse/oar/browser';
import type { AgentAdapter, AgentSession, LoginSession, LoginState, ModelOption, StartOptions, TurnHooks, TurnInput, TurnResult } from './types.js';
import { EFFORT_LEVELS, sessionEnvironment } from './types.js';
import { agentLoginRequired } from './diagnostics.js';

/** These are additional providers; existing adapters keep their native contracts. */
export const OAR_AGENTS = [
  { adapter: 'cursor', label: 'Cursor', binary: null, installGuide: 'Node.js 24+; Cursor SDK is included. Sign in from the agent row.' },
  { adapter: 'codebuddy', label: 'CodeBuddy Code', binary: 'codebuddy', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/codebuddy.md' },
  { adapter: 'devin', label: 'Devin', binary: 'devin', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/devin.md' },
  { adapter: 'factory', label: 'Factory Droid', binary: 'droid', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/factory.md' },
  { adapter: 'grok', label: 'Grok Build', binary: 'grok', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/grok.md' },
  { adapter: 'kimi', label: 'Kimi Code', binary: 'kimi', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/kimi.md' },
  { adapter: 'opencode', label: 'OpenCode', binary: 'opencode', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/opencode.md' },
  { adapter: 'qoder', label: 'Qoder', binary: 'qodercli', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/qoder.md' },
  { adapter: 'qwen', label: 'Qwen Code', binary: 'qwen', installGuide: 'Node.js 24+', installUrl: 'https://github.com/botiverse/oar/blob/main/docs/runtimes/qwen.md' },
] as const;
const loginUrl = (value: string): string => {
  const url = new URL(value);
  if (!['https:', 'http:'].includes(url.protocol)) throw new Error('Unsupported login URL');
  return url.href;
};

export type OarAgentId = typeof OAR_AGENTS[number]['adapter'];
export const isOarAgent = (id: string): id is OarAgentId => OAR_AGENTS.some(agent => agent.adapter === id);

/** Import one runtime on demand. In particular, do not load OAR's unused Pi SDK. */
async function loadRuntime(id: OarAgentId): Promise<Runtime> {
  switch (id) {
    case 'cursor': return (await import('@botiverse/oar/runtimes/cursor')).createCursorRuntime({ execution: 'subprocess', sdkModule: import.meta.resolve('@cursor/sdk') });
    case 'codebuddy': return (await import('@botiverse/oar/runtimes/codebuddy')).codebuddyRuntime;
    case 'devin': return (await import('@botiverse/oar/runtimes/devin')).devinRuntime;
    case 'factory': return (await import('@botiverse/oar/runtimes/factory')).factoryRuntime;
    case 'grok': return (await import('@botiverse/oar/runtimes/grok')).grokRuntime;
    case 'kimi': return (await import('@botiverse/oar/runtimes/kimi')).kimiRuntime;
    case 'opencode': return (await import('@botiverse/oar/runtimes/opencode')).opencodeRuntime;
    case 'qoder': return (await import('@botiverse/oar/runtimes/qoder')).qoderRuntime;
    case 'qwen': return (await import('@botiverse/oar/runtimes/qwen')).qwenRuntime;
  }
}

/** OAR removes environment keys with null, whereas native spawn adapters use undefined. */
export function oarEnvironment(env: Record<string, string>, context?: Record<string, string>): NonNullable<SessionOptions['env']> {
  return Object.fromEntries(Object.entries(sessionEnvironment(env, context)).map(([key, value]) => [key, value ?? null]));
}

export class OarAdapter implements AgentAdapter {
  readonly label: string;
  readonly installGuide: string;
  unavailableReason?: string;
  readonly capabilities: AgentAdapter['capabilities'] = {
    resume: true, approvals: false, permissionModes: ['full_access'], defaultPermissionMode: 'full_access',
    effortLevels: [], images: false, deleteSession: false, history: false, quota: false,
    fork: false, forkAt: false, forkInto: false, multiAccount: false, modelSwitch: true, coordination: true,
  };
  private runtime?: Promise<Runtime>;
  private probe?: { at: number; value: Promise<AvailableInstallation | null> };

  constructor(readonly id: OarAgentId, private readonly command: string[] = [], private readonly env: Record<string, string> = {},
    private readonly runtimeLoader: (id: OarAgentId) => Promise<Runtime> = loadRuntime) {
    const meta = OAR_AGENTS.find(agent => agent.adapter === id)!;
    this.label = meta.label;
    this.installGuide = meta.installGuide;
  }

  private engine(): Promise<Runtime> {
    if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('OAR requires Node.js 24 or newer.');
    return this.runtime ??= this.runtimeLoader(this.id).catch(error => { this.runtime = undefined; throw error; });
  }

  private environment(context?: Record<string, string>): NonNullable<SessionOptions['env']> {
    const env = oarEnvironment(this.env, context);
    if (this.command.length && this.id !== 'cursor') return { ...env, [`OAR_${this.id.toUpperCase()}_BIN`]: this.command[0] };
    if (this.command.length) throw new Error('Cursor uses its SDK; a CLI command override is not supported.');
    return env;
  }

  private installation(refresh = false): Promise<AvailableInstallation | null> {
    if (!refresh && this.probe && Date.now() - this.probe.at < 30_000) return this.probe.value;
    const value = (async () => {
      try {
        const runtime = await this.engine();
        const result = await runtime.installation?.({ env: this.environment() });
        if (result?.kind !== 'available') {
          this.unavailableReason = result?.kind === 'unsupported' ? result.reason : undefined;
          return null;
        }
        this.unavailableReason = undefined;
        this.capabilities.images = runtime.sessionCapabilities?.(result).images === true;
        return result;
      } catch (error) {
        this.unavailableReason = error instanceof Error ? error.message : 'OAR runtime unavailable';
        return null;
      }
    })();
    this.probe = { at: Date.now(), value };
    return value;
  }

  async available(): Promise<boolean> { return !!await this.installation(); }

  async discoverInstallation(): Promise<{ command?: string } | null> {
    const installed = await this.installation();
    return installed ? installed.via === 'executable' ? { command: installed.command } : {} : null;
  }

  async models(refresh = false): Promise<ModelOption[]> {
    const installed = await this.installation(refresh);
    if (!installed) return [];
    const runtime = await this.engine();
    const result = await runtime.listModels?.(installed, { env: this.environment(), timeoutMs: 15000 });
    if (result?.kind === 'unauthenticated') throw agentLoginRequired(this.label);
    if (result?.kind === 'unsupported') throw new Error(result.reason);
    return result?.models.filter(model => !model.disabled).map(model => ({
      id: model.id, label: model.displayName ?? model.id,
      efforts: (model.effortLevels ?? []).filter((value): value is typeof EFFORT_LEVELS[number] => EFFORT_LEVELS.some(level => level === value)),
      ...(model.default ? { isDefault: true } : {}),
    })) ?? [];
  }

  async commands() { return []; }
  resumeCommand() { return ''; } // OAR resume is through the existing workbench conversation.

  startLogin(onSuccess: (account?: string) => Promise<void>): LoginSession {
    let state: LoginState = { status: 'starting', input: 'none' };
    const controller = new AbortController();
    let answer: ((value: string) => void) | undefined;
    void (async () => {
      const installed = await this.installation(true);
      if (controller.signal.aborted) return;
      if (!installed) { state = { status: 'error', error: 'unavailable' }; return; }
      const runtime = await this.engine();
      if (!runtime.login) { state = { status: 'error', error: 'unavailable' }; return; }
      const result = await runtime.login(installed, {
        signal: controller.signal,
        onEvent(event) {
          if (controller.signal.aborted) return;
          if (event.kind === 'auth_url') state = { status: 'waiting', url: loginUrl(event.url), input: 'none' };
          if (event.kind === 'device_code') state = { status: 'waiting', url: loginUrl(event.verificationUri), input: 'none', userCode: event.userCode };
        },
        prompt(prompt) {
          if (controller.signal.aborted || prompt.signal?.aborted) return Promise.reject(new Error('Login cancelled'));
          if (prompt.kind === 'select' || prompt.kind === 'secret' || !state.url) { state = { status: 'error', error: 'unavailable' }; return Promise.reject(new Error('Finish this credential setup in the native client.')); }
          state = { ...state, status: 'waiting', input: 'code-or-url' };
          return new Promise<string>((resolve, reject) => {
            answer = resolve;
            const cancel = () => { answer = undefined; reject(new Error('Login cancelled')); };
            controller.signal.addEventListener('abort', cancel, { once: true });
            prompt.signal?.addEventListener('abort', cancel, { once: true });
          });
        },
      }, { env: this.environment() });
      if (controller.signal.aborted) return;
      if (result.kind === 'logged_in') { await onSuccess(result.account?.email); if (!controller.signal.aborted) state = { status: 'success' }; }
      else state = { status: result.kind === 'cancelled' ? 'cancelled' : 'error', error: 'failed' };
    })().catch(() => { if (!controller.signal.aborted && state.error !== 'unavailable') state = { status: 'error', error: 'failed' }; });
    return {
      state: () => state,
      submit(value) { if (!answer) throw new Error('This login is waiting for browser approval.'); const resolve = answer; answer = undefined; state = { ...state, status: 'submitting' }; resolve(value); },
      cancel() { controller.abort(); state = { status: 'cancelled' }; },
    };
  }

  async start(opts: StartOptions): Promise<AgentSession> {
    if (opts.permissionMode !== 'full_access') throw new Error('OAR agents require an explicit full_access permission mode.');
    if (opts.workMode && opts.workMode !== 'execute') throw new Error('OAR does not expose workbench plan mode.');
    if (opts.speed) throw new Error('OAR service tiers are not exposed by this adapter.');
    const installed = await this.installation();
    if (!installed) throw new Error(this.unavailableReason ?? `${this.label} is not installed.`);
    const runtime = await this.engine();
    const session = await runtime.session(installed, {
      cwd: opts.cwd, resume: opts.resumeId, model: opts.model, effort: opts.effort,
      env: this.environment(opts.coordinationEnv),
      ...(this.command.length > 1 ? { launchArgs: this.command.slice(1) } : {}),
      retainedBytes: 8 * 1024 * 1024,
    });
    this.capabilities.images = session.capabilities.images;
    opts.onSessionId?.(session.id);
    return new OarSession(session);
  }
}

class OarSession implements AgentSession {
  constructor(private readonly session: Session) {}
  get agentSessionId() { return this.session.id; }
  async prompt(input: TurnInput, hooks: TurnHooks): Promise<TurnResult> {
    const { promptAndWait } = await import('@botiverse/oar/observe');
    if (input.images.some(image => !image.path)) throw new Error('An image attachment is missing its local file.');
    const toolText = new Map<string, string>();
    const unsubscribe = this.session.events(event => projectOarEvent(event, hooks, this.session.id, toolText), { cursor: { sessionId: this.session.id, afterSeq: this.session.records().at(-1)?.seq ?? -1 } });
    try {
      const run = await promptAndWait(this.session, input.text, { images: input.images.map(image => ({ path: image.path!, mediaType: image.mimeType })) });
      if (run.kind === 'rejected') {
        if (['runtime_exited', 'disposed', 'session_released'].includes(run.code)) throw new Error(run.reason);
        return { stopReason: 'error', error: run.reason };
      }
      if (run.kind === 'handled') return { stopReason: 'error', error: 'The runtime handled this input without reporting a model turn.' };
      if (run.outcome.kind === 'failed') {
        // A lost process cannot accept another prompt. Let the manager close it and retain the resume id.
        if (run.outcome.failure === 'runtime_exited') throw new Error(run.outcome.reason);
        return { stopReason: 'error', error: run.outcome.reason };
      }
      return { stopReason: run.outcome.kind === 'aborted' ? 'cancelled' : 'end_turn' };
    } finally { unsubscribe(); }
  }
  async cancel() {
    const result = await this.session.abort();
    if (result.kind === 'rejected' && result.code !== 'no_active_turn') throw new Error(result.reason);
  }
  async close() { await this.session.dispose(); }
}

function toolInput(text: string | undefined): Record<string, unknown> | undefined {
  if (!text) return undefined;
  try { const value: unknown = JSON.parse(text); return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : undefined; }
  catch { return undefined; } // Partial native JSON is not a complete tool invocation yet.
}

/** Preserve child identities and only render root text as the assistant's answer. */
export function projectOarEvent(event: Event, hooks: TurnHooks, rootSessionId: string, toolText = new Map<string, string>()): void {
  const emit = hooks.emit;
  const id = 'callId' in event ? JSON.stringify([event.sessionId, event.agentPath, event.callId]) : '';
  switch (event.kind) {
    case 'text_delta': if (event.sessionId === rootSessionId && !event.agentPath.length) emit({ type: 'message.delta', data: { text: event.text } }); break;
    case 'reasoning': if (event.sessionId === rootSessionId && !event.agentPath.length && event.content.kind === 'text') emit({ type: 'reasoning.delta', data: { text: event.content.text } }); break;
    case 'tool_call_started': toolText.set('input:' + id, event.input ?? ''); emit({ type: 'tool.start', data: { id, title: event.tool, input: toolInput(event.input), status: 'in_progress' } }); break;
    case 'tool_call_input': toolText.set('input:' + id, event.input); emit({ type: 'tool.update', data: { id, input: toolInput(event.input) } }); break;
    case 'tool_call_input_delta': {
      const input = (toolText.get('input:' + id) ?? '') + event.delta;
      toolText.set('input:' + id, input); emit({ type: 'tool.update', data: { id, input: toolInput(input) } }); break;
    }
    case 'tool_call_progress': {
      const output = (event.output ?? toolText.get('output:' + id) ?? '') + (event.outputDelta ?? '');
      toolText.set('output:' + id, output); emit({ type: 'tool.update', data: { id, output } }); break;
    }
    case 'tool_call_ended': emit({ type: 'tool.end', data: { id, status: event.result === 'failed' ? 'failed' : 'completed', output: event.content?.filter(part => part.type === 'text').map(part => part.text).join('\n') } }); break;
    case 'usage': if (event.sessionId === rootSessionId && !event.agentPath.length && event.usage.context) emit({ type: 'usage.updated', data: { used: event.usage.context.tokens, size: event.usage.context.contextWindow } }); break;
  }
}
