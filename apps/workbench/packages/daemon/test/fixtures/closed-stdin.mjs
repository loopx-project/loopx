// A backend that completes its handshake but cannot receive any later requests.
import { closeSync, writeFileSync } from 'node:fs';

writeFileSync(process.env.CLOSED_STDIN_PID, String(process.pid));
process.stdin.once('data', (chunk) => {
  const request = JSON.parse(chunk.toString().trim());
  process.stdin.pause();
  closeSync(0);
  const response = request.method === 'initialize'
    ? { jsonrpc: '2.0', id: request.id, result: { protocolVersion: '2024-11-05', capabilities: {}, serverInfo: { name: 'closed', version: '1' } } }
    : { type: 'response', id: request.id, command: request.type, success: true, data: { sessionId: 'closed-stdin' } };
  process.stdout.write(JSON.stringify(response) + '\n');
});
setInterval(() => {}, 1000);
