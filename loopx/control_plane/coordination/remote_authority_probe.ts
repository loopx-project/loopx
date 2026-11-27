/** Private stdin bridge to the same scoped TLS client used by task commands. */
import {canonicalAuthorityObject} from "./authority_store_codec.ts";
import {decodeRemoteAuthorityRequest, REMOTE_AUTHORITY_REQUEST} from "./remote_authority_contract.ts";
import {callRemoteAuthority} from "./remote_authority_http.ts";
import {inspectRemoteAuthority, readRemoteAuthorityCa, remoteAuthorityInspectionResult} from "./remote_authority_inspection.ts";

const inspect = process.argv[2] === "--inspect";

try {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    input += chunk;
    if (Buffer.byteLength(input) > 16 * 1024) throw new TypeError("Invalid probe input");
  }
  if (inspect) {
    process.stdout.write(JSON.stringify(await inspectRemoteAuthority(JSON.parse(input))) + "\n");
  } else {
    const cfg = canonicalAuthorityObject(JSON.parse(input), "remote authority probe");
    if (typeof cfg.endpoint !== "string" || typeof cfg.credential !== "string") throw new TypeError("Invalid probe binding");
    const request = decodeRemoteAuthorityRequest({
      schema_version: REMOTE_AUTHORITY_REQUEST, operation: "read",
      tenant_id: cfg.tenant_id, goal_id: cfg.goal_id, actor_id: cfg.actor_id,
      store_identity: cfg.store_identity, todo_id: cfg.probe_todo_id,
    });
    const response = await callRemoteAuthority(cfg.endpoint, cfg.credential, request, {ca: await readRemoteAuthorityCa(cfg.ca_file), timeoutMs: 3000});

    process.stdout.write(response.status === "found" ? "verified\n" : "unavailable\n");
  }
} catch {
  process.stdout.write(inspect ? JSON.stringify(remoteAuthorityInspectionResult("binding_invalid")) + "\n" : "unavailable\n");
}
