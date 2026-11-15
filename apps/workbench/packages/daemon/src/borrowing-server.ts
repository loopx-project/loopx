import { createReadStream } from 'node:fs';
import { createServer, type ServerResponse } from 'node:http';
import type { BorrowingService } from './borrowing.js';
import { HttpError } from './errors.js';

export function sendToolArtifact(service: BorrowingService, owner: string, job: string, artifact: string, res: ServerResponse): void {
  const file = service.artifact(owner, job, artifact);
  const stream = createReadStream(file.path);
  stream.on('error', () => {
    if (res.headersSent) res.destroy();
    else res.writeHead(404, { 'Content-Type': 'application/json', 'Cache-Control': 'private, no-store' })
      .end(JSON.stringify({ error: 'artifact file unavailable' }));
  });
  stream.on('open', () => {
    res.writeHead(200, { 'Content-Type': file.mimeType, 'Content-Length': file.size,
      'Content-Disposition': `attachment; filename="${file.name}"`, 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store' });
    stream.pipe(res);
  });
}

/** Thread-scoped credentials can only invoke tools and read their own jobs. */
export function borrowingServer(service: BorrowingService) {
  return createServer(async (req, res) => {
    try {
      const owner = service.caller((req.headers.authorization ?? '').replace(/^Bearer /, ''));
      const artifact = /^\/artifacts\/([^/]+)\/([^/]+)$/.exec(req.url ?? '');
      if (req.method === 'GET' && artifact) return sendToolArtifact(service, owner, artifact[1], artifact[2], res);
      if (req.method !== 'POST' || req.url !== '/command') throw new HttpError(404, 'not found');
      const chunks: Buffer[] = []; let size = 0;
      for await (const chunk of req) {
        size += chunk.length;
        if (size > 64000) throw new HttpError(413, 'command too large');
        chunks.push(chunk);
      }
      const value = await service.command(owner, JSON.parse(Buffer.concat(chunks).toString()));
      res.writeHead(200, { 'Content-Type': 'application/json' }).end(JSON.stringify(value));
    } catch (err) {
      res.writeHead(err instanceof HttpError ? err.status : 400, { 'Content-Type': 'application/json' })
        .end(JSON.stringify({ error: err instanceof Error ? err.message : String(err) }));
    }
  });
}
