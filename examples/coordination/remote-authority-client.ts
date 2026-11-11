/** One request, no automatic retries. Read commands from stdin, credentials from env. */
import {readFile} from "node:fs/promises";
import {parseArgs} from "node:util";
import {callRemoteAuthority} from "../../loopx/control_plane/coordination/remote_authority_http.ts";
import {decodeRemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
const {values} = parseArgs({options: {endpoint: {type: "string"}, ca: {type: "string"}, recover: {type: "boolean"}}});
if (!values.endpoint || !process.env.LOOPX_AUTHORITY_CREDENTIAL) throw new Error("Provide an HTTPS endpoint and service credential");
let raw = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) {
  raw += chunk;
  if (Buffer.byteLength(raw) > 16 * 1024) throw new Error("Request is too large");
}
const result = await callRemoteAuthority(values.endpoint, process.env.LOOPX_AUTHORITY_CREDENTIAL,
  decodeRemoteAuthorityRequest(JSON.parse(raw)), {mode: values.recover ? "recover" : "execute",
    ...(values.ca ? {ca: await readFile(values.ca)} : {})});
process.stdout.write(`${JSON.stringify(result)}\n`);
if (!["found", "missing", "result"].includes(String(result.status)) ||
    result.status === "result" && !["applied", "no_change", "replayed", "recovered"].includes(String((result.result as Record<string, unknown>)?.status))) process.exitCode = 1;
