/** TLS-only, bounded transport for the candidate task-command service. */
import {createServer, request as httpsRequest, type ServerOptions, type RequestOptions} from "node:https";
import type {AddressInfo} from "node:net";
import type {Duplex} from "node:stream";
import type {JsonObject} from "../effect_program.ts";
import {decodeRemoteAuthorityRequest, decodeRemoteAuthorityResponse, remoteAuthorityResponse,
  type RemoteAuthorityMode, type RemoteAuthorityRequest} from "./remote_authority_contract.ts";
import type {RemoteAuthorityService} from "./remote_authority_service.ts";

const MAX_REQUEST_BYTES = 16 * 1024;
const MAX_RESPONSE_BYTES = 64 * 1024;
const SHUTDOWN_SOCKET_DRAIN_MS = 5000;
export function createRemoteAuthorityServer(service: RemoteAuthorityService,
  tls: Pick<ServerOptions, "key" | "cert" | "ca">, maxInFlight = 16) {
  if (!tls.key || !tls.cert || !Number.isSafeInteger(maxInFlight) || maxInFlight < 1 || maxInFlight > 128) throw new TypeError("TLS and a bounded admission limit are required");
  let active = 0, draining = false;
  let drained: (() => void) | undefined;
  const sockets = new Set<Duplex>();
  const server = createServer({...tls, minVersion: "TLSv1.2", handshakeTimeout: 10000}, async (request, response) => {
    response.setHeader("Content-Type", "application/json");
    response.setHeader("Cache-Control", "no-store");
    const reject = (status: number, code: string) => {
      response.writeHead(status, {Connection: "close"});
      response.end(JSON.stringify({status: "rejected", reason_code: code}));
    };
    const mode = request.url === "/v0/tasks/execute" || request.url === "/v0/tasks/snapshot" ? "execute" : request.url === "/v0/tasks/recover" ? "recover" : null;
    if (request.method !== "POST" || !mode) return reject(404, "route_unavailable");
    if (draining || active >= maxInFlight) return reject(503, "remote_authority_busy");
    const authorization = request.headers.authorization;
    // No cookie authentication or browser CORS surface; credentials never enter
    // request JSON, receipts, logs or redirect targets.
    if (typeof authorization !== "string" || !/^Bearer [\x21-\x7e]{32,512}$/u.test(authorization) ||
        request.headers.origin !== undefined || request.headers["content-type"]?.split(";")[0] !== "application/json") return reject(401, "request_authentication_required");
    if (request.headers["content-encoding"] !== undefined) return reject(415, "encoded_body_unsupported");
    active++;
    let dispatched = false;
    try {
      const chunks: Buffer[] = []; let length = 0;
      for await (const chunk of request) {
        length += chunk.length;
        if (length > MAX_REQUEST_BYTES) return reject(413, "request_too_large");
        chunks.push(Buffer.from(chunk));
      }
      const raw = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      const decoded = decodeRemoteAuthorityRequest(raw);
      if (draining) return reject(503, "remote_authority_busy");
      dispatched = true;
      const result = await service.dispatch(authorization.slice(7), decoded, mode);
      const encoded = JSON.stringify(result);
      if (Buffer.byteLength(encoded) > MAX_RESPONSE_BYTES) {
        response.end(JSON.stringify(remoteAuthorityResponse(decoded, mode, {status:
          mode === "recover" || decoded.operation === "read" || decoded.operation === "snapshot" ? "unavailable" : "ambiguous",
          reason_code: "remote_authority_response_too_large"})));
      } else response.end(encoded);
    } catch {
      // Once dispatched, a broken response never establishes a rollback.
      if (dispatched) response.destroy();
      else if (!response.destroyed) reject(400, "invalid_remote_authority_request");
    } finally {active--; if (active === 0) drained?.();}
  });
  // Include sockets that have not completed the TLS handshake or HTTP headers.
  server.on("connection", socket => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  });
  server.requestTimeout = 15000;
  server.headersTimeout = 10000;
  server.keepAliveTimeout = 1000;
  server.maxConnections = maxInFlight * 2;
  return {server,
    async listen(host = "127.0.0.1", port = 0): Promise<number> {
      await new Promise<void>((resolve, reject) => {
        const onError = (error: Error) => reject(error);
        server.once("error", onError);
        server.listen(port, host, () => {server.off("error", onError); resolve();});
      });
      return (server.address() as AddressInfo).port;
    },
    async close(): Promise<void> {
      draining = true;
      const completed = active ? new Promise<void>(resolve => {drained = resolve;}) : Promise.resolve();
      // server.close() can stop Node's request timeout checker. Bound network
      // draining ourselves; a disconnected response never cancels dispatched
      // business work or permits the caller to close its database pool early.
      const deadline = setTimeout(() => {for (const socket of sockets) socket.destroy();}, SHUTDOWN_SOCKET_DRAIN_MS);
      try {
        await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      } finally {clearTimeout(deadline);}
      await completed;
    },
  };
}

/** No automatic retry, redirects, or certificate-verification bypass. Recovery
 * reuses the complete original command; missing receipt is not a new operation. */
export async function callRemoteAuthority(endpoint: string, credential: string,
  raw: RemoteAuthorityRequest, options: {mode?: RemoteAuthorityMode; ca?: RequestOptions["ca"]; timeoutMs?: number} = {}): Promise<JsonObject> {
  const request = decodeRemoteAuthorityRequest(raw), mode = options.mode ?? "execute";
  const base = new URL(endpoint);
  if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash || base.pathname !== "/") throw new TypeError("A credential-free HTTPS origin is required");
  if (!/^[\x21-\x7e]{32,512}$/u.test(credential)) throw new TypeError("Invalid service credential");
  const timeout = options.timeoutMs ?? 15000;
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 120000) throw new TypeError("Invalid request deadline");
  const uncertain = () => remoteAuthorityResponse(request, mode, {status:
    mode === "recover" || request.operation === "read" || request.operation === "snapshot" ? "unavailable" : "ambiguous",
    reason_code: "remote_authority_transport_uncertain"});
  return new Promise(resolve => {
    const body = JSON.stringify(request);
    const outgoing = httpsRequest(new URL(`/v0/tasks/${mode}`, base), {method: "POST", ca: options.ca,
      rejectUnauthorized: true, headers: {Authorization: `Bearer ${credential}`,
        "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body)}}, response => {
      const chunks: Buffer[] = []; let bytes = 0;
      response.on("data", chunk => {
        bytes += chunk.length;
        if (bytes > MAX_RESPONSE_BYTES) outgoing.destroy(); else chunks.push(Buffer.from(chunk));
      });
      response.on("error", () => resolve(uncertain()));
      response.on("end", () => {
        if (response.statusCode !== 200) {resolve(uncertain()); return;}
        try {
          resolve(decodeRemoteAuthorityResponse(JSON.parse(Buffer.concat(chunks).toString("utf8")), request, mode));
        } catch {resolve(uncertain());}
      });
    });
    // Absolute deadline also covers a peer that keeps sending partial bytes.
    const timer = setTimeout(() => outgoing.destroy(), timeout);
    outgoing.on("close", () => {clearTimeout(timer); resolve(uncertain());});
    outgoing.on("error", () => resolve(uncertain()));
    outgoing.end(body);
  });
}
