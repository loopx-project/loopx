import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import test from "node:test";
import {withManagedFixture} from "./managed-fixture-process.mjs";

const child = source => spawn(process.execPath, ["--input-type=module", "-e", source], {stdio: ["pipe", "pipe", "pipe"]});
const options = {readTimeoutMs: 3000, stopTimeoutMs: 100};

test("normal JSON control and stop close the real child and drain diagnostics", async () => {
  const process = child(`
    import {createInterface} from "node:readline";
    console.log(JSON.stringify({ready: true}));
    for await (const command of createInterface({input: process.stdin})) {
      if (command === "stop") {process.stderr.write("normal teardown\\n"); break;}
      console.log(JSON.stringify({command}));
    }
  `);
  const value = await withManagedFixture(process, async fixture => {
    assert.deepEqual(await fixture.next(), {ready: true});
    assert.deepEqual(await fixture.control("inspect"), {command: "inspect"});
    return "complete";
  }, options);
  assert.equal(value, "complete");
  assert.equal(process.exitCode, 0);
  assert(process.stdout.destroyed && process.stderr.destroyed);
});

test("startup exit retains the original stderr and exit code", async () => {
  const process = child('process.stderr.write("original startup failure: 缺少资源\\n"); process.exitCode = 17;');
  await assert.rejects(withManagedFixture(process, fixture => fixture.next(), options), error => {
    assert.match(error.message, /exit=17/);
    assert.match(error.message, /original startup failure: 缺少资源/);
    assert.doesNotMatch(error.message, /shutdown failed/);
    return true;
  });
  assert.equal(process.exitCode, 17);
});

test("a browser failure survives a closed-pipe shutdown race", async () => {
  const process = child('console.log(JSON.stringify({ready: true}));');
  const original = new Error("original browser assertion");
  await assert.rejects(withManagedFixture(process, async fixture => {
    await fixture.next();
    if (process.exitCode === null) await once(process, "exit");
    throw original;
  }, options), error => error === original);
  assert.equal(process.exitCode, 0);
});

test("a broken control pipe rejects instead of crashing or waiting for read timeout", async () => {
  const process = child('import {closeSync} from "node:fs"; closeSync(0); console.log(JSON.stringify({ready: true})); setInterval(() => {}, 1000);');
  await assert.rejects(withManagedFixture(process, async fixture => {
    await fixture.next();
    await fixture.control("inspect");
  }, options), /Managed fixture (control|transport) failed/);
  assert.notEqual(process.signalCode, null, "Cleanup terminates the real child after failed control");
});

test("spawn failure rejects with its cause and still closes the child", async () => {
  const process = spawn("loopx-missing-synthetic-fixture-executable", [], {stdio: ["pipe", "pipe", "pipe"]});
  await assert.rejects(withManagedFixture(process, fixture => fixture.next(), options), error => {
    assert.equal(error.cause.code, "ENOENT");
    return true;
  });
  assert(process.stdout.destroyed && process.stderr.destroyed);
});

test("an unresponsive fixture is reaped without replacing the scenario failure", async () => {
  const process = child('process.on("SIGTERM", () => {}); console.log(JSON.stringify({ready: true})); setInterval(() => {}, 1000);');
  const original = new Error("original failure before teardown");
  await assert.rejects(withManagedFixture(process, async fixture => {
    await fixture.next();
    throw original;
  }, options), error => error === original);
  assert.equal(process.signalCode, "SIGKILL");
});
