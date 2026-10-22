import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {once} from "node:events";
import {existsSync} from "node:fs";
import {mkdtemp, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {setTimeout as delay} from "node:timers/promises";
import test from "node:test";

const bridge = new URL("../../loopx/control_plane/turn_driver/host_stdio_bridge.ts", import.meta.url).pathname;
const launch = (script: string) => spawn(process.execPath,
  ["--no-warnings", "--experimental-strip-types", bridge, process.execPath, "-e", script],
  {stdio: ["pipe", "pipe", "pipe"]});

test("stdio relay preserves UTF-8 split across input chunks and keeps stdin open", async () => {
  const child = launch("process.stdin.on('data', b => process.stdout.write(b))");
  const result = once(child, "close");
  let text = "";
  const echoed = new Promise<void>(resolve => child.stdout.on("data", chunk => {
    text += chunk.toString();
    if (text === "界\n") resolve();
  }));
  const bytes = Buffer.from("界\n");
  child.stdin.write(bytes.subarray(0, 1));
  await delay(20);
  child.stdin.write(bytes.subarray(1));
  try { await Promise.race([echoed, delay(5000).then(() => {throw new Error("stdio echo did not arrive");})]); }
  finally { child.stdin.end(); }
  await result;
  assert.equal(text, "界\n");
});

for (const stop of ["eof", "SIGTERM", "SIGINT"] as const) {
  test(`stdio relay drains the owned tool before returning after ${stop}`, {skip: process.platform === "win32"}, async t => {
    const root = await mkdtemp(join(tmpdir(), "loopx-stdio-stop-"));
    t.after(() => rm(root, {recursive: true, force: true}));
    const ready = join(root, "ready"), release = join(root, "release"), late = join(root, "late");
    const child = launch(`const fs=require('node:fs');
      process.on('SIGTERM',()=>{});process.on('SIGINT',()=>{});
      fs.writeFileSync(${JSON.stringify(ready)},'ready');
      setInterval(()=>{if(fs.existsSync(${JSON.stringify(release)}))fs.writeFileSync(${JSON.stringify(late)},'late')},10);`);
    const closed = once(child, "close");
    t.after(async () => {
      if (child.exitCode === null && child.signalCode === null) {
        child.stdin.end();
        child.kill("SIGTERM");
        await closed;
      }
    });
    const deadline = Date.now() + 5000;
    while (!existsSync(ready) && Date.now() < deadline) await delay(10);
    assert.ok(existsSync(ready), "fixture tool must start before cancellation");
    if (stop === "eof") child.stdin.end(); else child.kill(stop);
    await closed;
    // A barrier lets any survivor write only after the relay has returned.
    await writeFile(release, "continue");
    await delay(200);
    assert.equal(existsSync(late), false, "no tool effect may occur after cleanup returned");
  });
}
