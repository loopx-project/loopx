#!/usr/bin/env node
/** Updates connected nodes through authenticated relay exec; no inbound node access is required. */
import { randomUUID } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { adoptLegacyEnv, loadConfig, loadDotEnv } from '../packages/daemon/dist/config.js';

adoptLegacyEnv();
loadDotEnv(process.env.CALLI_ENV_FILE ?? join(homedir(), '.config/calli/.env'));
adoptLegacyEnv();
const config = loadConfig();
const host = ['0.0.0.0', '::'].includes(config.host) ? '127.0.0.1' : config.host;
const base = process.env.CALLI_UPDATE_URL ?? `http://${host.includes(':') ? `[${host}]` : host}:${config.port}`;
const args = process.argv.slice(2);
const only = args.find(arg => !arg.startsWith('-'));
if (args.some(arg => arg.startsWith('-') && arg !== '--check') || args.filter(arg => !arg.startsWith('-')).length > 1) {
  throw new Error('Usage: scripts/update-hosts.sh [--check] [machine-id]');
}
const dry = args.includes('--check');
const quote = value => `'${String(value).replaceAll("'", "'\\''")}'`;
const script = readFileSync(new URL('./update-node.sh', import.meta.url), 'utf8');
const file = process.env.CALLI_HOSTS_FILE ?? join(config.dataDir, 'hosts.json');
const entries = existsSync(file) ? JSON.parse(readFileSync(file, 'utf8')).hosts ?? [] : [];
async function api(path, body) {
  const response = await fetch(new URL(path, base), {
    method: body === undefined ? 'GET' : 'POST',
    headers: { Authorization: `Bearer ${config.token}`, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    signal: AbortSignal.timeout(40_000),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error ?? `HTTP ${response.status}`);
  return value;
}
async function exec(id, command, input) {
  const result = await api(`/api/hosts/${encodeURIComponent(id)}/exec`, { command, input });
  if (result.code !== 0) throw new Error(result.stderr || `Command exited ${result.code}`);
  return Buffer.from(result.stdout, 'base64').toString('utf8');
}
const machines = (await api('/api/hosts')).filter(machine => !only || machine.id === only);
if (!machines.length) throw new Error('No matching machines registered');
for (const machine of machines) {
  console.log(`[${machine.id}] ${dry ? 'Checking' : 'Updating'}`);
  try {
    if (machine.status !== 'online') throw new Error('node is offline');
    const job = `calli-update-${randomUUID()}`;
    const folder = `/tmp/${job}`;
    const entry = entries.find(entry => entry.id === machine.id);
    const params = ['calli', '~/.local/state/calli/daemon.sock', entry?.repo ?? '~/code/calli', dry ? '1' : '0', '5'].map(quote).join(' ');
    // A separate unit survives restarting the node daemon. Tokens never enter the node's script.
    await exec(machine.id, `umask 077; mkdir ${quote(folder)} && cat > ${quote(`${folder}/run.sh`)} && systemd-run --user --quiet --collect --unit=${quote(job)} /bin/sh -c ${quote(`/bin/sh ${quote(`${folder}/run.sh`)} ${params} > ${quote(`${folder}/log`)} 2>&1; echo $? > ${quote(`${folder}/status`)}`)}`, script);
    let done = false;
    // Builds and busy threads may take a while. A timeout leaves the job and its logs intact.
    for (const deadline = Date.now() + 30 * 60_000; Date.now() < deadline;) {
      await new Promise(resolve => setTimeout(resolve, 3000));
      let output;
      try { output = await exec(machine.id, `if test -f ${quote(`${folder}/status`)}; then cat ${quote(`${folder}/status`)} ${quote(`${folder}/log`)}; fi`); }
      catch { continue; } // The node reconnects after its service switches releases.
      if (!output) continue;
      const [status, ...lines] = output.split('\n');
      console.log(lines.join('\n'));
      if (status !== '0' || lines.some(line => line.startsWith('fail '))) throw new Error(`update failed; logs: ${folder}/log`);
      await exec(machine.id, `rm -rf -- ${quote(folder)}`);
      done = true;
      break;
    }
    if (!done) throw new Error(`still waiting; inspect the node's ${folder}/log`);
  } catch (error) {
    console.error(`[${machine.id}] ${error.message}`);
    process.exitCode = 1;
  }
}
