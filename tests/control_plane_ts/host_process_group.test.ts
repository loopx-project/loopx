import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {once} from "node:events";
import {mkdtemp, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {performance} from "node:perf_hooks";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";
import {hasLiveGroupMembers, waitForProcessGroupStop} from "../../loopx/control_plane/turn_driver/host_process_group.ts";
import {runHostProcess} from "../../loopx/control_plane/turn_driver/host_process.ts";

test("cleanup observation excludes only zombies, not sleeping or stopped members", () => {
  assert.equal(hasLiveGroupMembers(" 42 Z\n42 Z+\n100 R\n", 42), false);
  for (const state of ["R", "S", "T", "D", "?", "ZS"]) {
    assert.equal(hasLiveGroupMembers(`42 ${state}\n`, 42), !state.startsWith("Z"));
  }
  assert.equal(hasLiveGroupMembers("100 S\n", 42), false);
  assert.throws(() => hasLiveGroupMembers("not a process row\n", 42), /invalid.*observation/);
  for (const missing of ["", "\n \t"]) {
    assert.throws(() => hasLiveGroupMembers(missing, 42), /invalid.*observation/);
  }
});

test("an observable live group fails closed at the bounded cleanup deadline", {skip: process.platform === "win32"}, async t => {
  // Own a dedicated group, like the Host supervisor. An inherited group can
  // be 1 in a PID namespace, where kill(-1, 0) has Linux's all-process meaning.
  const child = spawn(process.execPath, ["-e", "setInterval(() => {}, 1000); process.stdout.write('ready');"],
    {detached: true, stdio: ["ignore", "pipe", "ignore"]});
  t.after(async () => {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) return;
    const exited = once(child, "exit");
    child.kill("SIGKILL"); // Only this test's child; it creates no descendants.
    await exited;
  });
  await once(child, "spawn");
  const [ready] = await once(child.stdout!, "data");
  assert.equal(ready.toString(), "ready");
  const pgid = Number(execFileSync("ps", ["-p", String(child.pid), "-o", "pgid="], {encoding: "utf8"}).trim());
  assert.ok(Number.isSafeInteger(pgid) && pgid > 1);
  assert.equal(pgid, child.pid);
  await assert.rejects(waitForProcessGroupStop(pgid), /did not stop before cleanup deadline/);
});

test("an absence observation after the cleanup budget cannot certify stop", {skip: process.platform === "win32"}, async t => {
  t.mock.method(process, "kill", () => true);
  let clockReads = 0;
  t.mock.method(performance, "now", () => ++clockReads <= 2 ? 0 : 1001);
  // Real ps observes no such group, but its result arrives after the budget.
  await assert.rejects(waitForProcessGroupStop(2147483647), /did not stop before cleanup deadline/);
});

test("missing process-state observation cannot certify cleanup", {skip: process.platform === "win32"}, async t => {
  t.mock.method(process, "kill", () => true); // Signal zero sees an existing group.
  const previousPath = process.env.PATH;
  process.env.PATH = "/missing/loopx-test-process-observer";
  t.after(() => { if (previousPath === undefined) delete process.env.PATH; else process.env.PATH = previousPath; });
  await assert.rejects(waitForProcessGroupStop(123), /process-group observation failed/);
});

test("EPERM is not mistaken for a stopped group", {skip: process.platform === "win32"}, async t => {
  const pgid = Number(execFileSync("ps", ["-p", String(process.pid), "-o", "pgid="], {encoding: "utf8"}).trim());
  t.mock.method(process, "kill", () => { throw Object.assign(new Error("permission denied"), {code: "EPERM"}); });
  await assert.rejects(waitForProcessGroupStop(pgid), /did not stop before cleanup deadline/);
});

test("managed OAR cancellation drains nested detached groups before returning", {skip: process.platform === "win32"}, async t => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-oar-descendants-"));
  const childMarker = join(directory, "child-counter"), grandchildMarker = join(directory, "grandchild-counter");
  const ancestry = join(directory, "ancestry.json");
  const nativeSession = join(directory, "native-session.json");
  const nativeIdentity = {id: "retained-native-session", turns: ["prior completed work"]};
  await writeFile(nativeSession, JSON.stringify(nativeIdentity));
  const ownedGroups = new Set<number>();
  t.after(async () => {
    for (const group of ownedGroups) {
      try { process.kill(-group, "SIGKILL"); } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
      }
    }
    await rm(directory, {recursive: true, force: true});
  });
  const heartbeat = (path: string) => `
    const fs=require('node:fs');let count=0;
    const path=${JSON.stringify(path)};
    process.on('SIGTERM',()=>{});
    function tick(){fs.writeFileSync(path+'.next',String(count++));fs.renameSync(path+'.next',path)}
    tick();setInterval(tick,10);
  `;
  const grandchild = heartbeat(grandchildMarker);
  const child = `
    const {spawn}=require('node:child_process');
    const grandchild=spawn(process.execPath,['-e',${JSON.stringify(grandchild)}],{detached:true,stdio:'ignore'});
    grandchild.unref();
    ${heartbeat(childMarker)}
    fs.writeFileSync(${JSON.stringify(ancestry)},JSON.stringify({child:process.pid,grandchild:grandchild.pid}));
  `;
  const leader = `
    const {spawn}=require('node:child_process'),fs=require('node:fs');
    const child=spawn(process.execPath,['-e',${JSON.stringify(child)}],{detached:true,stdio:'ignore'});
    child.unref();
    const ready=setInterval(()=>{
      if(fs.existsSync(${JSON.stringify(childMarker)})&&fs.existsSync(${JSON.stringify(grandchildMarker)})){
        clearInterval(ready);process.stdout.write('native-ready\\n');
      }
    },5);
    process.on('SIGTERM',()=>process.exit(0));
    setInterval(()=>{},1000);
  `;
  const cancellation = new AbortController();
  let ready = false;
  const result = await runHostProcess({
    argv: [process.execPath, "-e", leader], cwd: directory, input: "",
    timeout_ms: 5000, drain_timeout_ms: 100, stdout_limit_bytes: 4096,
  }, async output => {
    if (output.kind === "stdout" && output.text.includes("native-ready")) {
      ready = true;
      const groups = JSON.parse(await readFile(ancestry, "utf8"));
      ownedGroups.add(groups.child); ownedGroups.add(groups.grandchild);
      cancellation.abort();
    }
  }, cancellation.signal, undefined, {trackDescendants: true, descendants: async observation => {
    for (const group of observation.process_groups) ownedGroups.add(group);
  }});
  assert.ok(ready, "the nested native workers must actually be running before cancellation");
  assert.equal(result.outcome, "cancelled");
  assert.equal(result.descendant_cleanup_complete, true);
  assert.equal(ownedGroups.size, 2, "both independently detached groups are owned");
  for (const group of ownedGroups) await waitForProcessGroupStop(group);
  const before = await Promise.all([readFile(childMarker, "utf8"), readFile(grandchildMarker, "utf8")]);
  await delay(120);
  assert.deepEqual(await Promise.all([readFile(childMarker, "utf8"), readFile(grandchildMarker, "utf8")]), before,
    "a stopped OAR owner must not leave either detached generation writing after return");
  assert.deepEqual(JSON.parse(await readFile(nativeSession, "utf8")), nativeIdentity,
    "process cleanup preserves the native conversation used by resume");
});

test("lost owner observation cannot strand a stopped detached native process", {skip: process.platform === "win32"}, async t => {
  const directory = await mkdtemp(join(tmpdir(), "loopx-oar-owner-loss-"));
  const pidPath = join(directory, "native.pid"), effectPath = join(directory, "native-effect");
  let nativePid: number | undefined;
  t.after(async () => {
    if (nativePid) {
      try { process.kill(-nativePid, "SIGKILL"); } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
      }
    }
    await rm(directory, {recursive: true, force: true});
  });
  const native = `
    const fs=require('node:fs');
    fs.writeFileSync(${JSON.stringify(effectPath)},'completed native effect');
    process.stdout.write('ready');setInterval(()=>{},1000);
  `;
  const leader = `
    const {spawn}=require('node:child_process'),fs=require('node:fs');
    const native=spawn(process.execPath,['-e',${JSON.stringify(native)}],{detached:true,stdio:['ignore','pipe','ignore']});
    native.stdout.once('data',()=>{
      process.kill(native.pid,'SIGSTOP');
      fs.writeFileSync(${JSON.stringify(pidPath)},String(native.pid));
      process.stdout.write('native-stopped\\n');
    });
    process.on('SIGTERM',()=>process.exit(0));setInterval(()=>{},1000);
  `;
  const cancellation = new AbortController();
  let deliveryFailures = 0;
  const result = await runHostProcess({
    argv: [process.execPath, "-e", leader], cwd: directory, input: "",
    timeout_ms: 5000, drain_timeout_ms: 100, stdout_limit_bytes: 4096,
  }, async output => {
    if (output.kind === "stdout" && output.text.includes("native-stopped")) {
      nativePid = Number(await readFile(pidPath, "utf8"));
      cancellation.abort();
    }
  }, cancellation.signal, undefined, {trackDescendants: true, descendants: async observation => {
    assert.ok(nativePid && observation.process_groups.includes(nativePid));
    deliveryFailures++;
    throw new Error("owner pipe disconnected");
  }});
  assert.ok(nativePid, "the native child must have started and been suspended before owner loss");
  assert.ok(deliveryFailures > 0, "the disconnected observation path must actually execute");
  assert.equal(result.outcome, "cancelled");
  assert.equal(result.output_complete, false, "lost observations cannot be reported as complete");
  assert.equal(result.descendant_cleanup_complete, true);
  await waitForProcessGroupStop(nativePid);
  assert.equal(await readFile(effectPath, "utf8"), "completed native effect");
});
