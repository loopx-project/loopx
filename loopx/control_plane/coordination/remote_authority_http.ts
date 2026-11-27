/** Outbound TLS-only, bounded client transport for the remote task-command wire contract. */
import {request as httpsRequest, type RequestOptions} from "node:https";
import type {JsonObject} from "../effect_program.ts";
import {decodeRemoteAuthorityRequest, decodeRemoteAuthorityResponse, remoteAuthorityResponse,
  type RemoteAuthorityMode, type RemoteAuthorityRequest} from "./remote_authority_contract.ts";

const MAX_RESPONSE_BYTES = 64 * 1024;
export interface RemoteAuthorityCallOptions {
  mode?: RemoteAuthorityMode;
  ca?: RequestOptions["ca"];
  timeoutMs?: number;
}

/** No automatic retry, redirects, or certificate-verification bypass. Recovery
 * reuses the complete original command; missing receipt is not a new operation. */
export async function callRemoteAuthority(endpoint: string, credential: string,
  raw: RemoteAuthorityRequest, options: RemoteAuthorityCallOptions = {}): Promise<JsonObject> {
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
