// Packaged App -> real Chat route -> typed inspection -> synthetic HTTPS peer.
import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createInterface} from "node:readline";
import {once} from "node:events";
import {mkdtemp, mkdir, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {resolve} from "node:path";
import {fileURLToPath} from "node:url";
import {testPythonCommand} from "../../../../scripts/test-python.mjs";
import {launchBrowser, loadPlaywright} from "../../../../examples/dashboard-browser-smoke-support.mjs";
import {openWorkspacePage} from "../../../../examples/personal-workspace-browser/scenario-context.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const temporary = await mkdtemp(resolve(tmpdir(), "loopx-remote-inspection-"));
const output = resolve(root, "output/playwright/remote-authority-inspection");
const baseline = process.env.LOOPX_REMOTE_INSPECT_BASELINE_ASSETS;
const arguments_ = ["fixture", "--root", temporary, ...(baseline ? ["--assets", baseline] : [])];
const python = testPythonCommand(`
import runpy
sys.argv = ${JSON.stringify(arguments_)}
runpy.run_module("tests.control_plane.remote_authority_browser_fixture", run_name="__main__")
`);
const server = spawn(python.command, python.args, {cwd: root,
  env: {...process.env, LOOPX_USAGE_PING: "0"}, stdio: ["pipe", "pipe", "pipe"]});
const replies = [], waiting = [];
let diagnostics = "", browser, session, release;
const lines = createInterface({input: server.stdout});
lines.on("line", line => {
  const reply = JSON.parse(line);
  if (waiting.length) waiting.shift().resolve(reply); else replies.push(reply);
});
server.stderr.on("data", chunk => {diagnostics = (diagnostics + chunk).slice(-4000);});
server.on("exit", code => {for (const waiter of waiting.splice(0)) waiter.reject(new Error(`Fixture exited ${code}: ${diagnostics}`));});
function reply() {
  return replies.length ? Promise.resolve(replies.shift()) : new Promise((resolveReply, reject) => {
    const timer = setTimeout(() => reject(new Error(`Fixture response timed out: ${diagnostics}`)), 20000);
    waiting.push({resolve: value => {clearTimeout(timer); resolveReply(value);}, reject: error => {clearTimeout(timer); reject(error);}});
  });
}
async function control(value = {}) {server.stdin.write(JSON.stringify(value) + "\n"); return await reply();}
try {
  const {origin} = await reply();
  await mkdir(output, {recursive: true});
  browser = await launchBrowser(loadPlaywright().chromium);
  let hold = false, fail = false, requestHeld;
  session = await openWorkspacePage(browser, origin + "/chat/?statusUrl=/status.json", {
    apiOptions: {fixturePort: Number(new URL(origin).port)},
    viewport: {width: 1440, height: 1000}, gotoWaitUntil: "domcontentloaded",
    beforeGoto: async (_api, page) => {
      await page.addInitScript(() => localStorage.setItem("loopx-pw-locale", "en"));
      await page.route("**/api/chat/goal-configuration/remote-authority/inspect", async route => {
        if (hold) await new Promise(resolveRequest => {release = resolveRequest; requestHeld();});
        if (fail) await route.abort(); else await route.continue();
      });
    },
  });
  const {page} = session;
  page.setDefaultTimeout(15000);
  await page.goto(origin + "/chat/?statusUrl=/status.json&settings_scope=goal&settings_goal=product-release&settings_tab=capabilities",
    {waitUntil: "domcontentloaded"});
  await page.locator(".personal-capability-settings").waitFor();
  assert.equal((await control()).requests, 0, "discovery never contacts the remote service");
  if (baseline) {
    assert.equal(await page.getByRole("region", {name: "Remote task connection"}).count(), 0);
    await page.screenshot({path: resolve(output, "before-desktop.png")});
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: resolve(output, "before-narrow.png")});
  } else {
    const panel = page.getByRole("region", {name: "Remote task connection"});
    await panel.getByRole("button", {name: "Check connection", exact: true}).click();
    await panel.getByText("Read verified", {exact: true}).waitFor();
    await page.screenshot({path: resolve(output, "success-desktop.png")});
    assert.equal((await control({mode: "denied"})).requests, 1);
    await panel.getByRole("button", {name: "Check again", exact: true}).click();
    await panel.getByText("Read unverified", {exact: true}).waitFor();
    await page.screenshot({path: resolve(output, "denied-desktop.png")});
    assert.equal((await control({mode: "found"})).requests, 2);
    await panel.getByRole("button", {name: "Check again", exact: true}).click();
    await panel.getByText("Read verified", {exact: true}).waitFor();
    assert.equal((await control()).requests, 3);

    hold = true;
    const held = new Promise(resolveHeld => {requestHeld = resolveHeld;});
    const delayedResponse = page.waitForResponse(response => response.url().endsWith("/remote-authority/inspect"));
    await panel.getByRole("button", {name: "Check again", exact: true}).click();
    await held;
    await panel.getByRole("button", {name: "Checking…", exact: true}).waitFor();
    assert.equal(await panel.getByRole("button").isDisabled(), true);
    const target = page.locator(".personal-settings-goal-choice select");
    const other = await target.locator("option").evaluateAll(options => options.find(option => option.value && option.value !== "product-release").value);
    await target.selectOption(other);
    hold = false;
    release();
    await (await delayedResponse).finished();
    assert.equal(await panel.getByText("Read verified", {exact: true}).count(), 0, "stale results cannot cross Goals");
    await target.selectOption("product-release");
    fail = true;
    await panel.getByRole("button", {name: "Check connection", exact: true}).click();
    await panel.getByRole("alert").waitFor();
    fail = false;
    await control({mode: "denied"});
    await panel.getByRole("button", {name: "Check again", exact: true}).click();
    await panel.getByText("Read unverified", {exact: true}).waitFor();
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: resolve(output, "denied-narrow.png")});
    assert.ok(await panel.evaluate(element => element.getBoundingClientRect().right <= innerWidth + 1), "narrow view has no horizontal overflow");
    const editor = page.locator(".personal-capability-detail");
    await editor.getByRole("heading", {name: "Periodic reports", exact: true}).scrollIntoViewIfNeeded();
    assert.ok(await editor.evaluate(element => element.clientHeight >= 200), "inspection preserves a usable settings editor on narrow screens");
    await editor.getByRole("combobox").first().click();
    await page.keyboard.press("Escape");
    await page.screenshot({path: resolve(output, "settings-narrow.png")});
    assert.equal((await control()).unchanged, true, "inspection never mutates local authority");
    assert.equal(session.errors.filter(error => !error.includes("net::ERR_FAILED")).length, 0, session.errors.join("\n"));
  }
  console.log(baseline ? "Captured baseline packaged Goal settings" : "Packaged inspection, denial/recovery, scope fencing, network failure and state parity passed");
} finally {
  release?.();
  await session?.close();
  await browser?.close();
  const stopped = server.exitCode === null ? once(server, "exit") : Promise.resolve();
  server.stdin.end();
  server.kill("SIGTERM");
  await stopped;
  await rm(temporary, {recursive: true, force: true});
}
