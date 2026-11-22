import { test } from 'node:test';
import assert from 'node:assert/strict';
import { hostPath } from '../src/api';
import { hostProblem } from '../src/hosts';

test('on another machine, a path of the API goes through the hub to the same path there', () => {
  assert.equal(hostPath('devbox', '/api/threads'), '/api/hosts/devbox/proxy/threads');
  assert.equal(hostPath('devbox', '/api/threads/t1/stream?after=42'), '/api/hosts/devbox/proxy/threads/t1/stream?after=42');
  assert.equal(hostPath('devbox', '/api/directories?path=%2Fsrv%2Fa%20b'), '/api/hosts/devbox/proxy/directories?path=%2Fsrv%2Fa%20b', 'the query is left as it was');
  assert.equal(hostPath('a/b', '/api/config'), '/api/hosts/a%2Fb/proxy/config', 'an id cannot reach outside its own segment');
  // Only the API moves: the page itself is always the hub's.
  assert.equal(hostPath('devbox', '/'), '/');
});

test('each way a machine can fail says what to do about it, with the name to type', () => {
  assert.match(hostProblem('host_key', 'devbox'), /运行一次 ssh devbox/);
  assert.match(hostProblem('auth', 'me@devbox'), /ssh me@devbox 能直接进去/);
  assert.match(hostProblem('daemon_missing', 'devbox'), /Calli 没有应答/);
  assert.match(hostProblem('unreachable', 'devbox'), /连不到/);
  // Something ssh said that the hub did not recognise still gets a sentence; ssh's own words go next to it.
  assert.equal(hostProblem('ssh_failed', 'devbox'), 'ssh 没连上。');
  assert.equal(hostProblem(undefined, 'devbox'), 'ssh 没连上。');
});
