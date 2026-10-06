import assert from "node:assert/strict";
import {existsSync} from "node:fs";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {join} from "node:path";
import {tmpdir} from "node:os";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";
import {decodeHostProcessRequest, runHostProcess, type HostProcessRequest} from "../../loopx/control_plane/turn_driver/host_process.ts";

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
