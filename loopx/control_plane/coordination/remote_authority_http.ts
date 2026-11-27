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

export type RemoteAuthorityTransportFailure = "dns" | "connection_refused" | "tls" | "connection"
  | "timeout" | "http" | "response_invalid" | "response_too_large";
export interface RemoteAuthorityCallObservation {
  response: JsonObject;
  transport: {kind: RemoteAuthorityTransportFailure; httpStatus?: number} | null;
}

export function remoteAuthorityOrigin(endpoint: string): URL {
  const base = new URL(endpoint);
  if (base.protocol !== "https:" || base.username || base.password || base.search || base.hash || base.pathname !== "/") throw new TypeError("A credential-free HTTPS origin is required");
  return base;
}

export function validateRemoteAuthorityCredential(credential: string): void {
  if (!/^[\x21-\x7e]{32,512}$/u.test(credential)) throw new TypeError("Invalid service credential");
}

function connectionFailure(error: NodeJS.ErrnoException): RemoteAuthorityTransportFailure {
  const code = error.code ?? "";
  if (code === "ENOTFOUND" || code === "EAI_AGAIN") return "dns";
  if (code === "ECONNREFUSED") return "connection_refused";
  if (/^(ERR_TLS_|ERR_SSL_|ERR_OSSL_|CERT_|DEPTH_ZERO_SELF_SIGNED_CERT|SELF_SIGNED_CERT_IN_CHAIN|UNABLE_TO_)/u.test(code)) return "tls";
  return "connection";
}

/** No automatic retry, redirects, or certificate-verification bypass. Recovery
 * reuses the complete original command; missing receipt is not a new operation. */
export async function callRemoteAuthority(endpoint: string, credential: string,
  raw: RemoteAuthorityRequest, options: RemoteAuthorityCallOptions = {}): Promise<JsonObject> {
  return (await observeRemoteAuthorityCall(endpoint, credential, raw, options)).response;
}

/** Local transport evidence is separate from the wire outcome. In particular,
 * transport failures never prove that a mutating command did not commit. */
export async function observeRemoteAuthorityCall(endpoint: string, credential: string,
  raw: RemoteAuthorityRequest, options: RemoteAuthorityCallOptions = {}): Promise<RemoteAuthorityCallObservation> {
  const request = decodeRemoteAuthorityRequest(raw), mode = options.mode ?? "execute";
  const base = remoteAuthorityOrigin(endpoint);
  validateRemoteAuthorityCredential(credential);
  const timeout = options.timeoutMs ?? 15000;
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 120000) throw new TypeError("Invalid request deadline");
  const uncertain = () => remoteAuthorityResponse(request, mode, {status:
    mode === "recover" || request.operation === "read" || request.operation === "snapshot" ? "unavailable" : "ambiguous",
    reason_code: "remote_authority_transport_uncertain"});
  return new Promise(resolve => {
    let settled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const finish = (result: RemoteAuthorityCallObservation) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(result);
    };
    const fail = (kind: RemoteAuthorityTransportFailure, httpStatus?: number) =>
      finish({response: uncertain(), transport: {kind, ...(httpStatus === undefined ? {} : {httpStatus})}});
    const body = JSON.stringify(request);
    const outgoing = httpsRequest(new URL(`/v0/tasks/${mode}`, base), {method: "POST", ca: options.ca,
      rejectUnauthorized: true, headers: {Authorization: `Bearer ${credential}`,
        "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body)}}, response => {
      if (response.statusCode !== 200) {
        fail("http", response.statusCode);
        response.destroy();
        outgoing.destroy();
        return;
      }
      const chunks: Buffer[] = []; let bytes = 0;
      response.on("data", chunk => {
        bytes += chunk.length;
        if (bytes > MAX_RESPONSE_BYTES) {fail("response_too_large"); outgoing.destroy();}
        else chunks.push(Buffer.from(chunk));
      });
      response.on("error", () => fail("connection"));
      response.on("end", () => {
        try {
          finish({response: decodeRemoteAuthorityResponse(JSON.parse(Buffer.concat(chunks).toString("utf8")), request, mode), transport: null});
        } catch {fail("response_invalid");}
      });
    });
    // Absolute deadline also covers a peer that keeps sending partial bytes.
    timer = setTimeout(() => {fail("timeout"); outgoing.destroy();}, timeout);
    outgoing.on("close", () => fail("connection"));
    outgoing.on("error", error => fail(connectionFailure(error)));
    outgoing.end(body);
  });
}
