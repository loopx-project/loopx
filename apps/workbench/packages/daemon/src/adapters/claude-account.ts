import { spawn, execFile } from 'node:child_process';
import { cp, readFile, writeFile, lstat } from 'node:fs/promises';
import { join } from 'node:path';
import { stripVTControlCharacters } from 'node:util';
import { HttpError } from '../errors.js';
import type { LoginSession, LoginState } from './types.js';

// A new subscription must not inherit an API key or another account's OAuth override.
const AUTH_ENV = ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
  'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR', 'CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR',
  'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY'];

export async function copyClaudeAccount(source: string, configFile: string, target: string, env: Record<string, string>): Promise<Record<string, string>> {
  // Copy portable configuration only. Credentials, histories, caches and project trust stay put.
  for (const name of ['CLAUDE.md', 'skills', 'agents', 'commands', 'hooks', 'settings.json', 'settings.local.json']) {
    const path = join(source, name);
    try { await lstat(path); } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') continue;
      throw error;
    }
    if (name.startsWith('settings.')) {
      const settings = JSON.parse(await readFile(path, 'utf8'));
      delete settings.apiKeyHelper;
      delete settings.forceLoginOrgUUID;
      delete settings.forceLoginMethod;
      for (const key of [...AUTH_ENV, 'CLAUDE_CONFIG_DIR']) if (settings.env) delete settings.env[key];
      await writeFile(join(target, name), JSON.stringify(settings, null, 2) + '\n', { mode: 0o600 });
    } else {
      // Materialize symlinks so editing the new account cannot write through into the source.
      await cp(path, join(target, name), { recursive: true, dereference: true, force: false, errorOnExist: true });
    }
  }
  let config: Record<string, unknown> = {};
  try { config = JSON.parse(await readFile(configFile, 'utf8')); } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
  }
  // Always create this file: an empty account must not fall back to the original global config.
  await writeFile(join(target, '.claude.json'), JSON.stringify({ mcpServers: config.mcpServers ?? {} }, null, 2) + '\n', { mode: 0o600 });
  return { ...env, ...Object.fromEntries(AUTH_ENV.map(key => [key, ''])), CLAUDE_CONFIG_DIR: target };
}

/** The native CLI owns OAuth, PKCE and credential storage. Only its URL and final status leave here. */
export function startClaudeLogin(binary: string, configDir: string, extraEnv: Record<string, string>, onSuccess: (account?: string) => Promise<void>, timeoutMs = 10 * 60_000): LoginSession {
  const env = { ...process.env, ...extraEnv, CLAUDE_CONFIG_DIR: configDir, BROWSER: 'true' };
  let state: LoginState = { status: 'starting' };
  let output = '';
  const child = spawn(binary, ['auth', 'login', '--claudeai'], { env, stdio: ['pipe', 'pipe', 'pipe'] });
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
        if (!['claude.ai', 'claude.com', 'console.anthropic.com', 'platform.claude.com'].includes(url.hostname) ||
            url.username || url.password || !url.pathname.endsWith('/oauth/authorize')) continue;
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
  child.on('close', (code) => {
    if (state.status === 'cancelled' || state.status === 'error') return;
    if (code !== 0) { fail('failed'); return; }
    state = { status: 'submitting' };
    execFile(binary, ['auth', 'status'], { env, timeout: 15_000, maxBuffer: 64 * 1024 }, async (error, stdout) => {
      if (state.status !== 'submitting') return;
      try {
        const status = JSON.parse(stdout);
        if (error || status.loggedIn !== true) { fail('verification'); return; }
        await onSuccess(typeof status.email === 'string' ? status.email : undefined);
        if (state.status !== 'submitting') return;
        clearTimeout(timer);
        state = { status: 'success' };
      } catch { fail('verification'); }
    });
  });
  return {
    state: () => ({ ...state }),
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
