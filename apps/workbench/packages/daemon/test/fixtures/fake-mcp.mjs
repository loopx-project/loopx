#!/usr/bin/env node
// A stdio MCP server small enough to stand in for the vision server in adapter tests: it answers
// initialize and tools/list, ignores notifications, and can be told to die on startup.
import process from 'node:process';

if (process.env.FAKE_MCP_FAIL) process.exit(3);

let buffer = '';
const reply = (id, result) => process.stdout.write(JSON.stringify({ jsonrpc: '2.0', id, result }) + '\n');

process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => {
  buffer += chunk;
  let index;
  while ((index = buffer.indexOf('\n')) >= 0) {
    const line = buffer.slice(0, index).trim();
    buffer = buffer.slice(index + 1);
    if (!line) continue;
    let message;
    try { message = JSON.parse(line); } catch { continue; }
    if (message.id === undefined) continue; // notification
    if (message.method === 'initialize') {
      reply(message.id, { protocolVersion: '2024-11-05', capabilities: {}, serverInfo: { name: 'fake-mcp', version: '0.0.1' } });
    } else if (message.method === 'tools/list') {
      reply(message.id, { tools: [
        { name: 'analyze_image', description: 'Describe an image', inputSchema: { type: 'object' } },
        { name: 'ping', description: 'Reply pong', inputSchema: { type: 'object' } },
      ] });
    } else {
      reply(message.id, {});
    }
  }
});
