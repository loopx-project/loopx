import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";
import {decodeHostProcessRequest, runHostProcess, type HostProcessRequest} from "../../loopx/control_plane/turn_driver/host_process.ts";
import {decodeDelegatedHostLease, runLeasedHostProcess} from "../../loopx/control_plane/turn_driver/leased_host_process.ts";
import {FileAuthorityStore} from "../../loopx/control_plane/coordination/file_authority_store.ts";
import {executeCoordinationTodoClaim} from "../../loopx/control_plane/coordination/todo_claim.ts";
import {prepareCoordinationProjectionCommit} from "../../loopx/control_plane/coordination/coordination_projection.ts";
import {authorityProjectionFixture} from "./authority_projection_fixture.ts";
import {productionScaleLeaseAcquisitionFixture} from "./production_scale_coordination_fixture.ts";
import type {JsonObject} from "../../loopx/control_plane/effect_program.ts";

const request = (script: string, overrides: Partial<HostProcessRequest> = {}): HostProcessRequest => ({
  argv: [process.execPath, "-e", script], cwd: process.cwd(), input: "request\n",
  timeout_ms: 3000, drain_timeout_ms: 50, stdout_limit_bytes: 12000, ...overrides,
});

test("Host output is streamed with UTF-8 boundaries and stdin EOF", async () => {
  let stdout = "", stderr = "";
  const result = await runHostProcess(request(`
    let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
      const b=Buffer.from('界');process.stdout.write(b.subarray(0,1));
      setTimeout(()=>{process.stdout.write(b.subarray(1));process.stderr.write(input)},5)
    });`), async item => { if (item.kind === "stdout") stdout += item.text; else stderr += item.text; });
  assert.equal(stdout, "界"); assert.equal(stderr, "request\n");
  assert.equal(result.outcome, "exited"); assert.equal(result.returncode, 0); assert.equal(result.output_complete, true);
});

test("null execution deadline waits for natural completion", async () => {
  const value = decodeHostProcessRequest(request(
    "setTimeout(()=>process.stdout.write('finished'),150)", {timeout_ms: null}));
  let text = "";
  const result = await runHostProcess(value, async item => { text += item.text; });
  assert.equal(text, "finished");
  assert.equal(result.outcome, "exited");
  assert.equal(result.returncode, 0);
});

test("invalid requests and absent executables cannot be mistaken for success", async () => {
  for (const change of [{argv: []}, {argv: [""]}, {timeout_ms: Infinity}, {timeout_ms: 0}, {stdout_limit_bytes: -1}, {drain_timeout_ms: -1}, {unexpected: true}]) {
    assert.throws(() => decodeHostProcessRequest({...request(""), ...change}), /invalid/);
  }
  const result = await runHostProcess(request("", {argv: ["/missing/loopx-test-host"]}), async () => {});
  assert.equal(result.outcome, "spawn_failed"); assert.equal(result.returncode, null);
});

test("stdout limit cancels a still-running Host before collecting the full stream", async () => {
  let received = 0;
  const result = await runHostProcess(request(`setInterval(()=>process.stdout.write('x'.repeat(20000)),1)`,
    {stdout_limit_bytes: 1000}), async item => { received += item.text.length; });
  assert.equal(result.outcome, "output_limit"); assert.ok(received <= 1000); assert.equal(result.output_complete, false);
});

test("abort before start performs no invocation", async () => {
  const controller = new AbortController(); controller.abort();
  const result = await runHostProcess(request("throw new Error('must not start')"), async () => assert.fail(), controller.signal);
  assert.equal(result.outcome, "cancelled"); assert.equal(result.returncode, null);
});

async function delegatedCompletionWaitFixture(t: test.TestContext, ttlSeconds: number,
  waitKind: "completion" | "future_date" = "completion") {
  const root = await mkdtemp(join(tmpdir(), "loopx-host-dependency-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const goal = "goal-a", storePath = join(root, "authority");
  const store = new FileAuthorityStore(storePath, goal);
  const fixture = productionScaleLeaseAcquisitionFixture(goal, "native");
  const projection = authorityProjectionFixture(goal, (fixture.projection.todos as JsonObject[]).map(row =>
    row.todo_id === fixture.target ? {...row, required_write_scopes: ["lease-admission/**"]} : row),
    fixture.projection.leases as JsonObject[], "native", {handoff_mode: "hard_lease"});
  assert.equal((await store.commitAuthority({operation_id: "seed-host-dependency", expected_provider_revision: null,
    next_projection: projection, events: [], receipts: []})).status, "applied");
  const claimRequest = {goal_id: goal, todo_id: fixture.target, claimed_by: "agent-a", actor_agent_id: "agent-a",
    expected_role: "agent" as const, registered_agents: fixture.registered_agents,
    operation_id: "host-claim-acquire", lease_request: {idempotency_key: "host-execution", expected_version: 0,
      ttl_seconds: ttlSeconds}, dry_run: false};
  const first = await executeCoordinationTodoClaim(store, {...claimRequest, now: new Date()});
  assert.equal(first.status, "applied", JSON.stringify(first));
  const requestPath = join(root, "claim.json");
  const renewalResultPath = join(root, "renewal-result.json");
  await writeFile(requestPath, JSON.stringify({...claimRequest, prerequisite_id: fixture.acquisition.conflict_todo_id,
    renewal_result_path: renewalResultPath}));
  const scriptPath = join(root, "lease-cli.mjs");
  const claimUrl = new URL("../../loopx/control_plane/coordination/todo_claim.ts", import.meta.url).href;
  const lifecycleUrl = new URL("../../loopx/control_plane/coordination/task_lease_lifecycle.ts", import.meta.url).href;
  const storeUrl = new URL("../../loopx/control_plane/coordination/file_authority_store.ts", import.meta.url).href;
  const projectionUrl = new URL("../../loopx/control_plane/coordination/coordination_projection.ts", import.meta.url).href;
  await writeFile(scriptPath, `import {readFileSync, writeFileSync} from "node:fs";
import {FileAuthorityStore} from ${JSON.stringify(storeUrl)};
import {executeCoordinationTodoClaim} from ${JSON.stringify(claimUrl)};
import {executeCanonicalTaskLeaseLifecycle} from ${JSON.stringify(lifecycleUrl)};
import {prepareCoordinationProjectionCommit} from ${JSON.stringify(projectionUrl)};
const [mode, storePath, requestPath, ...flags] = process.argv.slice(2);
const request = JSON.parse(readFileSync(requestPath, "utf8"));
const store = new FileAuthorityStore(storePath, request.goal_id);
if (mode === "renew") {
  const head = await store.loadAuthority();
  if (head.status !== "loaded") throw Error("missing authority");
  const todo = head.head.todos.find(row => row.todo_id === request.todo_id);
  const changed = await store.commitAuthority(prepareCoordinationProjectionCommit({
    goal_id: request.goal_id, operation_id: "host-renewal-add-wait",
    expected_provider_revision: head.provider_revision, projection: head.head,
    mutations: [{kind: "todo_upsert", todo: {...todo, resume_when: "todo_done:" + request.prerequisite_id}}]}));
  if (changed.status !== "applied") throw Error("wait mutation failed");
  const version = Number(flags[flags.indexOf("--expected-version") + 1]);
  const ttl = Number(flags[flags.indexOf("--ttl-seconds") + 1]);
  const result = await executeCanonicalTaskLeaseLifecycle(store, {operation: "renew", goal_id: request.goal_id,
    todo_id: request.todo_id, owner: request.claimed_by, idempotency_key: request.lease_request.idempotency_key,
    expected_version: version, ttl_seconds: ttl, registered_agents: request.registered_agents, now: new Date()});
  if (result.status !== "applied") writeFileSync(request.renewal_result_path,
    JSON.stringify({status: result.status, reason_code: result.reason_code}));
  process.stdout.write(JSON.stringify({ok: result.status === "applied", lease: result.lease, reason_code: result.reason_code}));
} else {
  const result = await executeCoordinationTodoClaim(store, {...request, prerequisite_id: undefined,
    now: new Date()});
  process.stdout.write(JSON.stringify({ok: ["applied", "replayed", "recovered", "no_change"].includes(result.status),
    lease: result.lease, reason_code: result.reason_code}));
}`);
  const command = (mode: "read" | "renew") => [process.execPath, "--no-warnings", "--experimental-strip-types",
    scriptPath, mode, storePath, requestPath];
  const lease = decodeDelegatedHostLease({lease: first.lease,
    read_argv: command("read"), renew_argv: command("renew"), ttl_seconds: ttlSeconds});
  const addWait = async () => {
    const head = await store.loadAuthority();
    assert.equal(head.status, "loaded");
    if (head.status !== "loaded") throw Error("missing authority");
    const todo = (head.head.todos as JsonObject[]).find(row => row.todo_id === fixture.target)!;
    assert.equal((await store.commitAuthority(prepareCoordinationProjectionCommit({goal_id: goal,
      operation_id: "host-initial-add-wait", expected_provider_revision: head.provider_revision,
      projection: head.head, mutations: [{kind: "todo_upsert", todo: {...todo,
        resume_when: waitKind === "completion" ? `todo_done:${fixture.acquisition.conflict_todo_id}`
          : "resume_at:2099-01-01T00:00:00Z"}}]}))).status, "applied");
  };
  return {root, store, lease, addWait, renewalResultPath};
}

for (const waitKind of ["completion", "future_date"] as const) test(`pending canonical ${waitKind} wait rejects delegated Host before spawn`, async t => {
  const {root, lease, addWait} = await delegatedCompletionWaitFixture(t, 30, waitKind);
  await addWait();
  const marker = join(root, "host-started");
  let spawned = 0;
  const result = await runLeasedHostProcess(request(`require('fs').writeFileSync(${JSON.stringify(marker)},'started')`),
    lease, async () => {}, new AbortController().signal, async () => {spawned++;});
  assert.equal(result.outcome, "cancelled");
  assert.deepEqual(result.lease_failure, {reason: "execution_proof_rejected", boundary: "initial_proof"});
  assert.equal(spawned, 0);
  assert.equal(existsSync(marker), false);
});

test("completion wait appearing during renewal cancels delegated Host", async t => {
  const {root, store, lease, renewalResultPath} = await delegatedCompletionWaitFixture(t, 12);
  const leaseExpiryMs = Date.parse(lease.lease.expires_at);
  const marker = join(root, "host-heartbeat");
  let spawned = 0;
  let hostPid: number | null = null;
  const owner = new AbortController();
  t.after(() => owner.abort());
  const running = runLeasedHostProcess(request(`const fs=require('fs');let n=0;
    setInterval(()=>fs.writeFileSync(${JSON.stringify(marker)},String(++n)),20)`, {timeout_ms: 16_000}),
    lease, async () => {}, owner.signal, async item => {spawned++; hostPid = item.pid;});
  const startDeadline = Date.now() + 2_000;
  while (!existsSync(marker) && Date.now() < startDeadline) await delay(10);
  assert.equal(existsSync(marker), true, "delegated Host did not start its heartbeat");
  const renewalDeadline = Math.min(Date.now() + 9_000, leaseExpiryMs - 500);
  while (!existsSync(renewalResultPath) && Date.now() < renewalDeadline) await delay(10);
  assert.equal(existsSync(renewalResultPath), true, "renewal rejection was not observed");
  const stopDeadline = Math.min(Date.now() + 4_000, leaseExpiryMs - 500);
  const hostIsRunning = (pid: number) => {
    try { process.kill(pid, 0); return true; }
    catch (error) { if ((error as {code?: string}).code === "ESRCH") return false; throw error; }
  };
  while (hostPid !== null && hostIsRunning(hostPid) && Date.now() < stopDeadline) await delay(10);
  assert.ok(hostPid !== null && !hostIsRunning(hostPid),
    "Host did not stop promptly after renewal rejection, before the lease deadline");
  assert.ok(Date.now() < leaseExpiryMs,
    "Host stopped at the lease deadline instead of after renewal rejection");
  const result = await running;
  assert.equal(spawned, 1);
  assert.equal(result.outcome, "cancelled");
  assert.deepEqual(result.lease_failure, {reason: "renewal_rejected", boundary: "renewal"});
  const heartbeat = await readFile(marker, "utf8");
  await delay(120);
  assert.equal(await readFile(marker, "utf8"), heartbeat, "Host continued after renewal rejection");
  const head = await store.loadAuthority();
  assert.equal(head.status, "loaded");
  if (head.status === "loaded") assert.equal((head.head.todos as JsonObject[]).some(row =>
    row.todo_id === lease.lease.todo_id && row.resume_when !== undefined), true);
});

for (const mode of ["timeout", "abort", "leader_exit", "closed_pipes"] as const) {
  test(`${mode}: descendants cannot keep working after managed execution returns`, {skip: process.platform === "win32"}, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-host-group-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const marker = join(root, "counter");
    // Ignore TERM so the test proves escalation and does not merely observe a
    // cooperative child. Publish the marker atomically: a kill during a write
    // must not look like a surviving child, but each completed tick stays visible.
    const child = `const fs=require('fs');let n=0;process.on('SIGTERM',()=>{});
      const marker=${JSON.stringify(marker)}, staged=marker+'.next';
      const publish=()=>{fs.writeFileSync(staged,String(n));fs.renameSync(staged,marker)};
      publish();setInterval(()=>{n++;publish()},10)`;
    const script = `const{spawn}=require('child_process');const fs=require('fs');
      spawn(process.execPath,['-e',${JSON.stringify(child)}],{stdio:${JSON.stringify(mode === "closed_pipes" ? "ignore" : "inherit")}});
      const timer=setInterval(()=>{if(fs.existsSync(${JSON.stringify(marker)})){
        clearInterval(timer);process.stdout.write('ready\\n');
        ${mode === "leader_exit" || mode === "closed_pipes" ? "process.exit(0)" : "setInterval(()=>{},1000)"}
      }},5)`;
    const controller = new AbortController();
    let expire: (() => void) | undefined;
    let ready = false;
    if (mode === "timeout") {
      // Control only the supervisor deadline, not real process IO or cleanup.
      // This case proves drain of a known-ready descendant. A separate real
      // 500ms case below covers expiry before readiness, without assuming IO.
      const timer = globalThis.setTimeout;
      t.mock.method(globalThis, "setTimeout", (callback: () => void, ms: number) => {
        if (ms !== 500) return timer(callback, ms);
        expire = callback;
        return timer(() => { controller.abort(); }, 3000); // fixture readiness watchdog
      });
    }
    const result = await runHostProcess(request(script, {timeout_ms: mode === "timeout" ? 500 : mode === "abort" ? null : 3000}), async item => {
      if (item.text.includes("ready")) {
        ready = true;
        if (mode === "abort") controller.abort();
        if (mode === "timeout") { assert.ok(expire); expire(); }
      }
    }, controller.signal);
    assert.equal(ready, true, "descendant readiness was not established");
    assert.equal(result.outcome, mode === "abort" ? "cancelled" : mode === "timeout" ? "timeout" : "exited");
    assert.equal(result.cleanup_scope, "process_group"); assert.equal(result.group_signal_sent, true);
    const counter = await readFile(marker, "utf8"); await delay(100);
    assert.equal(await readFile(marker, "utf8"), counter, "child kept changing state after return");
    if (mode === "leader_exit") assert.equal(result.output_complete, false);
  });
}

test("real timeout before readiness prevents later Host effects", {skip: process.platform === "win32"}, async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-host-pre-ready-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const marker = join(root, "late-effect");
  const result = await runHostProcess(request(
    `setTimeout(()=>require('fs').writeFileSync(${JSON.stringify(marker)},'unexpected'),900)`,
    {timeout_ms: 500}), async () => {});
  assert.equal(result.outcome, "timeout");
  assert.equal(result.cleanup_scope, "process_group");
  assert.equal(result.group_signal_sent, true);
  assert.equal(existsSync(marker), false);
  await delay(900);
  assert.equal(existsSync(marker), false, "Host performed an effect after timeout returned");
});

test("output consumer failure cancels execution rather than leaving an orphan", async () => {
  const result = await runHostProcess(request(`setInterval(()=>process.stdout.write('tick\\n'),10)`),
    async () => { throw new Error("consumer left"); });
  assert.equal(result.outcome, "cancelled"); assert.equal(result.output_complete, false);
});

test("closed pipes do not turn asynchronous KILL delivery into completed cleanup", {skip: process.platform === "win32"}, async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-host-kill-fence-"));
  const marker = join(root, "counter");
  const descendant = `const fs=require('fs');process.on('SIGTERM',()=>{});let n=0;
    const publish=()=>{fs.writeFileSync(${JSON.stringify(marker + ".next")},String(n++));
      fs.renameSync(${JSON.stringify(marker + ".next")},${JSON.stringify(marker)})};
    publish();setInterval(publish,10)`;
  const leader = `const{spawn}=require('child_process');const fs=require('fs');
    spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:'ignore'});
    const timer=setInterval(()=>{if(fs.existsSync(${JSON.stringify(marker)})){
      clearInterval(timer);process.stdout.write('ready');process.exit(0)}},5)`;
  const kill = process.kill.bind(process);
  let killDelivered = false;
  let scheduled: Promise<void> | undefined;
  t.mock.method(process, "kill", (pid: number, signal?: NodeJS.Signals | number) => {
    if (pid < 0 && signal === "SIGKILL") {
      // Model the kernel's asynchronous signal delivery deterministically.
      // The old supervisor returns before this delivery and the marker changes.
      scheduled ??= delay(100).then(() => {
        try { kill(pid, "SIGKILL"); }
        catch (error) { if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error; }
        killDelivered = true;
      });
      return true;
    }
    return kill(pid, signal);
  });
  t.after(async () => { await scheduled; await rm(root, {recursive: true, force: true}); });
  const result = await runHostProcess(request(leader), async () => {});
  assert.equal(result.outcome, "exited");
  assert.equal(killDelivered, true, "returned before KILL had stopped the group");
  const counter = await readFile(marker, "utf8");
  await delay(100);
  assert.equal(await readFile(marker, "utf8"), counter);
});

test("the spawned Host group is reported once before input, and an unrecorded group never runs", {skip: process.platform === "win32"}, async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-host-spawned-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const seen: unknown[] = [];
  let stdout = "";
  const result = await runHostProcess(request(`process.stdout.write(String(process.pid)+' '+String(require('child_process').execSync('ps -o pgid= -p '+process.pid)).trim())`),
    async item => { stdout += item.text; }, undefined, undefined, {spawned: async item => { seen.push(item); }});
  const [pid, pgid] = stdout.split(" ").map(Number);
  assert.equal(result.outcome, "exited");
  assert.deepEqual(seen, [{kind: "spawned", pid, process_group: pid}]); assert.equal(pgid, pid);
  // A caller that cannot record the owned group runs nothing unaccounted for, and
  // the armed host proves it really started, so this is not an unspawned process.
  const script = (marker: string) => `require('fs').writeFileSync(${JSON.stringify(marker)},'')
    process.stdin.on('data',()=>{});setInterval(()=>{},1000)`;
  const recorded = join(root, "recorded");
  const refused = await runHostProcess(request(script(recorded)), async () => {}, undefined, undefined, {spawned: async () => {
    const until = Date.now() + 2000;
    while (!existsSync(recorded) && Date.now() < until) await delay(5);
    assert.ok(existsSync(recorded), "the Host never started");
    throw new Error("record unavailable"); }});
  assert.equal(refused.outcome, "cancelled"); assert.equal(refused.output_complete, false);
});
