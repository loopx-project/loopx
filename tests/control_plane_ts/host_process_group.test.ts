import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import test from "node:test";
import {hasLiveGroupMembers, waitForProcessGroupStop} from "../../loopx/control_plane/turn_driver/host_process_group.ts";

test("cleanup observation excludes only zombies, not sleeping or stopped members", () => {
  assert.equal(hasLiveGroupMembers(" 42 Z\n42 Z+\n100 R\n", 42), false);
  for (const state of ["R", "S", "T", "D", "?", "ZS"]) {
    assert.equal(hasLiveGroupMembers(`42 ${state}\n`, 42), !state.startsWith("Z"));
  }
  assert.equal(hasLiveGroupMembers("100 S\n", 42), false);
  assert.throws(() => hasLiveGroupMembers("not a process row\n", 42), /invalid.*observation/);
});

test("an observable live group fails closed at the bounded cleanup deadline", {skip: process.platform === "win32"}, async () => {
  // Observe our own live group with signal zero; do not terminate any process.
  const pgid = Number(execFileSync("ps", ["-p", String(process.pid), "-o", "pgid="], {encoding: "utf8"}).trim());
  assert.ok(Number.isSafeInteger(pgid) && pgid > 0);
  await assert.rejects(waitForProcessGroupStop(pgid), /did not stop before cleanup deadline/);
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
