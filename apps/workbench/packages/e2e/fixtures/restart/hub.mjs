#!/usr/bin/env node
// A stand-in for a Calli hub in restart.sh tests: a tiny HTTP server speaking just enough for the
// curl fake. State files live in the directory given as the second argument, so every test runs
// against its own:
//   threads-active.json   served for /api/threads. A leading "+" plays a scripted sequence: the
//                         first line is this poll's answer, the rest becomes the file.
//   mode                  "active" (always serve threads-active.json) or "idle" (always "[]")
//   health.json           served for /api/health
import { createServer } from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const port = Number(process.argv[2]);
const stateDir = process.argv[3];

const active = () => {
  let text = readFileSync(join(stateDir, 'threads-active.json'), 'utf8');
  if (text.startsWith('+')) {
    const newline = text.indexOf('\n');
    const now = newline === -1 ? text.slice(1) : text.slice(1, newline);
    const rest = newline === -1 ? '' : text.slice(newline + 1);
    writeFileSync(join(stateDir, 'threads-active.json'), rest);
    text = now;
  }
  return text;
};

const server = createServer((req, res) => {
  const body = req.url.includes('health')
    ? readFileSync(join(stateDir, 'health.json'), 'utf8')
    : req.url.includes('archived') || readFileSync(join(stateDir, 'mode'), 'utf8').trim() === 'idle'
      ? '[]'
      : active();
  res.writeHead(200, { 'Content-Length': Buffer.byteLength(body) });
  res.end(body);
});
server.on('error', (error) => {
  console.error(error.message);
  process.exit(1);
});
server.listen(port, '127.0.0.1');
