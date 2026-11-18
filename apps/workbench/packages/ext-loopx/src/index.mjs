import { createInterface } from 'node:readline';
import { projectStatus } from './projection.mjs';

let nextId = 0;
const pending = new Map();
const write = value => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...value }) + '\n');
function command(command, bindingId) {
  const id = `host-${++nextId}`;
  return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); write({ id, method: 'commands.run', params: { command, bindingId } }); });
}
async function detect() {
  const result = await command('version');
  const version = result.stdout.trim().match(/^loopx (\S+)$/)?.[1];
  return { ok: result.code === 0 && version === '1.4.2', version, ...(version === '1.4.2' ? {} : { missing: 'unverified_version' }) };
}
async function handle(method, params) {
  if (method === 'extension.detect') return detect();
  if (method === 'extension.validateSettings') return { errors: {} };
  if (method === 'worksource.snapshot') {
    const detection = await detect();
    if (!detection.ok) throw new Error(detection.missing || 'loopx_unavailable');
    const result = await command('status', params.binding.id);
    // status may report unrelated contract warnings as nonzero; only a valid scoped read succeeds.
    if (result.code !== 0) throw new Error('status_failed');
    return projectStatus(JSON.parse(result.stdout), params.binding.externalId);
  }
  throw new Error('unknown_method');
}
createInterface({ input: process.stdin, crlfDelay: Infinity }).on('line', line => {
  let message;
  try { message = JSON.parse(line); } catch { process.exitCode = 1; process.stdin.destroy(); return; }
  if (!message.method) {
    const p = pending.get(message.id); if (!p) return;
    pending.delete(message.id);
    if (message.error) p.reject(new Error(message.error.message)); else p.resolve(message.result);
  } else if (message.id !== undefined) {
    void handle(message.method, message.params).then(
      result => write({ id: message.id, result }),
      error => write({ id: message.id, error: { code: -32000, message: error.message } }),
    );
  }
});
