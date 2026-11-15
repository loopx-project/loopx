/**
 * A throwaway hub for checking work by hand: the e2e harness's daemon (own port, data dir,
 * workdir and fake agents), plus a smoke test of one turn. Never touches the real hub.
 *
 *   node packages/e2e/dev-instance.mjs           start, smoke test, keep running until Ctrl-C
 *   node packages/e2e/dev-instance.mjs --check   start, smoke test, stop
 *   node packages/e2e/dev-instance.mjs --host 0.0.0.0   bind where a browser can reach it
 *
 * Expects a build; scripts/init.sh builds first.
 */
import { networkInterfaces } from 'node:os';
import { startDaemon, api } from './harness.mjs';

const args = process.argv.slice(2);
const check = args.includes('--check');
const hostIndex = args.indexOf('--host');
const host = hostIndex >= 0 ? args[hostIndex + 1] : undefined;

async function smoke(daemon) {
  const agents = await (await api(daemon, '/api/agents')).json();
  if (!agents.some((a) => a.id === 'pi' && a.available)) throw new Error('fake pi agent is not available');
  // The test hub's agents are the fakes it was built with; anything else means this machine's
  // real CLIs or accounts auto-joined it.
  const builtIn = ['claude', 'pi', 'agy', 'traex', 'codex', 'zcode', 'dsh', 'doubao'];
  const extra = agents.filter((a) => !builtIn.includes(a.id));
  if (extra.length) throw new Error(`the test hub picked up agents beyond its fakes: ${extra.map((a) => a.id).join(', ')}`);
  const html = await fetch(daemon.base + '/');
  if (!html.ok) throw new Error(`GET / answered ${html.status}`);

  const res = await api(daemon, '/api/threads', { method: 'POST', body: JSON.stringify({ agent: 'pi', cwd: daemon.workdir, title: 'smoke' }) });
  if (res.status !== 201) throw new Error(`creating a thread answered ${res.status}`);
  const thread = await res.json();
  const sent = await api(daemon, `/api/threads/${thread.id}/turns`, { method: 'POST', body: JSON.stringify({ text: 'hello' }) });
  if (!sent.ok) throw new Error(`sending a turn answered ${sent.status}`);
  const deadline = Date.now() + 20_000;
  for (;;) {
    const t = await (await api(daemon, `/api/threads/${thread.id}`)).json();
    if (t.status === 'idle') break;
    if (Date.now() > deadline) throw new Error(`the smoke turn did not finish; status ${t.status}`);
    await new Promise((r) => setTimeout(r, 100));
  }
  // The fake pi answers "hi there <text>"; a turn that errored ends idle too, without it.
  const events = await (await api(daemon, `/api/threads/${thread.id}/events`)).json();
  const answer = events.filter((e) => e.type === 'message.delta').map((e) => e.data.text).join('');
  if (!events.some((e) => e.type === 'turn.done') || !answer.startsWith('hi there hello')) {
    throw new Error(`the smoke turn did not answer; events:\n${JSON.stringify(events, null, 1)}`);
  }
}

const daemon = await startDaemon(host ? { CALLI_HOST: host } : {});
let stopping = false;
async function stop(code) {
  if (stopping) return;
  stopping = true;
  await daemon.stop();
  process.exit(code);
}
process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));

try {
  await smoke(daemon);
} catch (err) {
  console.error(`Smoke test failed: ${err.message}\n--- daemon log ---\n${daemon.log()}`);
  await stop(1);
}

console.log(`Smoke test passed.
  URL      ${urls(daemon, host).map((u) => `${u}/`).join('   ')}
  Token    ${daemon.token}
  PID      ${daemon.pid} (daemon), ${process.pid} (this script)
  Data     ${daemon.dataDir}
  Workdir  ${daemon.workdir}`);
if (check) await stop(0);
else console.log(`Running. Stop with Ctrl-C, or: kill ${process.pid}  (cleans up the daemon and its temp dirs)`);

/** The addresses a browser can open: the one asked for, or every interface when asked for all. */
function urls(daemon, host) {
  const port = daemon.base.split(':').pop();
  const listed = host && host !== '127.0.0.1' && host !== 'localhost'
    ? host === '0.0.0.0' || host === '::'
      ? Object.values(networkInterfaces()).flat().filter((a) => a && a.family === 'IPv4' && !a.internal).map((a) => a.address)
      : [host]
    : ['127.0.0.1'];
  return (listed.length ? listed : ['127.0.0.1']).map((a) => `http://${a}:${port}`);
}
