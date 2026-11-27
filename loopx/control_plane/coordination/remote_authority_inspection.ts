/** Read-only operator inspection. No remote content or execution authority escapes. */
import {open} from "node:fs/promises";
import {isAbsolute} from "node:path";
import type {JsonObject} from "../effect_program.ts";
import {canonicalAuthorityObject} from "./authority_store_codec.ts";
import {decodeRemoteAuthorityRequest, REMOTE_AUTHORITY_REQUEST} from "./remote_authority_contract.ts";
import {observeRemoteAuthorityCall, remoteAuthorityOrigin, validateRemoteAuthorityCredential} from "./remote_authority_http.ts";

const diagnostics = {
  goal_unregistered: ["configuration", "The selected Goal is not registered.", "Select an existing registered Goal."],
  actor_unregistered: ["configuration", "The selected agent is not registered for this Goal.", "Select an agent registered for this Goal."],
  disabled: ["configuration", "Remote authority is not enabled for this Goal.", "Inspect the selected Goal's explicit remote_authority binding."],
  binding_incomplete: ["configuration", "The remote authority binding is incomplete.", "Check endpoint, tenant_id, store_identity, actor_id and probe_todo_id in the selected Goal."],
  binding_invalid: ["configuration", "The remote authority inspection input is invalid.", "Check the selected Goal's identifiers and binding format."],
  actor_mismatch: ["configuration", "The selected agent does not match the bound remote actor.", "Select the agent named by this Goal's remote binding."],
  endpoint_invalid: ["configuration", "The endpoint is not a credential-free HTTPS origin.", "Use an HTTPS origin without a path, query, fragment or embedded credentials."],
  credential_required: ["configuration", "No service credential is available.", "Set LOOPX_AUTHORITY_CREDENTIAL or LOOPX_REMOTE_AUTHORITY_CREDENTIAL in the environment."],
  credential_invalid: ["configuration", "The service credential has an invalid format.", "Check the configured environment credential; never put it in command arguments."],
  ca_invalid: ["configuration", "The configured CA file cannot be read safely.", "Use an absolute path to a readable regular CA file of 1 to 65536 bytes."],
  dns: ["transport", "The service hostname could not be resolved.", "Check the configured hostname and local DNS connectivity."],
  connection_refused: ["transport", "The service refused the connection.", "Check the configured port and whether the service is listening."],
  tls: ["transport", "The TLS connection could not be verified or established.", "Check the server certificate, hostname, validity and configured CA; keep verification enabled."],
  connection: ["transport", "The connection ended without a verified response.", "Check network and service availability before inspecting again."],
  timeout: ["transport", "The read exceeded its deadline.", "Check network and service responsiveness before inspecting again."],
  http: ["transport", "The service returned a non-success HTTP status.", "Check service and proxy configuration; an HTTP status alone does not verify identity or scope."],
  response_invalid: ["protocol", "The response did not match the read contract and request.", "Check client/service compatibility and request correlation."],
  response_too_large: ["protocol", "The response exceeded the bounded read limit.", "Check that the endpoint serves the remote task protocol."],
  unauthenticated: ["authority", "The service rejected the credential.", "Check credential validity with the service operator."],
  scope_denied: ["authority", "The service denied the requested scope.", "Check the credential's tenant, Goal, actor and store permissions with the service operator."],
  rejected: ["authority", "The service rejected the read.", "Check service-side policy and the selected binding with the service operator."],
  todo_missing: ["read", "The configured probe Todo was not found.", "Check probe_todo_id and the selected scope; a missing Todo does not verify readiness."],
  read_unverified: ["read", "The service did not verify the configured read.", "Check service availability and the selected binding."],
  read_verified: ["read", "The configured Todo read was verified. Execution is not admitted.", "Use the existing execution admission and lease workflow before running work."],
} as const;

export function remoteAuthorityInspectionResult(reason: keyof typeof diagnostics, httpStatus?: number): JsonObject {
  const [stage, message, recommended_action] = diagnostics[reason];
  const ok = reason === "read_verified";
  return {schema_version: "loopx_remote_authority_inspection_v1", ok,
    status: ok ? "verified" : "unavailable", stage, reason_code: `remote_authority_${reason}`,
    message, recommended_action, execution_admitted: false,
    ...(httpStatus === undefined ? {} : {http_status: httpStatus})};
}

export async function readRemoteAuthorityCa(path: unknown): Promise<Buffer | undefined> {
  if (path === null || path === undefined) return undefined;
  if (typeof path !== "string" || !isAbsolute(path)) throw new TypeError("Invalid CA path");
  const file = await open(path, "r");
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size === 0 || info.size > 64 * 1024) throw new TypeError("Invalid CA file");
    const bytes = Buffer.alloc(info.size);
    if ((await file.read(bytes, 0, bytes.length, 0)).bytesRead !== bytes.length) throw new TypeError("Incomplete CA file");
    return bytes;
  } finally {await file.close();}
}

/** Only the private bridge supplies registration evidence. Caller configuration
 * cannot select an operation, bypass TLS verification or request a retry. */
export async function inspectRemoteAuthority(raw: unknown): Promise<JsonObject> {
  let input: JsonObject, cfg: JsonObject;
  try {
    input = canonicalAuthorityObject(raw, "inspection input");
    if (input.goal_registered !== true) return remoteAuthorityInspectionResult("goal_unregistered");
    cfg = canonicalAuthorityObject(input.config ?? {}, "remote authority binding");
  } catch {return remoteAuthorityInspectionResult("binding_invalid");}
  if (cfg.enabled !== true) return remoteAuthorityInspectionResult("disabled");
  if (!["endpoint", "tenant_id", "store_identity", "actor_id", "probe_todo_id"].every(
    key => typeof cfg[key] === "string" && cfg[key].trim())) return remoteAuthorityInspectionResult("binding_incomplete");
  if (input.actor_registered !== true) return remoteAuthorityInspectionResult("actor_unregistered");
  if (input.agent_id !== cfg.actor_id) return remoteAuthorityInspectionResult("actor_mismatch");
  const endpoint = cfg.endpoint as string;
  try {remoteAuthorityOrigin(endpoint);} catch {return remoteAuthorityInspectionResult("endpoint_invalid");}
  if (!input.credential) return remoteAuthorityInspectionResult("credential_required");
  if (typeof input.credential !== "string") return remoteAuthorityInspectionResult("credential_invalid");
  try {validateRemoteAuthorityCredential(input.credential);} catch {return remoteAuthorityInspectionResult("credential_invalid");}
  let request;
  try {
    request = decodeRemoteAuthorityRequest({schema_version: REMOTE_AUTHORITY_REQUEST, operation: "read",
      tenant_id: cfg.tenant_id, goal_id: input.goal_id, actor_id: cfg.actor_id,
      store_identity: cfg.store_identity, todo_id: cfg.probe_todo_id});
  } catch {return remoteAuthorityInspectionResult("binding_invalid");}
  let ca;
  try {ca = await readRemoteAuthorityCa(cfg.ca_file);} catch {return remoteAuthorityInspectionResult("ca_invalid");}
  try {
    const {response, transport} = await observeRemoteAuthorityCall(endpoint, input.credential, request, {ca, timeoutMs: 3000});
    if (transport) return remoteAuthorityInspectionResult(transport.kind, transport.httpStatus);
    if (response.status === "found") return remoteAuthorityInspectionResult("read_verified");
    if (response.status === "missing") return remoteAuthorityInspectionResult("todo_missing");
    if (response.status === "rejected") {
      if (response.reason_code === "principal_unauthenticated") return remoteAuthorityInspectionResult("unauthenticated");
      if (response.reason_code === "remote_authority_scope_denied") return remoteAuthorityInspectionResult("scope_denied");
      return remoteAuthorityInspectionResult("rejected");
    }
    return remoteAuthorityInspectionResult("read_unverified");
  } catch {return remoteAuthorityInspectionResult("read_unverified");}
}
