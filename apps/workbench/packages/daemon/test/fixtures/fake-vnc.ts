import { createServer, type Server } from 'node:net';
import type { AddressInfo } from 'node:net';

/** A pretend VNC server: says its RFB version first, as a real one does, then echoes what it gets. */
export async function fakeVnc(banner = 'RFB 003.008\n'): Promise<{ server: Server; port: number; close: () => Promise<void> }> {
  const server = createServer((socket) => {
    socket.write(banner);
    socket.on('data', (chunk) => socket.write(chunk));
    socket.on('error', () => undefined);
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  return {
    server,
    port: (server.address() as AddressInfo).port,
    close: () => new Promise((r) => server.close(() => r())),
  };
}
