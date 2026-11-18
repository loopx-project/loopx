// Real daemon HTTP routing and Core CLI; only model-backed thread creation is recorded.
import { createHttpServer } from '../../daemon/src/http.ts';
import { LoopxService } from '../../daemon/src/adapters/loopx/service.ts';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const options = JSON.parse(input);
const threads = [];
const manager = {
  createThread: async (request) => {
    const thread = { id: `thread-${threads.length + 1}`, ...request };
    threads.push(thread);
    return thread;
  },
  announceLoopx: () => {},
  autoHandover: { setOverride: () => {} },
};
const service = new LoopxService({
  dataDir: options.dataDir,
  adapters: new Map([['codex', { id: 'codex' }]]),
  dirs: { all: () => options.allowed },
  cli: options.cli,
});
service.attach(manager);
const token = 'join-registry-test-token';
const server = createHttpServer({ manager, loopx: service, token, webDir: null, version: 'test' });
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
try {
  const responses = [];
  for (const body of options.requests) {
    const response = await fetch(`http://127.0.0.1:${server.address().port}/api/loopx/launch/join`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body), signal: AbortSignal.timeout(20000),
    });
    responses.push({ status: response.status, body: await response.json() });
  }
  process.stdout.write(JSON.stringify({ responses, threads, bindings: service.bindings.entries() }));
} finally {
  await new Promise((resolve) => server.close(resolve));
}
