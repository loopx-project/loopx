import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Store } from '../../src/store.js';
import { ThreadManager } from '../../src/threads.js';
import { AttachmentStore } from '../../src/attachments.js';
import { ClaudeAcpAdapter } from '../../src/adapters/claude.js';
import { LoopxService } from '../../src/adapters/loopx/service.js';

const [root, stage] = process.argv.slice(2);
const data = join(root, 'data'), work = join(root, 'work');
mkdirSync(work, { recursive: true });
if (stage !== 'recover') writeFileSync(join(root, 'requests.jsonl'), '');
const adapter = new ClaudeAcpAdapter([process.execPath, fileURLToPath(new URL('./fake-acp.mjs', import.meta.url))], {
  CLAUDE_CONFIG_DIR: join(root, 'claude'), FAKE_ACP_REQUEST_LOG: join(root, 'requests.jsonl'),
  ...(stage === 'submitted' ? { FAKE_ACP_HOLD_PROMPT: '1' } : {}),
});
const adapters = new Map([['claude', adapter]]);
const store = new Store(data);
const manager = new ThreadManager(store, adapters, [work], new AttachmentStore(data));
const service = new LoopxService({ dataDir: data, adapters, dirs: manager.dirs,
  core: new URL('http://127.0.0.1:1'), cli: { command: [process.execPath, fileURLToPath(new URL('./fake-loopx.mjs', import.meta.url))] },
});
manager.loopx = service;
service.attach(manager);
if (stage === 'prepare' || stage === 'submitted') {
  const thread = await manager.createThread({ agent: 'claude', cwd: work, permissionMode: 'ask' });
  writeFileSync(join(root, 'thread'), thread.id);
  service.bindings.set(thread.id, { goalId: 'g-a', goalAgentId: 'claude', managedAgentId: null, goalInstanceId: null,
    workspace: work, permission: 'read_only', proposalId: 'fixture-proposal', firstMessage: 'Review the release', basis: 'workbench', source: 'launch' });
  manager.autoHandover.setOverride(thread.id, false);
  manager.subscribe(event => {
    if (stage !== 'prepare' || event.type !== 'turn.phase' || event.data.phase !== 'session_ready') return;
    // runTurn calls the real AcpSession synchronously; its usage baseline first yields to filesystem IO.
    // Kill at that yield, without replacing production methods or suspending a promise.
    queueMicrotask(() => {
      writeFileSync(join(root, 'before.json'), JSON.stringify({ events: manager.listEvents(thread.id, 0), binding: service.binding(thread.id) }));
      process.kill(process.pid, 'SIGKILL');
    });
  });
  await service.apply({ proposalId: 'fixture-proposal' });
  if (stage === 'submitted') {
    const poll = setInterval(() => {
      if (readFileSync(join(root, 'requests.jsonl'), 'utf8').includes('session/prompt')) {
        clearInterval(poll);
        process.kill(process.pid, 'SIGKILL');
      }
    }, 10);
  }
} else {
  manager.recoverInterrupted();
  const threadId = readFileSync(join(root, 'thread'), 'utf8');
  let ended!: () => void;
  const finished = new Promise<void>(resolve => { ended = resolve; });
  const off = manager.subscribe(event => { if (event.threadId === threadId && (event.type === 'turn.done' || event.type === 'turn.error')) ended(); });
  const recovered = await service.apply({ proposalId: 'fixture-proposal' });
  if (!('delivery' in recovered)) await finished;
  off();
  const confirmed = await service.apply({ proposalId: 'fixture-proposal' });
  writeFileSync(join(root, 'after.json'), JSON.stringify({ recovered, confirmed, events: manager.listEvents(threadId, 0), binding: service.binding(threadId) }));
  await manager.shutdown();
  store.close();
}
