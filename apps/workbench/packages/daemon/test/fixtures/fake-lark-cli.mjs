#!/usr/bin/env node
import { appendFileSync, readFileSync } from 'node:fs';
const args = process.argv.slice(2);
const config = JSON.parse(readFileSync(process.env.FAKE_LARK_CONFIG, 'utf8'));
const input = readFileSync(0, 'utf8');
appendFileSync(process.env.FAKE_LARK_LOG, JSON.stringify({ args, input, credentials: Object.keys(process.env).filter(k => k === 'CALLI_TOKEN' || k.startsWith('CALLI_')) }) + '\n');
if (args[0] === 'auth') {
  process.stdout.write(JSON.stringify({ identities: { bot: { available: config.ready !== false }, user: { available: true, openId: 'test-recipient' } } }));
} else if (config.timeout) {
  setTimeout(() => process.exit(0), 30000);
} else if (config.fail) {
  process.stderr.write('untrusted stderr must not leak'); process.exitCode = 1;
} else process.stdout.write('{}');
