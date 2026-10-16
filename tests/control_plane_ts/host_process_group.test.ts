import assert from "node:assert/strict";
import {execFileSync, spawn} from "node:child_process";
import {once} from "node:events";
import {performance} from "node:perf_hooks";
import test from "node:test";
import {hasLiveGroupMembers, waitForProcessGroupStop} from "../../loopx/control_plane/turn_driver/host_process_group.ts";

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
