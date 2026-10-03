import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import test from "node:test";

test("runtime loads selected owners without importing unrelated methods", () => {
  const entry = new URL("../../loopx/control_plane/effect_runtime_handlers.ts", import.meta.url).href;
  // Observe a fresh process, not this test runner's already imported modules.
  const script = `
    import assert from "node:assert/strict";
    import {registerHooks} from "node:module";
    const loaded = new Set();
    registerHooks({load(url, context, nextLoad) {
      loaded.add(url);
      return nextLoad(url, context);
    }});
    const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import(${JSON.stringify(entry)});
    let shutdowns = 0;
    const handlers = createEffectRuntimeHandlers({fingerprint: "loading-test",
      requestShutdown: () => { shutdowns++; }});
    const imported = (suffix) => [...loaded].some(url => url.endsWith(suffix));
    for (const suffix of ["/task_lease_acquire_decision.ts", "/external_evidence.ts", "/performance_diagnosis.ts"]) {
      assert.equal(imported(suffix), false, suffix);
    }
    const ping = await dispatchEffectRuntimeMethod(handlers, "runtime.ping", {});
    assert.deepEqual(ping, {ready: true, pid: process.pid, fingerprint: "loading-test"});
    await assert.rejects(dispatchEffectRuntimeMethod(handlers, "not-a-method", {}),
      {code: "unsupported_method"});
    assert.equal(imported("/task_lease_acquire_decision.ts"), false);
    const results = await Promise.all([
      dispatchEffectRuntimeMethod(handlers, "task_lease.write_scopes.overlap",
        {left: ["docs/**"], right: ["docs/file.md"]}),
      dispatchEffectRuntimeMethod(handlers, "task_lease.write_scopes.overlap",
        {left: ["tests/**"], right: ["docs/file.md"]}),
    ]);
    assert.equal(results[0].overlap, true);
    assert.equal(results[1].overlap, false);
    const repeated = await dispatchEffectRuntimeMethod(handlers, "task_lease.write_scopes.overlap",
      {left: ["tests/**"], right: ["docs/file.md"]});
    assert.equal(repeated.overlap, false);
    assert.equal(imported("/task_lease_acquire_decision.ts"), true);
    assert.equal(imported("/external_evidence.ts"), false);
    assert.equal(imported("/performance_diagnosis.ts"), false);
    await dispatchEffectRuntimeMethod(handlers, "runtime.shutdown", {});
    assert.equal(shutdowns, 1);
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types",
    "--input-type=module", "-e", script], {encoding: "utf8", timeout: 30_000});
  assert.equal(child.status, 0, child.stderr);
});

test("a selected module load failure rejects instead of falling back", () => {
  const entry = new URL("../../loopx/control_plane/effect_runtime_handlers.ts", import.meta.url).href;
  const script = `
    import assert from "node:assert/strict";
    import {registerHooks} from "node:module";
    registerHooks({load(url, context, nextLoad) {
      if (url.endsWith("/performance_diagnosis.ts")) {
        throw new Error("selected owner unavailable");
      }
      return nextLoad(url, context);
    }});
    const {createEffectRuntimeHandlers, dispatchEffectRuntimeMethod} = await import(${JSON.stringify(entry)});
    const handlers = createEffectRuntimeHandlers({fingerprint: "loading-test", requestShutdown() {}});
    for (let attempt = 0; attempt < 2; attempt++) {
      await assert.rejects(dispatchEffectRuntimeMethod(handlers, "performance_diagnosis.plan", {}),
        /selected owner unavailable/);
    }
    assert.equal((await dispatchEffectRuntimeMethod(handlers, "runtime.ping", {})).ready, true);
  `;
  const child = spawnSync(process.execPath, ["--no-warnings", "--experimental-strip-types",
    "--input-type=module", "-e", script], {encoding: "utf8", timeout: 30_000});
  assert.equal(child.status, 0, child.stderr);
});
