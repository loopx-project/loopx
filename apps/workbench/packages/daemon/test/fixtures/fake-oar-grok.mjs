#!/usr/bin/env node
// A native ACP process, independent of the workbench bridge and OAR's scripted runtime.
import { createInterface } from 'node:readline';
if (process.argv.includes('--version')) { console.log('grok 1.0.89'); process.exit(0); }
if (process.argv.includes('--help')) { console.log('Usage: grok agent stdio'); process.exit(0); }
const out = value => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', ...value }) + '\n');
const result = (id, value) => out({ id, result: value });
const sessionId = 'public-fixture-session';
const update = value => out({ method: 'session/update', params: { sessionId, update: value } });
let pending;
createInterface({ input: process.stdin }).on('line', line => {
  const message = JSON.parse(line);
  switch (message.method) {
    case 'initialize': result(message.id, { protocolVersion: 1, agentCapabilities: { loadSession: true, promptCapabilities: { image: true } }, agentInfo: { name: 'grok', version: '1.0.89' }, authMethods: [] }); break;
    case 'session/new': result(message.id, { sessionId }); break;
    case '_x.ai/models/list': result(message.id, { currentModelId: 'grok-fixture', availableModels: [{ modelId: 'grok-fixture', name: 'Grok fixture' }] }); break;
    case 'session/load': result(message.id, {}); break;
    case 'session/set_config_option': result(message.id, { configOptions: [] }); break;
    case 'session/prompt': {
      const text = message.params.prompt.filter(p => p.type === 'text').map(p => p.text).join('').split('\n')[0];
      if (text === 'wait') { pending = message.id; break; }
      if (text === 'crash') { process.exit(9); }
      update({ sessionUpdate: 'agent_thought_chunk', content: { type: 'text', text: 'Checking the fixture' } });
      update({ sessionUpdate: 'tool_call', toolCallId: 'read-1', title: 'Read README', kind: 'read', status: 'in_progress', rawInput: { path: 'README.md' } });
      update({ sessionUpdate: 'tool_call_update', toolCallId: 'read-1', status: 'completed', content: [{ type: 'content', content: { type: 'text', text: 'fixture content' } }] });
      update({ sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Native ACP answer: ' + text.split('\n')[0] } });
      result(message.id, { stopReason: 'end_turn' });
      break;
    }
    case 'session/cancel': if (pending !== undefined) { result(pending, { stopReason: 'cancelled' }); pending = undefined; } break;
    default: if (message.id !== undefined) result(message.id, {});
  }
});
