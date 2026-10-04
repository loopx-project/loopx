import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {mkdtemp, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createInterface} from "node:readline";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";

const bridge = new URL("../../loopx/control_plane/collaboration/delegation_preview_bridge.ts", import.meta.url);

function transport(script: string, cwd = process.cwd(), lifetimeClock?: number) {
  const clock = lifetimeClock === undefined ? [] : ["--import", "data:text/javascript," + encodeURIComponent(
    `const original=globalThis.setTimeout;globalThis.setTimeout=(f,ms,...a)=>original(f,ms===300000?${lifetimeClock}:ms,...a)`)];
  const child = spawn(process.execPath, [...clock, "--no-warnings", "--experimental-strip-types", bridge.pathname],
    {stdio: ["pipe", "pipe", "pipe"]});
  const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
  let id = 0;
  const send = (value: unknown) => child.stdin.write(JSON.stringify(value) + "\n");
  const read = async () => {
    const line = await lines.next(); assert.equal(line.done, false);
    return JSON.parse(line.value!);
  };
  const exited = new Promise<number | null>(resolve => child.once("exit", resolve));
  const ready = async () => {
    send({kind: "start", request: {argv: [process.execPath, "-e", script], cwd,
      input: "", timeout_ms: 10000, drain_timeout_ms: 50, stdout_limit_bytes: 100000}});
    assert.deepEqual(await read(), {kind: "ready"});
  };
  const request = (argv: string[], timeout_ms = 3000) => {
    send({kind: "request", id: ++id, argv, timeout_ms}); return read();
  };
  return {child, ready, request, read, send, exited, lines};
}

const echo = `const readline=require('readline');let n=0;
  readline.createInterface({input:process.stdin}).on('line',line=>{
    const r=JSON.parse(line);process.stdout.write(JSON.stringify({kind:'preview',id:r.id,
      returncode:0,value:{n:++n,argv:r.argv,cwd:process.cwd(),pid:process.pid}})+'\\n')})`;

test("one fixed-cwd worker serves sequential fresh requests and parent EOF stops it", async () => {
  const session = transport(echo); await session.ready();
  const first = await session.request(["first"]);
  const second = await session.request(["second"]);
  assert.equal(first.value.pid, second.value.pid);
  assert.equal(first.value.n, 1); assert.equal(second.value.n, 2);
  assert.deepEqual(second.value.argv, ["second"]); assert.equal(second.value.cwd, process.cwd());
  session.child.stdin.end(); assert.equal(await session.exited, 0);
});

for (const mode of ["timeout", "owner_eof", "owner_term", "worker_crash", "invalid_json", "output_limit"] as const) {
  test(`${mode} waits for descendant cleanup, never leaves late writes`, {skip: process.platform === "win32"}, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-preview-cleanup-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const marker = join(root, "counter");
    const descendant = `const fs=require('fs');process.on('SIGTERM',()=>{});let n=0;
      setInterval(()=>{fs.writeFileSync(${JSON.stringify(marker + ".next")},String(n++));
        fs.renameSync(${JSON.stringify(marker + ".next")},${JSON.stringify(marker)})},5)`;
    const worker = `const{spawn}=require('child_process');const fs=require('fs');
      const child=spawn(process.execPath,['-e',${JSON.stringify(descendant)}],{stdio:'ignore'});
      process.stdin.on('data',()=>{const wait=setInterval(()=>{if(fs.existsSync(${JSON.stringify(marker)})){
        clearInterval(wait); ${mode === "worker_crash" ? "process.exit(3)" : mode === "invalid_json"
          ? "process.stdout.write('not-json\\n')" : mode === "output_limit"
            ? "process.stdout.write('x'.repeat(1100000))" : "setInterval(()=>{},1000)"}
      }},5)});`;
    const session = transport(worker, root); await session.ready();
    const response = session.request(["wait"], mode === "timeout" ? 300 : 3000);
    // EOF/signal are real transport cancellation, not mocked AbortSignals.
    while (true) { try { await readFile(marker); break; } catch { await delay(10); } }
    if (mode === "owner_eof") session.child.stdin.end();
    if (mode === "owner_term") session.child.kill("SIGTERM");
    const result = await response;
    assert.equal(result.kind, "failure");
    if (mode === "timeout") assert.equal(result.outcome, "timeout");
    const before = await readFile(marker, "utf8"); await delay(80);
    assert.equal(await readFile(marker, "utf8"), before, "descendant still executing after failure");
    await session.exited;
  });
}

test("two bridges keep their workspaces and request counters separate", async t => {
  const root = await mkdtemp(join(tmpdir(), "loopx-preview-partition-"));
  t.after(() => rm(root, {recursive: true, force: true}));
  const first = transport(echo), second = transport(echo, root);
  await Promise.all([first.ready(), second.ready()]);
  const [a, b] = await Promise.all([first.request(["a"]), second.request(["b"])]);
  assert.notEqual(a.value.pid, b.value.pid); assert.notEqual(a.value.cwd, b.value.cwd);
  assert.equal(a.value.n, 1); assert.equal(b.value.n, 1);
  first.child.stdin.end(); second.child.stdin.end(); await Promise.all([first.exited, second.exited]);
});

test("the bounded session retires after its 128th response", async () => {
  const session = transport(echo); await session.ready();
  for (let index = 1; index <= 128; index++) {
    const response = await session.request([String(index)]);
    assert.equal(response.value.n, index);
  }
  assert.equal(await session.exited, 0);
});

test("lifetime expiration with an accepted request fails without a retirement permission", async () => {
  const session = transport("process.stdin.resume();setInterval(()=>{},1000)", process.cwd(), 500);
  await session.ready();
  assert.deepEqual(await session.request(["wait"]), {kind: "failure", id: 1, outcome: "timeout"});
  assert.equal(await session.exited, 0);
  assert.equal((await session.lines.next()).done, true, "accepted work must not receive a retry fence");
});
