#!/usr/bin/env node
// A stand-in for curl in restart.sh tests. It talks to the hub fake over plain TCP, so the
// connection behaves like a real one, including being refused when the hub is down.
//
//   curl-fake -sf -m 5 -H "Authorization: ..." http://127.0.0.1:PORT/api/threads[?archived=1]
//
// // The `mode` file (in $CALLI_FAKE_STATE, shared with the hub fake) drives it:
//   down       exit 7 before connecting (nothing is listening; a stopped hub)
//   downonce   exit 7 once, then behave normally
//   flaky      drop the connection (exit 56) when a `fail-next` file exists, then remove it
//   up         normal: forward the request, print the body
import { connect } from 'node:net';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const state = process.env.CALLI_FAKE_STATE ?? here;
const url = process.argv.at(-1);
const mode = readFileSync(join(state, 'mode'), 'utf8').trim();

if (mode === 'down' || mode === 'downonce') {
  if (mode === 'downonce') writeFileSync(join(state, 'mode'), 'up');
  console.error('curl-fake: connection refused (down)');
  process.exit(7);
}

const failNext = join(state, 'fail-next');
const drop = mode === 'flaky' && exists(failNext);
if (drop) rmSync(failNext);

const { port, pathname, search } = new URL(url);
const socket = connect({ port: Number(port), host: '127.0.0.1' });
socket.on('error', (error) => {
  if (error.code === 'ECONNREFUSED') process.exit(7);
  if (drop) process.exit(56); // the scripted drop
  console.error(error.message);
  process.exit(56);
});
if (drop) socket.on('connect', () => socket.destroy()); // hang up right away
socket.on('connect', () => socket.end(`GET ${pathname}${search} HTTP/1.1\r\nHost: fake\r\n\r\n`));
let body = '';
socket.on('data', (chunk) => {
  body += chunk.toString('utf8');
  const split = body.indexOf('\r\n\r\n');
  if (split !== -1) {
    socket.destroy();
    process.stdout.write(body.slice(split + 4));
    process.exit(0);
  }
});
socket.on('close', () => process.exit(body ? 0 : 56));

function exists(path) {
  try {
    readFileSync(path);
    return true;
  } catch {
    return false;
  }
}
