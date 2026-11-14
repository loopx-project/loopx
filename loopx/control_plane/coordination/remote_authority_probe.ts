/** Private stdin bridge to the same scoped TLS client used by task commands. */
import {open} from "node:fs/promises";
import {isAbsolute} from "node:path";
import {canonicalAuthorityObject} from "./authority_store_codec.ts";
import {decodeRemoteAuthorityRequest, REMOTE_AUTHORITY_REQUEST} from "./remote_authority_contract.ts";
import {callRemoteAuthority} from "./remote_authority_http.ts";

async function readCa(path: unknown): Promise<Buffer | undefined> {
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

try {
  let input = "";
  process.stdin.setEncoding("utf8");
  for await (const chunk of process.stdin) {
    input += chunk;
    if (Buffer.byteLength(input) > 16 * 1024) throw new TypeError("Invalid probe input");
  }
  const cfg = canonicalAuthorityObject(JSON.parse(input), "remote authority probe");
  if (typeof cfg.endpoint !== "string" || typeof cfg.credential !== "string") throw new TypeError("Invalid probe binding");
  const request = decodeRemoteAuthorityRequest({
    schema_version: REMOTE_AUTHORITY_REQUEST, operation: "read",
    tenant_id: cfg.tenant_id, goal_id: cfg.goal_id, actor_id: cfg.actor_id,
    store_identity: cfg.store_identity, todo_id: cfg.probe_todo_id,
  });
  const response = await callRemoteAuthority(cfg.endpoint, cfg.credential, request, {ca: await readCa(cfg.ca_file), timeoutMs: 3000});
  // A missing Todo is not proof of usable access to the expected authority.
  process.stdout.write(response.status === "found" ? "verified\n" : "unavailable\n");
} catch {
  process.stdout.write("unavailable\n");
}
