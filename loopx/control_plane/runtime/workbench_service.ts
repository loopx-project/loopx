/** Foreground native-service worker. The OS, not this process, owns restarting. */
import { spawn } from 'node:child_process';
import { closeSync, openSync, readFileSync, appendFileSync } from 'node:fs';
import { join } from 'node:path';
import { prepareLaunch, socketAlive } from './workbench.ts';

const state = process.argv[2];
try {
  if (!state) throw new Error('Workbench service requires its state directory.');
  const saved = JSON.parse(readFileSync(join(state, 'launch.json'), 'utf8'));
  const launch = await prepareLaunch(state, { action: 'start', sourceRoot: '', appDir: saved.appDir,
    envFile: saved.envFile, port: saved.port });
  if (await socketAlive(join(state, 'daemon.sock')) || await socketAlive(join(launch.env.LOOCHAT_DATA_DIR!, 'coordination/calli.sock')))
    throw new Error('Another daemon already uses this state; no process was stopped.');
  const fd = openSync(join(state, 'daemon.log'), 'a', 0o600);
  const child = spawn(process.execPath, [launch.entry], { cwd: join(launch.app, 'packages/daemon'),
    env: launch.env, stdio: ['ignore', fd, fd] });
  closeSync(fd);
  let stopping = false;
  for (const signal of ['SIGTERM', 'SIGINT'] as const) process.on(signal, () => { stopping = true; child.kill(signal); });
  process.exitCode = await new Promise<number>((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', (code) => resolve(stopping ? 0 : code ?? 1));
  });
} catch (error) {
  const message = `Workbench service: ${(error as Error).message}\n`;
  if (state) { try { appendFileSync(join(state, 'daemon.log'), message, { mode: 0o600 }); } catch { /* Native manager still retains stderr. */ } }
  console.error(message.trim());
  process.exitCode = 1;
}
