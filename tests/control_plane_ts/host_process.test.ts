import assert from "node:assert/strict";
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
    // cooperative child. Its marker is the semantic oracle, not a PID lookup.
    const child = `const fs=require('fs');let n=0;process.on('SIGTERM',()=>{});
      fs.writeFileSync(${JSON.stringify(marker)},String(n));
      setInterval(()=>fs.writeFileSync(${JSON.stringify(marker)},String(++n)),10)`;
    const script = `const{spawn}=require('child_process');const fs=require('fs');
      spawn(process.execPath,['-e',${JSON.stringify(child)}],{stdio:${JSON.stringify(mode === "closed_pipes" ? "ignore" : "inherit")}});
      const timer=setInterval(()=>{if(fs.existsSync(${JSON.stringify(marker)})){
        clearInterval(timer);process.stdout.write('ready\\n');
        ${mode === "leader_exit" || mode === "closed_pipes" ? "process.exit(0)" : "setInterval(()=>{},1000)"}
      }},5)`;
    const controller = new AbortController();
    const result = await runHostProcess(request(script, {timeout_ms: mode === "timeout" ? 500 : 3000}), async item => {
      if (mode === "abort" && item.text.includes("ready")) controller.abort();
    }, controller.signal);
    assert.equal(result.outcome, mode === "abort" ? "cancelled" : mode === "timeout" ? "timeout" : "exited");
    assert.equal(result.cleanup_scope, "process_group"); assert.equal(result.group_signal_sent, true);
    const counter = await readFile(marker, "utf8"); await delay(100);
    assert.equal(await readFile(marker, "utf8"), counter, "child kept changing state after return");
    if (mode === "leader_exit") assert.equal(result.output_complete, false);
  });
}

test("output consumer failure cancels execution rather than leaving an orphan", async () => {
  const result = await runHostProcess(request(`setInterval(()=>process.stdout.write('tick\\n'),10)`),
    async () => { throw new Error("consumer left"); });
  assert.equal(result.outcome, "cancelled"); assert.equal(result.output_complete, false);
});
