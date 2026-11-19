// Run with an existing Ego Page and the dashboard's Vite origin:
// const {checkFootageWatch} = await import("/checkout/apps/presentation/dashboard/smoke/content-footage-watch-checks.mjs");
// console.log(await checkFootageWatch(page, origin));
import assert from "node:assert/strict";

export async function checkFootageWatch(page, origin) {
  const load = async () => {
    await page.goto(origin + "/smoke/content-footage-watch-smoke.html");
    await page.waitForFunction(() => window.footageWatchFixture && document.body.innerText.includes("A bounded observation"));
  };
  await load();
  const initial = await page.snapshot();
  const invoke = (method, value) => page.evaluate(async ({method, value}) => {
    await window.footageWatchFixture[method](value);
    return window.footageWatchFixture.read();
  }, {method, value});
  const read = () => page.evaluate(() => window.footageWatchFixture.read());
  const start = async () => {
    await page.click("loc=role:button[name='Check for up to 5 minutes']");
    await page.waitForFunction(() => document.body.innerText.includes("Stop checking"));
    await invoke("advance", 0);
  };
  const checks = state => state.calls.filter(call => call.action === "generation_check");
  await invoke("advance", 30000);
  assert.equal(checks(await read()).length, 0, "A saved running task must not start observation on mount");

  await load(); await start();
  let state = await invoke("advance", 299000);
  assert.ok(state.text.includes("Stop checking") && checks(state).length > 0);
  state = await invoke("advance", 2000);
  assert.ok(!state.text.includes("Stop checking"), "Expire at five elapsed minutes even while the sixteen-second GET is pending");
  const atExpiry = checks(state).length;
  state = await invoke("advance", 310000);
  assert.equal(checks(state).length, atExpiry, "No new query after the deadline");
  assert.ok(checks(state).every(call => call.at < 300000));
  const deadlineResult = {queries: atExpiry, lastQueryAt: checks(state).at(-1).at};

  await start();
  state = await invoke("advance", 27000);
  assert.ok(checks(state).length > atExpiry, "Explicit restart gets a fresh window");
  await page.click("loc=role:button[name='Stop checking']");
  await page.waitForFunction(() => !document.body.innerText.includes("Stop checking"));
  const stoppedAt = checks(state).length;
  state = await invoke("advance", 310000);
  assert.equal(checks(state).length, stoppedAt, "Manual stop retains the cloud task without more observation");

  for (const reason of ["terminal", "unreadable", "changeGoal", "unmount"]) {
    await load(); await start(); await invoke("advance", 11000);
    await invoke(reason);
    // Let the pending response be observed before advancing beyond that state.
    if (reason === "terminal" || reason === "unreadable") {
      await invoke("advance", 16000);
      await page.waitForFunction(() => !document.body.innerText.includes("Stop checking"));
    }
    state = await invoke("advance", 310000);
    assert.equal(checks(state).length, 1, reason + " stops further automatic queries");
    assert.ok(!state.text.includes("Stop checking"));
    assert.ok(state.calls.every(call => ["generation_list", "generation_check"].includes(call.action)),
      "Stopping observation must not submit or cancel a cloud task");
  }
  await load();
  return {ok: true, boundary: "actual React component, synthetic elapsed clock and transport", deadlineResult,
    verified: ["mount reads only", "elapsed deadline while busy", "no late queries", "restart", "manual stop",
      "terminal", "read failure", "Goal change", "unmount", "no submit/cancel"], initial};
}
