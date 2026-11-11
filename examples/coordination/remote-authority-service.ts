/** Explicit candidate launcher; never changes local provider selection or data. */
import {readFile} from "node:fs/promises";
import {parseArgs} from "node:util";
import {Pool} from "pg";
import {RemoteAuthorityService} from "../../loopx/control_plane/coordination/remote_authority_service.ts";
import {fileRemoteAuthorityAdmission, readRemoteAuthorityPolicy} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";
import {createRemoteAuthorityServer} from "../../loopx/control_plane/coordination/remote_authority_http.ts";

const {values} = parseArgs({options: {policy: {type: "string"}, cert: {type: "string"}, key: {type: "string"},
  host: {type: "string", default: "127.0.0.1"}, port: {type: "string", default: "0"}}});
const port = Number(values.port);
if (!values.policy || !values.cert || !values.key || !Number.isSafeInteger(port) || port < 0 || port > 65535 ||
    !process.env.LOOPX_AUTHORITY_DATABASE_URL) throw new Error("Provide policy, TLS certificate/key and service-owned database configuration");
// Validate before listening; updates are read again for each admission.
await readRemoteAuthorityPolicy(values.policy);
const pool = new Pool({connectionString: process.env.LOOPX_AUTHORITY_DATABASE_URL,
  max: 8, connectionTimeoutMillis: 2000, query_timeout: 6000, idleTimeoutMillis: 10000,
  statement_timeout: 5000, lock_timeout: 2000, idle_in_transaction_session_timeout: 10000});
pool.on("error", () => process.stderr.write("Authority database connection unavailable\n"));
const service = new RemoteAuthorityService({admit: fileRemoteAuthorityAdmission(values.policy),
  database: {connect: async () => {
    const connection = await pool.connect();
    return {query: (sql, args) => connection.query(sql, args ? [...args] : undefined),
      release: error => connection.release(error)};
  }}});
const listener = createRemoteAuthorityServer(service, {cert: await readFile(values.cert), key: await readFile(values.key)});
try {
  const actualPort = await listener.listen(values.host, port);
  process.stdout.write(`${JSON.stringify({status: "listening", host: values.host, port: actualPort})}\n`);
} catch {
  await pool.end();
  throw new Error("Authority service listener unavailable");
}
let stopping = false;
async function stop() {
  if (stopping) return;
  stopping = true;
  await listener.close();
  await pool.end();
}
process.on("SIGINT", () => {void stop();});
process.on("SIGTERM", () => {void stop();});
