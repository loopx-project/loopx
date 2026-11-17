import {createHash, randomBytes} from "node:crypto";
import {execFileSync} from "node:child_process";
import {chmodSync, chownSync, existsSync, linkSync, mkdtempSync, readFileSync, renameSync, rmSync, writeFileSync} from "node:fs";
import {dirname, join} from "node:path";
import {REMOTE_AUTHORITY_POLICY, REMOTE_AUTHORITY_SCOPED_READ_POLICY} from "./loopx/control_plane/coordination/remote_authority_policy.ts";

const admin = "/secrets/admin";
const pgAuth = "/secrets/pg-auth";
const generationFile = join(admin, "generation.json");
const provisionedFlag = join(admin, "volumes-ready");
const installedFlag = join(admin, "database-ready");
const tenant = "local";
const goal = "collaboration";
const todo = "todo_welcome";
const actors = ["client-a", "client-b", "health", "reader"];
const bindingOperation = "compose-bind-starter-goal";
const goalRef = identity => ({goal_id: goal, goal_instance_id: `ginst_${identity.slice("postgresql:".length)}`});
const secret = () => randomBytes(32).toString("hex");
const read = path => readFileSync(path, "utf8");
const policyDigest = value => createHash("sha256").update(value).digest("hex");

function createFile(path, value, uid = 0, replace = false) {
  const temporary = mkdtempSync(join(dirname(path), ".init-"));
  const entry = join(temporary, "file");
  try {
    writeFileSync(entry, value, {mode: 0o400, flag: "wx", flush: true});
    chownSync(entry, uid, uid);
    try {
      if (replace) renameSync(entry, path);
      else linkSync(entry, path);
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
  } finally {
    rmSync(temporary, {recursive: true, force: true});
  }
}

function newGeneration() {
  const scratch = mkdtempSync("/tmp/authority-certs-");
  const openssl = (...args) => execFileSync("openssl", args, {cwd: scratch, stdio: "ignore", timeout: 15000});
  try {
    openssl("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
      "-subj", "/CN=LoopX Local CA", "-days", "3650", "-sha256",
      "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
      "-addext", "keyUsage=critical,keyCertSign,cRLSign",
      "-keyout", "ca.key", "-out", "ca.pem");
    openssl("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes",
      "-subj", "/CN=gateway", "-keyout", "server.key", "-out", "server.csr");
    writeFileSync(join(scratch, "server.ext"), "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:gateway,DNS:reader-gateway,DNS:localhost,IP:127.0.0.1\n");
    openssl("x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
      "-CAcreateserial", "-days", "397", "-sha256", "-extfile", "server.ext", "-out", "server.pem");
    return {
      identity: `postgresql:${randomBytes(16).toString("hex")}`,
      databasePassword: secret(),
      runtimePassword: secret(),
      credentials: Object.fromEntries(actors.map(actor => [actor, secret()])),
      root: read(join(scratch, "ca.pem")),
      rootKey: read(join(scratch, "ca.key")),
      key: read(join(scratch, "server.key")),
      cert: read(join(scratch, "server.pem")),
    };
  } finally {
    rmSync(scratch, {recursive: true, force: true});
  }
}

function prepareSecretVolumes() {
  const requiredFiles = {
    gateway: ["database-password", "policy.json", "cert.pem", "key.pem"],
    "reader-gateway": ["database-password", "policy.json", "cert.pem", "key.pem"],
    ...Object.fromEntries(actors.map(actor => [actor, ["credential", "ca.pem", "request.json"]])),
    "pg-auth": ["postgres-password"],
  };
  const hasDatabase = existsSync("/secrets/database/PG_VERSION");
  if (!existsSync(generationFile)) {
    const previous = hasDatabase || Object.keys(requiredFiles).some(volume => existsSync(`/secrets/${volume}/identity`));
    if (previous) throw new Error("bootstrap identity missing for existing state");
    chmodSync(admin, 0o700);
    createFile(generationFile, JSON.stringify(newGeneration()));
  }
  const generation = JSON.parse(read(generationFile));
  const {identity} = generation;
  const initialized = existsSync(provisionedFlag);
  if ((initialized && read(provisionedFlag) !== identity) || (existsSync(installedFlag) && !hasDatabase)) {
    throw new Error("mismatched bootstrap identity or missing existing database volume");
  }
  if (!initialized) {
    populateSecretVolumes(generation);
    createFile(join(admin, "ca.key"), generation.rootKey);
    createFile(join(admin, "ca.pem"), generation.root);
  }
  for (const [volume, files] of Object.entries(requiredFiles)) {
    const location = `/secrets/${volume}`;
    if (read(join(location, "identity")) !== identity || files.some(file => !existsSync(join(location, file)))) {
      throw new Error("credential volume needs restoration");
    }
  }
  createFile(provisionedFlag, identity);
  // The generation record needs plaintext copies only to resume an interrupted first start.
  if (generation.credentials) createFile(generationFile, JSON.stringify({identity}), 0, true);
}

function populateSecretVolumes({identity, credentials, root, cert, key, runtimePassword, databasePassword}) {
  const scopes = [{tenant_id: tenant, goal_id: goal, store_identity: identity, registered_agents: actors}];
  const principals = actors.filter(actor => actor !== "reader").map(actor => ({
    principal_id: `principal-${actor}`,
    credential_sha256: policyDigest(credentials[actor]),
    grants: [{
      tenant_id: tenant, goal_id: goal, actor_id: actor,
      operations: actor === "health" ? ["read"] : [
        "read", "claim", "progress", "renew", "release", "complete", ...(actor === "client-a" ? ["todo_add"] : []),
      ],
    }],
  }));
  const files = {
    gateway: {"database-password": runtimePassword, "policy.json": JSON.stringify({
      schema_version: REMOTE_AUTHORITY_POLICY, scopes, principals,
    }), "cert.pem": cert, "key.pem": key},
    "reader-gateway": {"database-password": runtimePassword, "policy.json": JSON.stringify({
      schema_version: REMOTE_AUTHORITY_SCOPED_READ_POLICY,
      scopes: [{...scopes[0], registered_agents: ["reader"], todo_read_acl: [{
        goal_ref: goalRef(identity), todo_id: todo, principal_id: "principal-reader", actor_id: "reader",
        todo_origin_operation_id: bindingOperation,
      }]}],
      principals: [{principal_id: "principal-reader", credential_sha256: policyDigest(credentials.reader),
        grants: [{tenant_id: tenant, goal_id: goal, actor_id: "reader", goal_ref: goalRef(identity),
          operations: ["read", "snapshot"]}]}],
    }), "cert.pem": cert, "key.pem": key},
    ...Object.fromEntries(actors.map(actor => [actor, {
      credential: credentials[actor], "ca.pem": root,
      "request.json": JSON.stringify({
        schema_version: "loopx_remote_authority_request_v0", tenant_id: tenant, goal_id: goal,
        store_identity: identity, actor_id: actor, todo_id: todo, operation: "read",
      }),
    }])),
    "pg-auth": {"postgres-password": databasePassword},
  };
  for (const [volume, contents] of Object.entries(files)) {
    const location = `/secrets/${volume}`;
    const owner = volume === "pg-auth" ? 0 : 1000;
    chmodSync(location, 0o700);
    chownSync(location, owner, owner);
    const marker = join(location, "identity");
    createFile(marker, identity, owner);
    if (read(marker) !== identity) throw new Error("mismatched credential volume");
    for (const [file, value] of Object.entries(contents)) createFile(join(location, file), value, owner);
  }
}

async function prepareDatabase() {
  const {Pool} = await import("pg");
  const {setupPostgreSqlAuthoritySchemaAndRole} = await import("./loopx/control_plane/coordination/postgresql_authority_seed.ts");
  const {PostgreSqlAuthorityStore} = await import("./loopx/control_plane/coordination/postgresql_authority_store.ts");
  const {coordinationTodoReadModel} = await import("./loopx/control_plane/coordination/coordination_projection.ts");
  const {canonicalTodoDomainRecord, TODO_DOMAIN_ITEM_SCHEMA, TODO_DOMAIN_READ_RECORD_SCHEMA} =
    await import("./loopx/control_plane/coordination/coordination_state_contract.ts");
  const {materializeTodoRecordForSchema} = await import("./loopx/control_plane/coordination/todo_presentation.ts");
  const {transitionGoalAcceptanceLifecycle} = await import("./loopx/control_plane/goals/acceptance_authority.ts");
  const {identity} = JSON.parse(read(generationFile));
  const runtimePassword = read("/secrets/gateway/database-password");
  const installed = existsSync(installedFlag);
  if (read(provisionedFlag) !== identity || (installed && read(installedFlag) !== identity)
    || !/^[a-f0-9]{64}$/.test(runtimePassword)) throw new Error("mismatched database initialization state");
  const opts = {host: "db", port: 5432, database: "authority", connectionTimeoutMillis: 5000, statement_timeout: 15000};
  const scopedStore = pool => new PostgreSqlAuthorityStore({connect: async () => {
    const client = await pool.connect();
    return {query: (sql, args) => client.query(sql, args ? [...args] : undefined), release: error => client.release(error)};
  }}, {tenant_id: tenant, goal_id: goal, expected_store_identity: identity});
  const setup = new Pool({...opts, user: "bootstrap", password: read(join(pgAuth, "postgres-password"))});
  const runtime = new Pool({...opts, user: "loopx_gateway", password: runtimePassword});
  try {
    if (!installed) {
      const database = {connect: async () => {
        const client = await setup.connect();
        return {query: (sql, args) => client.query(sql, args ? [...args] : undefined), release: error => client.release(error)};
      }};
      const schema = await setupPostgreSqlAuthoritySchemaAndRole({database, storeIdentity: identity, runtimeRole: "loopx_gateway"});
      if (schema.status !== "installed") throw new Error("authority schema installation failed");
      const role = await setup.query("SELECT rolcanlogin FROM pg_roles WHERE rolname = 'loopx_gateway'");
      if (!role.rows[0]?.rolcanlogin) {
        await setup.query(`ALTER ROLE loopx_gateway LOGIN PASSWORD '${runtimePassword}'`);
      }
      await setup.query("REVOKE CONNECT, TEMPORARY ON DATABASE authority FROM PUBLIC; GRANT CONNECT ON DATABASE authority TO loopx_gateway");
      const store = scopedStore(runtime);
      const before = await store.loadAuthority();
      if (before.status === "missing") {
        const record = materializeTodoRecordForSchema(canonicalTodoDomainRecord({
          schema_version: TODO_DOMAIN_ITEM_SCHEMA, todo_id: todo, role: "agent", status: "open",
          done: false, archive_state: "active", task_class: "advancement_task",
          text: "Verify the two-client handoff from claim to accepted result",
        }), TODO_DOMAIN_READ_RECORD_SCHEMA, "starter Todo");
        const todos = [record];
        const seeded = await store.commitAuthority({operation_id: "compose-initial-state", expected_provider_revision: null,
          next_projection: {goal_id: goal, handoff_mode: "hard_lease", todos, leases: [],
            todo_read_model: coordinationTodoReadModel(todos, TODO_DOMAIN_READ_RECORD_SCHEMA)}, events: [], receipts: []});
        if (seeded.status !== "applied") throw new Error("initial task could not be committed");
      }
      const bound = await transitionGoalAcceptanceLifecycle(store, {
        goal_id: goal, actor_agent_id: null, operation_id: bindingOperation,
        transition: {kind: "bind_existing", goal_ref: goalRef(identity)},
      });
      if (bound.status !== "applied" && bound.status !== "replayed") throw new Error("starter Goal could not be bound");
    }
    const after = await scopedStore(runtime).loadAuthority();
    if (after.status !== "loaded") throw new Error("initialized database is unavailable");
    createFile(installedFlag, identity);
  } finally {
    await Promise.all([setup.end(), runtime.end()]);
  }
}

const phase = process.argv[2];
try {
  if (phase === "secrets") prepareSecretVolumes();
  else if (phase === "database") await prepareDatabase();
  else throw new Error("unknown bootstrap command");
  process.stdout.write(`LoopX ${phase} ready\n`);
} catch (error) {
  // Database/crypto errors may reveal credentials. Expose the phase and stable code only.
  console.error(`LoopX ${phase} failed (${error.code ?? "init_state"})`);
  process.exitCode = 1;
}
