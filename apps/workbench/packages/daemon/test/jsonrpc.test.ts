import { test } from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough, Writable } from 'node:stream';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { JsonRpcPeer } from '../src/adapters/jsonrpc.js';

for (const completion of ['result', 'error'] as const) {
  test(`JSON-RPC drops a late incoming handler ${completion} after process exit`, async () => {
    const writes: string[] = [];
    const child = Object.assign(new EventEmitter(), {
      stdout: new PassThrough(), stderr: new PassThrough(),
      stdin: new Writable({ write(chunk, _encoding, callback) { writes.push(String(chunk)); callback(); } }),
      kill: () => true,
    });
    let resolve!: (value: unknown) => void;
    let reject!: (error: Error) => void;
    let requested = false;
    const answer = new Promise<unknown>((yes, no) => { resolve = yes; reject = no; });
    const peer = new JsonRpcPeer(child as unknown as ChildProcessWithoutNullStreams, {
      onNotification: () => undefined,
      onRequest: () => { requested = true; return answer; },
    }, 'fixture');
    const prompt = peer.request('session/prompt', { sessionId: 'fixture' });
    const failed = assert.rejects(prompt, /fixture exited/);
    child.stdout.write(JSON.stringify({ jsonrpc: '2.0', id: 1000, method: 'session/request_permission', params: {} }) + '\n');
    assert.equal(requested, true);
    child.emit('exit', 9, null);
    await failed;
    assert.equal(peer.isClosed, true);
    const writesAtExit = writes.length;
    if (completion === 'result') resolve({ outcome: { outcome: 'cancelled' } });
    else reject(new Error('permission handler ended after exit'));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(writes.length, writesAtExit, 'no response or error is written to closed agent stdin');
    child.stdout.destroy();
    child.stderr.destroy();
    child.stdin.destroy();
  });
}

test('JSON-RPC survives a broken stdin pipe before the exit event', async () => {
  // The agent dies at the OS level first: writes to its stdin fail with EPIPE while the
  // child's 'exit' event has not been delivered yet. That must close the peer, not the hub.
  let closed = false;
  const child = Object.assign(new EventEmitter(), {
    stdout: new PassThrough(), stderr: new PassThrough(),
    stdin: new Writable({
      write(_chunk, _encoding, callback) {
        const e = Object.assign(new Error('write EPIPE'), { code: 'EPIPE', errno: -32, syscall: 'write' }) as Error & { code: string };
        this.emit('error', e);
        callback(e);
      },
    }),
    kill: () => true,
  });
  const peer = new JsonRpcPeer(child as unknown as ChildProcessWithoutNullStreams, {
    onNotification: () => undefined,
    onRequest: () => undefined,
    onClosed: () => { closed = true; },
  }, 'fixture');
  const prompt = peer.request('session/prompt', { sessionId: 'fixture' });
  await assert.rejects(prompt, /EPIPE/);
  assert.equal(peer.isClosed, true);
  assert.equal(closed, true);
  peer.notify('session/cancel', { sessionId: 'fixture' }); // quiet on a closed peer
  child.emit('exit', 9, null); // late exit must not throw either
  child.stdout.destroy();
  child.stderr.destroy();
  child.stdin.destroy();
});
