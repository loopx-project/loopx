#!/usr/bin/env node
// Minimal native voice protocol fixture; never connects to an upstream service.
import { createInterface } from 'node:readline';
import { appendFileSync } from 'node:fs';

const scenario = process.env.FAKE_VOICE_SCENARIO ?? 'quiet';
const threadId = 'voice-probe-thread';
const send = value => process.stdout.write(JSON.stringify(value) + '\n');
const event = (method, params = {}) => send({ method, params: { threadId, ...params } });
const lines = createInterface({ input: process.stdin });
lines.on('close', () => process.exit(0));
lines.on('line', line => {
  const message = JSON.parse(line);
  if (process.env.FAKE_VOICE_LOG) appendFileSync(process.env.FAKE_VOICE_LOG, JSON.stringify(message) + '\n');
  const reply = result => send({ id: message.id, result });
  switch (message.method) {
    case 'initialize':
      if (scenario !== 'timeout') reply({});
      break;
    case 'account/read':
      reply({ account: { type: scenario === 'key' ? 'apiKey' : 'chatgpt', email: 'SECRET_ACCOUNT', planType: 'test' } });
      break;
    case 'thread/realtime/listVoices':
      reply({ voices: { v1: ['one'], v2: ['two', 'three'] } });
      break;
    case 'thread/start':
      reply({ thread: { id: threadId, ephemeral: true } });
      break;
    case 'thread/realtime/start': {
      if (scenario === 'exit') return process.exit(3);
      const fail = () => event('thread/realtime/error', { message: 'realtime conversation requires API key auth SECRET_TOKEN https://private.invalid/account' });
      if (scenario === 'early-error') fail();
      reply({});
      if (scenario === 'late-error') setTimeout(fail, 10);
      if (scenario === 'started') event('thread/realtime/started', { realtimeSessionId: 'SECRET_SESSION_ID', version: 'v3' });
      if (scenario === 'tool') send({ id: 'tool-request', method: 'item/commandExecution/requestApproval', params: { threadId, command: 'must-not-run' } });
      break;
    }
    case 'thread/realtime/stop':
      reply({}); event('thread/realtime/closed');
      break;
  }
});
