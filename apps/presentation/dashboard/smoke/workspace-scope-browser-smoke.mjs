// Packaged steward conversation scoped to a host-granted workspace, against the
// production Chat server and store. A synthetic workspace and the repository's
// fake Codex app-server stand in for the host; no model, registry or Goal is used.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { mkdtemp, mkdir, rename, rm, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";
import { launchBrowser, loadPlaywright, waitForHttp } from "../../../../examples/dashboard-browser-smoke-support.mjs";

const repoRoot = resolve(import.meta.dirname, "../../../..");
const python = resolveTestPython({ repoRoot });
const port = Number(process.env.LOOPX_WORKSPACE_SCOPE_PORT ?? 5413);
const screenshots = process.env.LOOPX_WORKSPACE_SCOPE_SCREENSHOTS;
const root = await mkdtemp(join(tmpdir(), "loopx-workspace-scope-"));
const workspace = join(root, "notes");
await mkdir(workspace);
await mkdir(join(root, "home"));
const codex = join(root, "codex");
const packetLog = join(root, "native-packets.jsonl");
const written = spawnSync(python, ["-c", [
  "import pathlib, runpy, sys",
  "source = runpy.run_path('examples/loopx-chat-runtime-smoke.py')['FAKE_CODEX']",
  "source = source.replace('active_turn = None', 'if sys.argv[1:] == [\"debug\", \"models\"]:\\n    print(json.dumps({\\'models\\': [{\\'slug\\': \\'trial-native-model\\', \\'display_name\\': \\'Trial native model\\', \\'supported_reasoning_levels\\': [{\\'effort\\': \\'low\\'}, {\\'effort\\': \\'high\\'}, {\\'effort\\': \\'xhigh\\'}]}, {\\'slug\\': \\'rejected-model\\', \\'display_name\\': \\'Rejected model\\', \\'supported_reasoning_levels\\': [{\\'effort\\': \\'high\\'}]}]}))\\n    sys.exit(0)\\nactive_turn = None', 1)",
  "source = source.replace('    method = request.get(\"method\")', f'    with open({sys.argv[2]!r}, \"a\") as output:\\n        output.write(json.dumps(request) + \"\\\\n\")\\n    method = request.get(\"method\")')",
  "source = source.replace('        result = {\"thread\": {\"id\": \"durable-thread\"}}', '        result = {\"thread\": {\"id\": \"durable-thread\"}, \"model\": \"rejected-readback\" if request[\"params\"].get(\"model\") == \"rejected-model\" else request[\"params\"].get(\"model\", \"old-model\"), \"reasoningEffort\": request[\"params\"].get(\"config\", {}).get(\"model_reasoning_effort\", \"xhigh\")}')",
  "path = pathlib.Path(sys.argv[1]); path.write_text(source); path.chmod(0o700)",
].join("\n"), codex, packetLog], { cwd: repoRoot, encoding: "utf8" });
assert.equal(written.status, 0, written.stderr);

let serverError = "";
function startServer(grant) {
  const child = spawn(python, ["-c", "import sys; from loopx.entrypoint import main; sys.exit(main())",
    "chat", "--no-open", "--port", String(port), "--codex-bin", codex, "--scan-root", workspace,
    "--global-registry", "--project-workspace-grant", grant],
  { cwd: root, env: { ...process.env, HOME: join(root, "home"), PYTHONPATH: repoRoot, LOOPX_USAGE_PING: "0" }, stdio: ["ignore", "pipe", "pipe"] });
  for (const stream of [child.stdout, child.stderr]) stream.on("data", (chunk) => { serverError += String(chunk); });
  return child;
}
let server = startServer("workspace_read");
async function stopServer() {
  if (server.exitCode !== null) return;
  const exited = new Promise((resolveExit) => server.once("exit", resolveExit));
  server.kill();
  await exited;
}
// The first-run usage notice is unrelated to this journey; keep evidence focused.
async function capture(target, name) {
  if (!screenshots) return;
  const dismiss = target.getByRole("button", { name: "收起统计告知" });
  if (await dismiss.isVisible()) await dismiss.click();
  await target.screenshot({ path: join(screenshots, name) });
}
let browser;
try {
  const url = `http://127.0.0.1:${port}/chat/`;
  await waitForHttp(url).catch((error) => { throw new Error(`${error.message}\n${serverError}`); });
  browser = await launchBrowser(loadPlaywright().chromium);
  const page = await browser.newPage({ locale: "zh-CN", viewport: { width: 1440, height: 900 } });
  const sessionRequests = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/chat/sessions") sessionRequests.push(request.postDataJSON());
  });
  await page.goto(url, { waitUntil: "networkidle" });
  const scope = page.getByRole("combobox", { name: "范围" });
  assert.equal(await scope.count(), 0, "The steward overview keeps its first screen; scope belongs to the conversation");

  await page.getByRole("navigation", { name: "管家视图" }).getByRole("button", { name: "对话" }).click();
  await scope.click();
  await page.getByRole("option", { name: "notes" }).click();
  await page.getByText("工作区对话 · 只读").waitFor();
  const workspaceUrl = page.url();
  assert.match(workspaceUrl, /[?&]workspace=[0-9a-f]{24}(?:&|$)/u);
  const question = "整理一下最近的素材";
  await page.getByLabel("发送消息").fill(question);
  await page.keyboard.press("Enter");
  await page.getByText("Runtime response.").first().waitFor({ timeout: 15_000 });
  // Upgrade a persisted read-only workspace through the same ordinary Scope entry.
  await stopServer();
  server = startServer("workspace_write");
  await waitForHttp(url).catch((error) => { throw new Error(`${error.message}\n${serverError}`); });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByText("工作区对话 · 读写").waitFor({ timeout: 15_000 });
  await page.getByText(question).first().waitFor({ timeout: 15_000 });
  await page.getByLabel("发送消息").fill("继续整理素材");
  await page.keyboard.press("Enter");
  await page.getByText("Runtime response.").nth(1).waitFor({ timeout: 15_000 });
  await capture(page, "ordinary-workspace-conversation.png");

  const projectRequests = sessionRequests.filter((body) => body.context_kind === "project");
  assert.ok(projectRequests.length > 0, "The workspace scope opens a project Session");
  for (const body of projectRequests) {
    assert.equal("goal_id" in body, false, "A workspace Session never names a Goal");
    assert.match(body.project_ref, /^[0-9a-f]{24}$/u);
  }

  await page.reload({ waitUntil: "networkidle" });
  await page.getByText(question).first().waitFor({ timeout: 15_000 });
  await scope.click();
  await page.getByRole("option", { name: "LoopX 管家" }).click();
  await page.waitForURL((current) => !current.searchParams.has("workspace"));
  await page.getByRole("combobox", { name: "范围" }).waitFor();
  assert.equal(await page.getByText(question).count(), 0, "The steward scope does not show the workspace exchange");
  assert.ok(sessionRequests.filter((body) => body.context_kind === "manager").every((body) => !("project_ref" in body)));

  if (screenshots) {
    const narrow = await browser.newPage({ locale: "zh-CN", viewport: { width: 390, height: 844 } });
    await narrow.goto(workspaceUrl, { waitUntil: "networkidle" });
    await narrow.getByText(question).first().waitFor({ timeout: 15_000 });
    await capture(narrow, "ordinary-workspace-conversation-narrow.png");
    await narrow.close();
  }

  await rename(workspace, `${workspace}.moved`);
  await page.goto(workspaceUrl, { waitUntil: "networkidle" });
  await page.getByTestId("workspace-scope-unavailable").waitFor({ timeout: 15_000 });
  await page.getByText(question).first().waitFor({ timeout: 15_000 });
  await page.getByLabel("发送消息").fill("还能继续吗");
  assert.equal(await page.getByRole("button", { name: "发送", exact: true }).isDisabled(), true,
    "A revoked grant keeps history and blocks new messages");
  await capture(page, "ordinary-workspace-grant-rejection.png");
  // Recover the same grant, then use the production HTTP/store/native path.
  // The provider is deterministic: no model or live workspace is involved.
  await rename(`${workspace}.moved`, workspace);
  await page.reload({waitUntil: "networkidle"});
  const composer = page.getByLabel("发送消息");
  await composer.waitFor();
  assert.equal(await page.locator(".personal-channel-header .personal-chat-model-menu").count(), 0);
  const menu = page.locator(".personal-channel-composer .personal-chat-model-menu");
  const actions = page.getByLabel("添加附件与快捷操作", {exact: true});
  await composer.fill("保留草稿，先核对配置。");
  const beforeActions = sessionRequests.length;
  await actions.click();
  await page.getByRole("button", {name: "添加图片", exact: true}).waitFor();
  await page.keyboard.press("Escape");
  assert.equal(await actions.evaluate(node => node === document.activeElement), true);
  assert.equal(await composer.inputValue(), "保留草稿，先核对配置。");
  assert.equal(sessionRequests.length, beforeActions, "Menu open/dismiss is read-only");
  const sessionsBeforeSettings = (await (await page.request.get(`${url}../api/chat/sessions`)).json()).sessions.map(row => row.session_id).sort();
  const packetsBefore = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  await menu.locator("summary").click();
  const modelSaved = page.waitForResponse(response => response.url().endsWith("/model-settings") && response.request().method() === "POST");
  await page.getByRole("combobox", {name: "选择模型", exact: true}).click();
  await page.getByRole("option", {name: "Trial native model", exact: true}).click();
  const modelResponse = await modelSaved;
  assert.equal(modelResponse.status(), 200, await modelResponse.text());
  const configured = (await modelResponse.json()).session;
  const sessionId = configured.session_id;
  assert.ok(sessionsBeforeSettings.includes(sessionId), "The selector updates an existing Session");
  assert.equal(configured.requested_agent_model_id, "trial-native-model");
  assert.equal(configured.agent_model_id, "old-model", "Saving the next selection does not relabel prior native work");
  const effortSaved = page.waitForResponse(response => response.url().endsWith("/model-settings") && response.request().method() === "POST");
  await page.getByRole("combobox", {name: "选择思考深度", exact: true}).click();
  await page.getByRole("option", {name: "high", exact: true}).click();
  assert.equal((await effortSaved).status(), 200);
  assert.equal(sessionRequests.length, beforeActions, "Settings never create a replacement Session");
  const afterSettings = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  assert.equal(afterSettings.length, packetsBefore.length, "Saving a choice starts no thread, resume or model work");
  await page.keyboard.press("Escape");
  const configuredAdmission = page.waitForResponse(response => response.url().endsWith(`/sessions/${sessionId}/turns`) && response.status() === 202);
  await composer.press("Enter");
  const configuredResponse = await configuredAdmission;
  const originalSend = configuredResponse.request().postDataJSON();
  const originalTurn = (await configuredResponse.json()).turn_id;
  await page.locator(".personal-message-pending").waitFor({state: "hidden"});
  await menu.locator(".personal-model-current").filter({hasText: "trial-native-model"}).waitFor();
  let completedPackets = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  assert.ok(completedPackets.some(row => row.method === "thread/resume" && row.params.threadId === "durable-thread"
    && row.params.model === "trial-native-model" && row.params.config.model_reasoning_effort === "high"));
  assert.equal(completedPackets.filter(row => row.method === "thread/start").length,
    packetsBefore.filter(row => row.method === "thread/start").length, "Model changes never fork the native thread");
  assert.ok(completedPackets.some(row => row.method === "turn/start" && row.params.model === "trial-native-model" && row.params.effort === "high"));
  await page.reload({waitUntil: "networkidle"});
  await composer.waitFor();
  await menu.locator(".personal-model-current").filter({hasText: "trial-native-model"}).waitFor();
  const restoredSessions = (await (await page.request.get(`${url}../api/chat/sessions`)).json()).sessions;
  assert.deepEqual(restoredSessions.map(row => row.session_id).sort(), sessionsBeforeSettings);
  assert.equal(restoredSessions.find(row => row.session_id === sessionId).requested_agent_reasoning_effort, "high");
  const snapshotUrl = new URL(`/api/chat/sessions/${sessionId}`, url).href;
  const beforeBusy = await (await page.request.get(snapshotUrl)).json();
  await composer.fill("wait for interrupt");
  const admitted = page.waitForResponse(response => response.url().endsWith(`/sessions/${sessionId}/turns`) && response.status() === 202);
  await composer.press("Enter");
  const activeId = (await (await admitted).json()).turn_id;
  const activeDeadline = Date.now() + 15_000;
  let activeSnapshot;
  do {
    activeSnapshot = await (await page.request.get(snapshotUrl)).json();
    if (activeSnapshot.active_turn?.turn_id === activeId && activeSnapshot.active_turn?.status === "running") break;
    await page.waitForTimeout(100);
  } while (Date.now() < activeDeadline);
  assert.equal(activeSnapshot.active_turn?.status, "running");
  await composer.fill("运行中调整设置，保留当前草稿。");
  const packetsWhileBusy = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  await menu.locator("summary").click();
  const busyChoice = page.waitForResponse(response => response.url().endsWith("/model-settings") && response.status() === 200);
  await page.getByRole("combobox", {name: "选择思考深度", exact: true}).click();
  await page.getByRole("option", {name: "low", exact: true}).click();
  const busySaved = (await (await busyChoice).json()).session;
  assert.equal(busySaved.active_turn_id, activeId);
  assert.equal(busySaved.agent_reasoning_effort, "high");
  assert.equal(busySaved.requested_agent_reasoning_effort, "low");
  assert.equal(await composer.inputValue(), "运行中调整设置，保留当前草稿。");
  const savedBusyPackets = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  assert.equal(savedBusyPackets.length, packetsWhileBusy.length, "Busy configuration saves never interrupt or resume the active native Turn");
  await capture(page, "composer-current-session-pending-desktop.png");
  await page.setViewportSize({width: 390, height: 844});
  await capture(page, "composer-current-session-pending-narrow.png");
  await page.setViewportSize({width: 1440, height: 900});
  await page.keyboard.press("Escape");
  const queued = await page.request.post(new URL(`/api/chat/sessions/${sessionId}/turns`, url).href,
    {data: {client_turn_id: "next-settings-followup", delivery: "queue", message: "A queued follow-up uses the saved configuration."}});
  assert.equal(queued.status(), 202, await queued.text());
  const queuedId = (await queued.json()).turn_id;
  assert.notEqual(queuedId, activeId);
  const stillActive = await (await page.request.get(snapshotUrl)).json();
  assert.equal(stillActive.session.active_turn_id, activeId, "A queued follow-up does not replace the running Turn");
  const interrupted = await page.request.post(new URL(`/api/chat/sessions/${sessionId}/turns/${activeId}/interrupt`, url).href, {data: {}});
  assert.equal(interrupted.status(), 200, await interrupted.text());
  const queuedDeadline = Date.now() + 15_000;
  let afterBusy;
  do {
    afterBusy = await (await page.request.get(snapshotUrl)).json();
    if (afterBusy.messages?.some(row => row.turn_id === queuedId && row.role === "agent")) break;
    await page.waitForTimeout(100);
  } while (Date.now() < queuedDeadline);
  assert.ok(afterBusy.messages.some(row => row.turn_id === queuedId && row.role === "agent"), "The queued Turn produced its own final answer");
  assert.equal(afterBusy.session.agent_reasoning_effort, "low", JSON.stringify({
    status: afterBusy.session.status, active: afterBusy.session.active_turn_id,
    requested: afterBusy.session.requested_agent_reasoning_effort, observed: afterBusy.session.agent_reasoning_effort,
    native: (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line))
      .filter(row => ["thread/resume", "turn/start"].includes(row.method)).map(row => ({method: row.method, model: row.params.model, effort: row.params.effort, config: row.params.config})),
  }));
  assert.equal(afterBusy.session.session_id, sessionId);
  assert.deepEqual(afterBusy.messages.slice(0, beforeBusy.messages.length), beforeBusy.messages, "Earlier history survives model changes byte-for-byte");
  completedPackets = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  assert.equal(completedPackets.findLast(row => row.method === "turn/start").params.effort, "low");
  assert.equal(completedPackets.filter(row => row.method === "thread/start").length,
    packetsBefore.filter(row => row.method === "thread/start").length, "Busy-to-queued adoption also retains the original thread");
  await page.reload({waitUntil: "networkidle"});
  await menu.locator("summary small").filter({hasText: "low"}).waitFor();
  await menu.locator("summary").click();
  await page.getByText("后续消息", {exact: true}).waitFor();
  const rejectionDepth = page.waitForResponse(response => response.url().endsWith("/model-settings") && response.status() === 200);
  await page.getByRole("combobox", {name: "选择思考深度", exact: true}).click();
  await page.getByRole("option", {name: "high", exact: true}).click();
  await rejectionDepth;
  const rejectedChoiceSaved = page.waitForResponse(response => response.url().endsWith("/model-settings") && response.request().method() === "POST");
  await page.getByRole("combobox", {name: "选择模型", exact: true}).click();
  await page.getByRole("option", {name: "Rejected model", exact: true}).click();
  assert.equal((await rejectedChoiceSaved).status(), 200);
  await page.keyboard.press("Escape");
  const beforeReplayPackets = await readFile(packetLog, "utf8");
  const originalReceipt = await page.request.post(new URL(`/api/chat/sessions/${sessionId}/turns`, url).href, {data: originalSend});
  assert.equal(originalReceipt.status(), 202, await originalReceipt.text());
  const replayBody = await originalReceipt.json();
  assert.equal(replayBody.turn_id, originalTurn);
  assert.equal(replayBody.created, false, "A completed request keeps its receipt despite a later unsupported configuration");
  assert.equal(await readFile(packetLog, "utf8"), beforeReplayPackets, "Receipt recovery starts no native configuration or work");
  const invalidIdentity = await page.request.post(new URL(`/api/chat/sessions/${sessionId}/turns`, url).href,
    {data: {client_turn_id: "invalid/identity", message: "Reject the request before any native preparation."}});
  assert.equal(invalidIdentity.status(), 400, await invalidIdentity.text());
  assert.equal(await readFile(packetLog, "utf8"), beforeReplayPackets, "Malformed request identity cannot prepare native configuration");
  await composer.fill("配置被拒绝时保留这条消息。");
  const rejection = page.waitForResponse(response => response.url().endsWith(`/sessions/${sessionId}/turns`) && response.status() === 424);
  await composer.press("Enter");
  const rejectedResponse = await rejection;
  assert.equal(rejectedResponse.status(), 424);
  const rejectedBody = await rejectedResponse.json();
  assert.equal(rejectedBody.error_code, "unsupported_configuration");
  assert.equal(rejectedBody.delivery_state, "not_delivered");
  await page.waitForFunction(() => document.querySelector("textarea")?.value === "配置被拒绝时保留这条消息。");
  assert.equal(await composer.inputValue(), "配置被拒绝时保留这条消息。");
  const refusedSession = (await (await page.request.get(`${url}../api/chat/sessions`)).json()).sessions.find(row => row.session_id === sessionId);
  assert.equal(refusedSession.agent_model_id, "trial-native-model", "A refused native configuration cannot replace actual readback");
  const refusedPackets = (await readFile(packetLog, "utf8")).trim().split("\n").map(line => JSON.parse(line));
  assert.equal(refusedPackets.filter(row => row.method === "turn/start").length,
    completedPackets.filter(row => row.method === "turn/start").length, "Rejected configuration cannot dispatch or replay the message");
  await capture(page, "composer-configuration-rejected.png");
  await menu.locator("summary").click();
  const repairedChoice = page.waitForResponse(response => response.url().endsWith("/model-settings") && response.status() === 200);
  await page.getByRole("combobox", {name: "选择模型", exact: true}).click();
  await page.getByRole("option", {name: "Trial native model", exact: true}).click();
  await repairedChoice;
  await page.keyboard.press("Escape");
  const recoveredAdmission = page.waitForResponse(response => response.url().endsWith(`/sessions/${sessionId}/turns`) && response.status() === 202);
  await composer.press("Enter");
  await recoveredAdmission;
  await page.locator(".personal-message-pending").waitFor({state: "hidden"});
  const recoveredSessions = (await (await page.request.get(new URL("/api/chat/sessions", url).href)).json()).sessions;
  assert.deepEqual(recoveredSessions.map(row => row.session_id).sort(), sessionsBeforeSettings,
    "Correcting rejected settings never makes the next Send start a new Session");
  console.log("workspace scope browser smoke ok; composer actions, actual native model/effort and rejection retained");
} finally {
  await browser?.close();
  await stopServer();
  await rm(root, { force: true, recursive: true });
}
