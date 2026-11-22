import { spawn } from 'node:child_process';
import { stripVTControlCharacters } from 'node:util';
import { HttpError } from '../errors.js';
import type { LoginSession, LoginState } from './types.js';

/** Native CLIs own OAuth and credential storage; the hub relays only a URL and one code. */
export function startCodeLogin(command: string[], env: NodeJS.ProcessEnv, authorize: (url: URL) => boolean,
  verify: () => Promise<string | undefined>, onSuccess: (account?: string) => Promise<void>,
  timeoutMs = 10 * 60_000, input?: LoginState['input']): LoginSession {
  let state: LoginState = { status: 'starting' };
  let output = '';
  const child = spawn(command[0], command.slice(1), { env, stdio: ['pipe', 'pipe', 'pipe'] });
  let stopped = false;
  const stop = () => {
    if (child.exitCode === null && !stopped) {
      stopped = true;
      child.kill('SIGTERM');
      const force = setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); }, 1000);
      force.unref();
      child.once('close', () => clearTimeout(force));
    }
  };
  const fail = (error: LoginState['error']) => {
    if (['success', 'error', 'cancelled'].includes(state.status)) return;
    state = { status: 'error', error };
    clearTimeout(timer);
    stop();
  };
  const timer = setTimeout(() => fail('expired'), timeoutMs);
  timer.unref();
  const receive = (chunk: Buffer) => {
    if (state.status !== 'starting') return;
    output = (output + stripVTControlCharacters(chunk.toString())).slice(-64 * 1024);
    for (const match of output.matchAll(/https:\/\/[^\s<>"']+(?=\s)/g)) {
      try {
        const url = new URL(match[0]);
        if (url.username || url.password || !authorize(url)) continue;
        state = { status: 'waiting', url: url.href };
        output = '';
        break;
      } catch { /* Wait for a complete URL. */ }
    }
  };
  child.stdout.on('data', receive);
  child.stderr.on('data', receive);
  child.stdin.on('error', () => fail('failed'));
  child.on('error', () => fail('unavailable'));
  child.on('close', async (code) => {
    if (state.status === 'cancelled' || state.status === 'error') return;
    if (code !== 0) { fail('failed'); return; }
    state = { status: 'submitting' };
    try {
      const account = await verify();
      if (state.status !== 'submitting') return;
      await onSuccess(account);
      if (state.status !== 'submitting') return;
      clearTimeout(timer);
      state = { status: 'success' };
    } catch { fail('verification'); }
  });
  return {
    state: () => ({ ...state, ...(input ? { input } : {}) }),
    submit(code) {
      if (state.status !== 'waiting') throw new HttpError(409, 'login is not waiting for a code');
      if (!code || code.length > 4096 || /\s|[\x00-\x1f\x7f]/.test(code)) throw new HttpError(400, 'invalid login code');
      state = { status: 'submitting' };
      child.stdin.write(code + '\n');
    },
    cancel() {
      clearTimeout(timer);
      if (state.status !== 'success') state = { status: 'cancelled' };
      stop();
    },
  };
}
