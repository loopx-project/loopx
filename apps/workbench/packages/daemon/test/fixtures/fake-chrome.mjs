#!/usr/bin/env node
// Stands in for Chrome: serves the DevTools discovery endpoints on loopback and writes
// DevToolsActivePort into the profile, as Chrome does with --remote-debugging-port=0.
import { appendFileSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { join } from 'node:path';

const args = process.argv.slice(2);
const profile = args.find(a => a.startsWith('--user-data-dir='))?.slice('--user-data-dir='.length);
const log = (entry) => appendFileSync(join(profile, '..', 'fake-chrome.log'), JSON.stringify(entry) + '\n');
// Like a second launch handed to a Chrome already using the profile: it exits without a port.
if (args.includes('--fake-exit')) { log({ exited: true }); process.exit(0); }

const server = createServer((req, res) => {
  if (req.url === '/json/version') return res.end(JSON.stringify({ Browser: 'FakeChrome/1.0' }));
  if (req.method === 'PUT' && req.url.startsWith('/json/new?')) {
    // Chrome unescapes the address it is given.
    log({ opened: decodeURIComponent(req.url.slice('/json/new?'.length)) });
    return res.end(JSON.stringify({ id: 'tab' }));
  }
  res.statusCode = 404; res.end();
});
server.listen(0, '127.0.0.1', () => {
  log({ pid: process.pid, display: process.env.DISPLAY, args,
    hubSecrets: ['CALLI_TOKEN', 'CALLI_TOKEN', 'CALLI_TOOLS_SOCKET', 'CALLI_TOOLS_KEY'].filter(k => process.env[k] !== undefined) });
  writeFileSync(join(profile, 'DevToolsActivePort'), `${server.address().port}\n/devtools/browser/fake\n`);
});
