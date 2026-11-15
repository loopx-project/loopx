import assert from "node:assert/strict";
import {createHash, randomBytes} from "node:crypto";
import {execFile, spawn, type ChildProcess} from "node:child_process";
import {promisify} from "node:util";
import {mkdtemp, readFile, writeFile, rename, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {fileURLToPath} from "node:url";
import type {TestContext} from "node:test";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";
import {Pool, type PoolConfig} from "pg";
import {installPostgreSqlAuthorityStoreSchema, PostgreSqlAuthorityStore,
  type PostgreSqlAuthorityDatabase} from "../../loopx/control_plane/coordination/postgresql_authority_store.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {REMOTE_AUTHORITY_REQUEST, type RemoteAuthorityRequest} from "../../loopx/control_plane/coordination/remote_authority_contract.ts";
import {REMOTE_AUTHORITY_POLICY} from "../../loopx/control_plane/coordination/remote_authority_policy.ts";

export const serviceIdentity = `postgresql:${"c".repeat(32)}`;
export const credentials = {a: `fixture-${"a".repeat(48)}`, b: `fixture-${"b".repeat(48)}`};
const repo = fileURLToPath(new URL("../../", import.meta.url));
const nodeFlags = ["--no-warnings", "--experimental-sqlite", "--experimental-strip-types"];
// Real clients get no inherited database URL or administrative fixture secrets.
const childEnvironment = Object.fromEntries(["PATH", "SystemRoot", "TEMP", "TMP", "TMPDIR"]
  .flatMap(key => process.env[key] === undefined ? [] : [[key, process.env[key]!]]));
export function servicePolicy(identity = serviceIdentity) {
  return {schema_version: REMOTE_AUTHORITY_POLICY,
    scopes: [{tenant_id: "tenant-one", goal_id: "goal-one", store_identity: identity, registered_agents: ["actor-a", "actor-b"]}],
    principals: ["a", "b"].map(actor => ({principal_id: `principal-${actor}`,
      credential_sha256: createHash("sha256").update(credentials[actor as "a" | "b"]).digest("hex"),
      grants: [{tenant_id: "tenant-one", goal_id: "goal-one", actor_id: `actor-${actor}`,
        operations: ["read", "claim", "progress", "renew", "release", "complete", "block", "defer", "todo_add", "snapshot"]}]}))};
}
export function claimRequest(actor: "a" | "b", todo = "todo_one", id = `claim-${todo}`): Extract<RemoteAuthorityRequest, {operation: "claim"}> {
  return {schema_version: REMOTE_AUTHORITY_REQUEST, tenant_id: "tenant-one", goal_id: "goal-one",
    actor_id: `actor-${actor}`, store_identity: serviceIdentity, todo_id: todo, operation: "claim", request_id: id};
}
export async function tlsFixture(t: TestContext) {
  const root = await mkdtemp(join(tmpdir(), "loopx-authority-tls-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const keyPath = join(root, "key.pem"), certPath = join(root, "cert.pem");
  await promisify(execFile)("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
    "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1", "-keyout", keyPath, "-out", certPath]);
  return {root, keyPath, certPath, key: await readFile(keyPath), cert: await readFile(certPath)};
}
export async function postgresRemoteFixture(t: TestContext, connectionString: string) {
  const tls = await tlsFixture(t), suffix = randomBytes(8).toString("hex");
  const name = `remote_${suffix}`, role = `remote_role_${suffix}`, password = randomBytes(24).toString("hex");
  const disconnects = new Map<Pool, Set<Promise<void>>>();
  function trackedPool(options: PoolConfig) {
    const pool = new Pool(options), pending = new Set<Promise<void>>();
    disconnects.set(pool, pending);
    pool.on("connect", client => {
      const closed = new Promise<void>(resolve => client.once("end", resolve));
      pending.add(closed); void closed.then(() => pending.delete(closed));
    });
    return pool;
  }
  async function closePool(pool?: Pool) {
    if (!pool) return;
    // Pool.end can resolve after removing clients from its bookkeeping but
    // before their sockets close. Never race database removal with that gap.
    await pool.end(); await Promise.all(disconnects.get(pool)!);
  }
  const admin = trackedPool({connectionString, max: 2});
  const processes = new Set<ChildProcess>();
  let databaseCreated = false, roleCreated = false;
  let setup: Pool | undefined, runtime: Pool | undefined;
  t.after(async () => {
    try {
      await Promise.all([...processes].map(stop));
      await closePool(runtime); await closePool(setup);
      if (databaseCreated) await admin.query(`DROP DATABASE ${name}`);
      if (roleCreated) await admin.query(`DROP ROLE ${role}`);
    } finally {await closePool(admin);}
  });
  await admin.query(`CREATE DATABASE ${name}`);
  databaseCreated = true;
  const url = new URL(connectionString); url.pathname = `/${name}`;
  setup = trackedPool({connectionString: url.href, max: 8});
  const db = (pool: Pool): PostgreSqlAuthorityDatabase => ({connect: async () => {
    const client = await pool.connect();
    return {query: (sql, args) => client.query(sql, args ? [...args] : undefined), release: error => client.release(error)};
  }});
  const database = db(setup);
  await installPostgreSqlAuthorityStoreSchema(database, serviceIdentity);
  await setup.query(`CREATE ROLE ${role} LOGIN PASSWORD '${password}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT`);
  roleCreated = true;
  await setup.query(`GRANT CONNECT ON DATABASE ${name} TO ${role}`);
  await setup.query(`GRANT USAGE ON SCHEMA loopx_control_plane TO ${role}`);
  await setup.query(`GRANT SELECT ON loopx_control_plane.authority_store_metadata TO ${role}`);
  await setup.query(`GRANT SELECT, INSERT, UPDATE ON loopx_control_plane.authority_heads TO ${role}`);
  await setup.query(`GRANT SELECT, INSERT ON loopx_control_plane.authority_commits,
    loopx_control_plane.authority_events, loopx_control_plane.authority_receipts TO ${role}`);
  const runtimeUrl = new URL(url); runtimeUrl.username = role; runtimeUrl.password = password;
  runtime = trackedPool({connectionString: runtimeUrl.href, max: 8, connectionTimeoutMillis: 2000});
  async function stop(process: ChildProcess) {
    if (process.exitCode !== null || process.signalCode !== null) return;
    await new Promise<void>(resolve => {
      const timer = setTimeout(() => process.kill("SIGKILL"), 10000);
      process.once("exit", () => {clearTimeout(timer); processes.delete(process); resolve();});
      process.kill("SIGTERM");
    });
  }
  const policyPath = join(tls.root, "policy.json");
  async function writePolicy(policy = servicePolicy()) {
    await writeFile(`${policyPath}.next`, JSON.stringify(policy), {mode: 0o600});
    await rename(`${policyPath}.next`, policyPath);
  }
  await writePolicy();
  const store = new PostgreSqlAuthorityStore(database, {tenant_id: "tenant-one", goal_id: "goal-one"});
  async function seed(ids: string[], notes: Record<string, string> = {}) {
    const prior = await store.loadAuthority();
    const projection = authorityProjectionFixture("goal-one", ids.map(todo_id => ({todo_id, role: "agent",
      status: "open", done: false, archive_state: "active", text: "Synthetic remote work", task_class: "advancement_task",
      required_write_scopes: [`${todo_id}/**`], ...(notes[todo_id] === undefined ? {} : {note: notes[todo_id]})})), [], "native", {handoff_mode: "hard_lease"});
    const result = await store.commitAuthority({operation_id: `seed-${randomBytes(8).toString("hex")}`,
      expected_provider_revision: prior.status === "loaded" ? prior.provider_revision : null,
      next_projection: projection, events: [], receipts: []});
    assert.equal(result.status, "applied");
  }
  async function start(port = 0) {
    const child = spawn(process.execPath, [...nodeFlags, "examples/coordination/remote-authority-service.ts",
      "--policy", policyPath, "--cert", tls.certPath, "--key", tls.keyPath, "--port", String(port)],
    {cwd: repo, env: {...childEnvironment, LOOPX_AUTHORITY_DATABASE_URL: runtimeUrl.href}, stdio: ["ignore", "pipe", "pipe"]});
    processes.add(child);
    const actualPort = await new Promise<number>((resolve, reject) => {
      let output = "", error = "";
      const timer = setTimeout(() => {child.kill("SIGKILL"); reject(new Error("Service startup timed out"));}, 15000);
      child.stderr.on("data", chunk => {error += chunk;});
      child.once("exit", () => {clearTimeout(timer); reject(new Error(`Service stopped before listening: ${error}`));});
      child.stdout.on("data", chunk => {
        output += chunk;
        if (output.includes("\n")) {clearTimeout(timer); resolve(JSON.parse(output.split("\n")[0]).port);}
      });
    });
    return {endpoint: `https://127.0.0.1:${actualPort}`, port: actualPort, stop: () => stop(child)};
  }
  async function client(endpoint: string, actor: "a" | "b", request: RemoteAuthorityRequest, recover = false) {
    const child = spawn(process.execPath, [...nodeFlags, "examples/coordination/remote-authority-client.ts", "--endpoint", endpoint,
      "--ca", tls.certPath, ...(recover ? ["--recover"] : [])],
    {cwd: repo, env: {...childEnvironment, LOOPX_AUTHORITY_CREDENTIAL: credentials[actor]}, stdio: ["pipe", "pipe", "pipe"]});
    processes.add(child);
    let output = "", error = "";
    child.stdout.on("data", chunk => {output += chunk;}); child.stderr.on("data", chunk => {error += chunk;});
    const result = new Promise<{code: number | null; value: JsonObject}>((resolve, reject) => {
      const timer = setTimeout(() => child.kill("SIGKILL"), 20000);
      child.once("exit", code => {
        clearTimeout(timer); processes.delete(child);
        try {resolve({code, value: JSON.parse(output)});} catch {reject(new Error(`Client did not return a result: ${error}`));}
      });
    });
    child.stdin.end(JSON.stringify(request)); return result;
  }
  return {...tls, database, runtimeDatabase: db(runtime), store, policyPath, writePolicy, seed, start, client};
}
