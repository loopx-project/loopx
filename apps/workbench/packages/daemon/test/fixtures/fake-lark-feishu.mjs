#!/usr/bin/env node
// Scriptable lark-cli boundary: no network, credentials, or real bot configuration.
import { appendFileSync, readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const args = process.argv.slice(2);
const config = () => JSON.parse(readFileSync(process.env.FAKE_LARK_CONFIG, 'utf8'));
// Creation names a new profile; selecting that profile before it exists would fail.
const creating = args.join(' ') === 'config init --new --name calli';
const command = creating ? args : args.slice(2);
const loggingIn = command.join(' ') === 'auth login --domain im';
const streaming = command[0] === 'event' || command[0] === 'config' || loggingIn;
const input = streaming ? '' : readFileSync(0, 'utf8');
const calls = () => readFileSync(process.env.FAKE_LARK_LOG, 'utf8').split('\n').filter(Boolean).map(line => JSON.parse(line));
appendFileSync(process.env.FAKE_LARK_LOG, JSON.stringify({ args, input, pid: process.pid, credentials: Object.keys(process.env).filter(key => key.startsWith('CALLI_')) }) + '\n');
const output = value => process.stdout.write(JSON.stringify(value) + '\n');
const fail = () => { process.stderr.write('untrusted CLI diagnostics must not leak\n'); process.exitCode = 1; };
const c = config();
if (!creating && (args[0] !== '--profile' || args[1] !== 'calli')) {
  process.stderr.write('expected --profile calli before command\n'); process.exitCode = 2;
} else if (command[0] === 'auth' && command[1] === 'status') {
  if (c.authFail) fail();
  else if (c.authTimeout) setTimeout(() => process.exit(0), 30_000);
  else output({ appId: c.configured === false ? '' : 'cli_fixture_app', identities: {
    bot: { available: c.ready !== false, verified: c.verified !== false },
    user: { available: c.userAvailable !== false, openId: c.openId ?? 'ou_owner' },
  } });
} else if (loggingIn) {
  process.stdout.write('Open https://example.invalid/authorize?fixture=login to confirm your identity.\n');
  const finish = () => {
    const latest = config();
    if (latest.loginExit) { fail(); process.exitCode = latest.loginExit; return; }
    const user = { userAvailable: latest.loginNoUser !== true, openId: latest.loginOpenId ?? latest.openId ?? 'ou_owner' };
    writeFileSync(process.env.FAKE_LARK_CONFIG, JSON.stringify({ ...latest, ...user }));
    output(user);
  };
  if (c.loginWait) {
    const timer = setInterval(() => { if (config().loginFinish) { clearInterval(timer); finish(); } }, 20);
  } else setTimeout(finish, c.loginDelayMs ?? 250);
} else if (command.join(' ') === 'config init --new --name calli') {
  process.stdout.write('Open https://example.invalid/authorize?fixture=calli to create the bot.\n');
  const finish = () => {
    const latest = config();
    if (latest.setupExit) { fail(); return; }
    writeFileSync(process.env.FAKE_LARK_CONFIG, JSON.stringify({ ...latest, configured: true }));
    output({ configured: true });
  };
  if (c.setupWait) {
    const timer = setInterval(() => { if (config().setupFinish) { clearInterval(timer); finish(); } }, 20);
  } else setTimeout(finish, c.setupDelayMs ?? 250);
} else if (command.slice(0, 3).join(' ') === 'event consume im.message.receive_v1') {
  if (command.includes('--dry-run')) {
    const preconditions = [
      { name: 'console_event_published', status: c.mode === 'console-off' ? 'blocked' : 'ok' },
      { name: 'credentials_available', status: c.mode === 'unavailable' ? 'missing' : 'ok' },
    ];
    output({ data: { decision: { status: preconditions.every(row => row.status === 'ok') ? 'ready' : 'blocked', preconditions } } });
  } else if (c.mode === 'busy-elsewhere') {
    process.stderr.write('another event bus is already connected\n'); process.exitCode = 1;
  } else {
    process.stderr.write('ready\n');
    let offset = 0;
    // Appending NDJSON lets tests deliver a reply after observing the first outbound message.
    setInterval(() => {
      const script = readFileSync(process.env.FAKE_LARK_EVENTS, 'utf8');
      const end = script.lastIndexOf('\n') + 1;
      if (end > offset) { process.stdout.write(script.slice(offset, end)); offset = end; }
    }, 15);
  }
} else if (command[0] === 'im' && ['+messages-reply', '+messages-send'].includes(command[1])) {
  const attempts = calls().filter(call => call.args[3] === command[1]).length;
  if (c.replyTimeout) setTimeout(() => process.exit(0), 30_000);
  else if (c.replyFail || attempts <= (c.replyFailures ?? 0)) fail();
  else {
    const key = command[command.indexOf('--idempotency-key') + 1];
    output({ data: { message_id: `om_${createHash('sha1').update(key).digest('hex').slice(0, 20)}` } });
  }
} else if (command.slice(0, 3).join(' ') === 'im reactions create') {
  output({ data: { reaction_id: 'reaction_fixture' } });
} else {
  process.stderr.write('unsupported fixture command\n'); process.exitCode = 2;
}
