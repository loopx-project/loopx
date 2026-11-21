#!/usr/bin/env node
// Native Claude auth contract: a streamed URL, a code on stdin, then a local status check.
import { appendFileSync, existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
const directory = process.env.CLAUDE_CONFIG_DIR;
if (!directory) process.exit(2);
const credentials = join(directory, '.credentials.json');
if (process.argv[2] !== 'auth') process.exit(2);
if (process.argv[3] === 'status') {
  const loggedIn = existsSync(credentials) && process.env.FAKE_AUTH_BAD_STATUS !== '1';
  console.log(JSON.stringify({ loggedIn, ...(loggedIn ? { email: 'new@example.com' } : {}) }));
  process.exit(loggedIn ? 0 : 1);
}
if (process.argv[3] !== 'login' || !process.argv.includes('--claudeai')) process.exit(2);
if (process.env.FAKE_AUTH_PID_FILE) appendFileSync(process.env.FAKE_AUTH_PID_FILE, `${process.pid}\n`);
if (process.env.FAKE_AUTH_EXIT === '1') process.exit(1);
process.stdout.write('\u001b[32mOpening browser to sign in…\u001b[0m\nhttps://claude.com/cai/oauth/');
setTimeout(() => process.stdout.write('authorize?state=test-state&code_challenge=fixture\nPaste code here if prompted > '), 20);
const input = createInterface({ input: process.stdin });
input.on('line', code => {
  // Never print the submitted code, even on failure.
  if (code !== 'test-code#test-state') process.exit(1);
  mkdirSync(directory, { recursive: true });
  writeFileSync(credentials, JSON.stringify({ claudeAiOauth: { accessToken: 'fixture-only' } }), { mode: 0o600 });
  console.log('Login successful');
  process.exit(0);
});
