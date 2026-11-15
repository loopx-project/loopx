import { spawn } from 'node:child_process';
import { readdir, readlink } from 'node:fs/promises';
import { prepareDoubaoStreamRuntime } from './doubao-runtime.js';
import { EFFORT_LEVELS, type EffortLevel, type ModelOption } from './types.js';

const OUTPUT_LIMIT = 1024 * 1024;

/** Consume only the native catalog envelope; text/session output is never a model list. */
export function doubaoModels(stdout: string): ModelOption[] {
  let record;
  try { record = JSON.parse(stdout); } catch { throw new Error('Doubao 返回了无效模型目录'); }
  if (record?.type !== 'calli_models' || !Array.isArray(record.models) || record.models.length > 512) {
    throw new Error('Doubao 返回了无效模型目录');
  }
  const seen = new Set<string>();
  return record.models.flatMap((model: any): ModelOption[] => {
    if (typeof model?.modelId !== 'string' || !model.modelId.trim() || model.modelId.length > 256
      || typeof model.name !== 'string' || !model.name.trim() || model.name.length > 512
      || typeof model.available !== 'boolean' || typeof model.isDefault !== 'boolean'
      || !Array.isArray(model.efforts) || typeof model.effortAdjustable !== 'boolean' || seen.has(model.modelId)) {
      throw new Error('Doubao 返回了无效模型选项');
    }
    seen.add(model.modelId);
    const efforts: EffortLevel[] = model.efforts.map((effort: any) => {
      const level = effort?.level === 'none' ? 'off' : effort?.level;
      if (!EFFORT_LEVELS.includes(level)) throw new Error('Doubao 返回了未知推理强度，请升级 Calli');
      return level;
    });
    if (!model.available) return [];
    return [{ id: model.modelId, label: model.name, isDefault: model.isDefault,
      ...(typeof model.description === 'string' ? { description: model.description } : {}),
      efforts: model.effortAdjustable ? [...new Set(efforts)] : [], images: false }];
  });
}

/** Native --list can spawn an idle shared service whose executable identity belongs to this copy.
 * Let it exit itself before returning to a different runtime. Never stop it or delete a live copy. */
export async function waitForDoubaoCatalogExit(directory: string, timeoutMs = 15_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const processes = await readdir('/proc');
    const owned = await Promise.all(processes.filter(pid => /^\d+$/.test(pid)).map(async pid => {
      try { return (await readlink(`/proc/${pid}/exe`)).startsWith(directory + '/'); }
      catch { return false; } // Exited or another user's process, never ours to manage.
    }));
    if (!owned.some(Boolean)) return;
    if (Date.now() >= deadline) throw new Error('Doubao 模型读取后后台服务尚未退出，请稍后重试；运行副本已保留');
    await new Promise(resolve => setTimeout(resolve, 100));
  }
}

/** Run --list only in a guarded catalog copy; no prompt, conversation or account configuration. */
export async function readDoubaoModels(command: string[], env: NodeJS.ProcessEnv, cwd: string, timeoutMs = 30_000): Promise<ModelOption[]> {
  const runtime = await prepareDoubaoStreamRuntime(command, env, cwd, 'models');
  try {
    const stdout = await new Promise<string>((resolve, reject) => {
      const child = spawn(runtime.command[0], [...runtime.command.slice(1), '--list'], {
        cwd, env, stdio: ['ignore', 'pipe', 'pipe'], detached: process.platform !== 'win32',
      });
      let stdout = '', stderr = '', error: Error | undefined, force: NodeJS.Timeout | undefined;
      const stop = (reason: Error) => {
        if (error) return;
        error = reason;
        child.kill('SIGINT');
        force = setTimeout(() => {
          if (process.platform !== 'win32' && child.pid) {
            try { process.kill(-child.pid, 'SIGKILL'); } catch { /* The owned group exited. */ }
          } else child.kill('SIGKILL');
        }, 5000);
        force.unref();
      };
      const timer = setTimeout(() => stop(new Error('Doubao 模型读取超时')), timeoutMs);
      child.stdout.setEncoding('utf8');
      child.stderr.setEncoding('utf8');
      child.stdout.on('data', (text: string) => {
        if (error) return;
        stdout += text;
        if (Buffer.byteLength(stdout) > OUTPUT_LIMIT) { stdout = ''; stop(new Error('Doubao 模型目录超过大小限制')); }
      });
      child.stderr.on('data', (text: string) => { stderr = (stderr + text).slice(-8192); });
      child.once('error', err => { error = err; });
      // Wait for the native child's pipes before removing the runtime it is using.
      child.once('close', code => {
        clearTimeout(timer); clearTimeout(force);
        if (error) reject(error);
        else if (code !== 0) reject(new Error(stderr.trim() || `Doubao 模型读取失败（code=${code}）`));
        else resolve(stdout);
      });
    });
    return doubaoModels(stdout);
  } finally {
    await waitForDoubaoCatalogExit(runtime.directory);
    await runtime.close();
  }
}
