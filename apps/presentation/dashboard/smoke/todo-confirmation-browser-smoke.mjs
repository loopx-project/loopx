import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdir } from "node:fs/promises";
import { createRequire } from "node:module";
import { createInterface } from "node:readline";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { launchBrowser } from "../../../../examples/dashboard-browser-smoke-support.mjs";
import { resolveTestPython } from "../../../../scripts/test-python.mjs";

const root = fileURLToPath(new URL("../../../../", import.meta.url));
const assetDir = resolve(process.env.LOOPX_CHAT_CONFIRMATION_ASSETS ?? resolve(root, "loopx/web/chat"));
const output = resolve(process.env.LOOPX_CHAT_CONFIRMATION_OUTPUT ?? resolve(root, "output/playwright/todo-confirmation"));
const browser = await launchBrowser(createRequire(import.meta.url)("playwright").chromium);
const textByWidth = {1200: "Reconcile delivery records after an interrupted confirmation",
  390: "Review the incident response guide after an interrupted confirmation"};

async function goalTab(page, label) {
  const tab = page.getByRole("navigation", {name: /Goal view|Goal 视图/})
    .getByRole("button", {name: label});
  const goal = page.locator(".personal-goal-link:visible").first();
  const nav = page.locator(".personal-mobile-menu:visible").first();
  await tab.or(goal).or(nav).first().waitFor();
  if (!await tab.isVisible()) {
    if (await nav.isVisible()) await nav.click();
    await goal.click();
  }
  await tab.click();
}

async function verifyPendingDisplay({page, post, origin, width, provider, snapshot, fixture = "/__confirmation_fixture/display"}) {
  await post(fixture, {hold: true});
  const original = await snapshot();
  const titles = width === 390
    ? ["核对欠付项的责任人与跟进记录", "保留交接文档中的原始验收证据"]
    : ["Review follow-up owners in the release checklist", "Preserve evidence for the support handover after release"];
  const operations = [];
  for (let i = 0; i < titles.length; i++) {
    const [created, preview] = await post("/api/actions/preview", {
      action_kind: "todo.create", context: {kind: "goal", goal_id: "goal-a"},
      normalized_parameters: {goal_id: "goal-a", text: titles[i]}, summary: "Create task",
      idempotency_key: "display-confirmation-" + width + "-" + i,
    });
    assert.equal(created, 201, JSON.stringify(preview));
    const id = preview.proposal.proposal_id;
    const path = "/api/actions/" + id;
    const [, pending] = await post(path + "/apply");
    assert.equal(pending.proposal.failure.error_code, "canonical_create_projection_pending");
    const createdStep = pending.proposal.checkpoint.steps.todo_created;
    assert.equal(createdStep.projection_delivery, "pending");
    operations.push({id, path, title: titles[i], todo: createdStep.todo_id, receipt: createdStep.canonical_receipt.receipt_id});
    assert.equal((await post(path + "/cancel"))[0], 409);
  }
  const committed = await snapshot();
  assert.equal(committed.ids.length, original.ids.length + 2);
  assert.notEqual(operations[0].todo, operations[1].todo);
  await page.goto(origin + "/chat/?goal=goal-a");
  await goalTab(page, /^(Overview|概览)$/);
  await page.reload();
  await goalTab(page, /^(Overview|概览)$/);
  const overview = page.getByRole("region", {name: /Display updates pending|展示待更新/});
  await overview.waitFor();
  for (const {title} of operations) await overview.getByRole("button", {name: title, exact: true}).waitFor();
  await page.screenshot({path: resolve(output, provider + "-" + width + "-display-overview.png")});
  await goalTab(page, /^(Chat|对话)$/);
  const cards = page.locator(".personal-proposal-row[data-action-kind='todo.create']");
  const card = title => cards.filter({hasText: title});
  // As with ordinary confirmations, these two persisted records have the
  // same action title. Both must remain selectable without first doing IO.
  for (const {title} of operations) await card(title).waitFor();
  await page.screenshot({path: resolve(output, provider + "-" + width + "-display-chat.png")});
  const [earlier, later] = operations;
  await card(later.title).click();
  const retry = page.getByRole("dialog").getByRole("button", {name: /^(Retry original operation|重试原操作)$/});
  await retry.waitFor();
  // Replace the upstream reply after apply ran. File finishes display, while
  // SQLite keeps it pending. Both temporarily lose the two readback routes.
  await post(fixture, {hold: provider === "sqlite", gateway_reply: later.id, hold_list: true, hold_detail: true});
  const gateway = page.waitForResponse(response => response.url().endsWith(later.path + "/apply"));
  const unavailable = [later.path, "/api/actions"].map(path => page.waitForResponse(response =>
    response.request().method() === "GET" && new URL(response.url()).pathname === path && response.status() === 503));
  await retry.click();
  const [reply] = await Promise.all([gateway, ...unavailable]);
  assert.equal(reply.status(), 502);
  assert.match(reply.headers()["content-type"], /^text\/html\b/);
  await retry.waitFor();
  assert.ok(await retry.isEnabled());
  assert.equal(await page.getByRole("dialog").getByRole("button", {name: /regenerate|重新生成/i}).count(), 0);
  await page.screenshot({path: resolve(output, provider + "-" + width + "-gateway-recovery.png")});
  await post(fixture, {hold: false, hold_list: false, hold_detail: false});
  function checkReceipt(proposal, operation) {
    assert.equal(proposal.proposal_id, operation.id);
    assert.equal(proposal.receipt.receipt_id, operation.receipt);
    assert.equal(proposal.receipt.resource_ids.todo_id, operation.todo);
    assert.equal(proposal.receipt.projection_verified, true);
  }
  async function recover(operation) {
    const outcome = page.waitForResponse(response => response.url().endsWith(operation.path + "/apply"));
    await retry.click();
    const response = await outcome;
    assert.equal(response.status(), 200);
    checkReceipt((await response.json()).proposal, operation);
    await page.keyboard.press("Escape");
  }
  const observed = (await (await fetch(origin + later.path)).json()).proposal;
  if (provider === "sqlite") {
    assert.equal(observed.failure.error_code, "canonical_create_projection_pending");
    assert.equal(observed.checkpoint.steps.todo_created.canonical_receipt.receipt_id, later.receipt);
  } else {
    checkReceipt(observed, later);
  }
  await recover(later);
  await goalTab(page, /^(Overview|概览)$/);
  await overview.getByRole("button", {name: earlier.title, exact: true}).click();
  // A connection drop with the list unavailable still uses exact readback.
  await post(fixture, {drop_reply: earlier.id, hold_list: true});
  const disconnected = page.waitForEvent("requestfailed", {
    predicate: request => request.method() === "POST" && request.url().endsWith(earlier.path + "/apply"),
  });
  await retry.click();
  await disconnected;
  await page.getByRole("dialog").getByRole("button", {name: /^(View updated Goal|查看更新后的 Goal)$/}).waitFor();
  const exact = await fetch(origin + earlier.path);
  checkReceipt((await exact.json()).proposal, earlier);
  await post(fixture, {hold_list: false});
  await page.keyboard.press("Escape");
  await overview.waitFor({state: "hidden"});
  assert.deepEqual(await snapshot(), committed);
  console.log(provider + " " + width + "px: HTML 502 and two failed reads keep the original retry; both receipts recovered");
}

async function verify(provider, interruptedStep) {
  const child = spawn(resolveTestPython({repoRoot: root}),
    ["-u", fileURLToPath(new URL("todo-confirmation-http-fixture.py", import.meta.url)), provider, assetDir, interruptedStep],
    {cwd: root, stdio: ["ignore", "pipe", "inherit"]});
  const exited = once(child, "exit");
  const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
  let timer;
  const lostRequest = interruptedStep === "applying";
  const beforeWrite = interruptedStep !== "todo_created";
  const capturePrefix = provider + "-" + interruptedStep;
  try {
    const origin = await Promise.race([
      (async () => {
        for await (const line of { [Symbol.asyncIterator]: () => lines }) {
          if (line.startsWith("{")) return JSON.parse(line).origin;
        }
        throw new Error("Chat test server exited before it reported its port.");
      })(),
      new Promise((_, reject) => {timer = setTimeout(() => reject(new Error("Chat did not start")), 30_000);}),
    ]).finally(() => clearTimeout(timer));
    const post = async (path, body = {}) => {
      const response = await fetch(origin + path, {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify(body)});
      return [response.status, await response.json()];
    };
    const snapshot = () => fetch(origin + "/__confirmation_fixture").then(response => response.json());
    for (const width of [1200, 390]) {
      const page = await browser.newPage({viewport: {width, height: 854}});
      page.setDefaultTimeout(12_000);
      try {
        const locale = width === 390 ? "zh-CN" : "en";
        await page.addInitScript(value => localStorage.setItem("loopx-pw-locale", value), locale);
        const text = textByWidth[width];
        const [status, preview] = await post("/api/actions/preview", {
          action_kind: "todo.create", context: {kind: "goal", goal_id: "goal-a"},
          normalized_parameters: {goal_id: "goal-a", text}, summary: text,
          idempotency_key: "interrupted-confirmation-" + width,
        });
        assert.equal(status, 201, JSON.stringify(preview));
        const id = preview.proposal.proposal_id;
        const path = "/api/actions/" + id;
        const attempted = [];
        page.on("request", request => {
          const url = new URL(request.url());
          if (url.pathname.startsWith("/api/actions") && request.method() === "POST") attempted.push(url.pathname);
        });
        await page.goto(origin + "/chat/?goal=goal-a");
        async function openConfirmation() {
          await goalTab(page, /^(Chat|对话)$/);
          const card = page.locator(".personal-proposal-row").filter({hasText: text});
          await card.first().click();
        }
        await openConfirmation();
        const drawer = page.getByRole("dialog");
        const initial = await snapshot();
        const injected = lostRequest
          ? page.waitForEvent("requestfailed", {predicate: request => request.url().endsWith(path + "/apply")})
          : page.waitForResponse(response => response.url().endsWith(path + "/apply"));
        await drawer.getByRole("button", {name: /^(Confirm and apply|确认并应用)$/}).click();
        const failure = await injected;
        const saved = lostRequest
          ? (await (await fetch(origin + path)).json()).proposal
          : (await failure.json()).proposal;
        assert.equal(saved.proposal_id, id);
        if (lostRequest) {
          assert.equal(saved.status, "applying");
          assert.equal(saved.failure, null);
        } else {
          assert.equal(failure.status(), 424);
          assert.equal(saved.failure.error_code, "canonical_action_failed");
          assert.equal(saved.failure.retry_safe, true);
        }
        const interrupted = await snapshot();
        const lost = interrupted.lost[id];
        if (beforeWrite) {
          assert.equal(saved.checkpoint, null);
          assert.deepEqual(interrupted, initial);
        } else {
          assert.ok(lost?.receipt_id && interrupted.ids.includes(lost.todo_id));
          assert.equal(interrupted.ids.length, initial.ids.length + 1);
          assert.notEqual(interrupted.provider_revision, initial.provider_revision);
        }
        const retry = drawer.getByRole("button", {name: /^(Retry original operation|重试原操作)$/});
        for (const alternative of ["cancel", "defer", "reject", "regenerate"]) {
          const [code, value] = await post(path + "/" + alternative);
          assert.deepEqual([code, value.error_code], [409, "action_conflict"], alternative);
        }
        await page.reload();
        await openConfirmation();
        await retry.waitFor();
        assert.equal(await drawer.getByRole("button", {name: /regenerate|重新生成/i}).count(), 0);
        await page.screenshot({path: resolve(output, capturePrefix + "-" + width + "-recovery.png")});
        const recoveredResponse = page.waitForResponse(response => response.url().endsWith(path + "/apply"));
        await retry.click();
        const response = await recoveredResponse;
        assert.equal(response.status(), 200);
        const {proposal} = await response.json();
        assert.equal(proposal.proposal_id, id);
        assert.equal(proposal.receipt.outcome, "todo_added");
        const recovered = await snapshot();
        if (beforeWrite) {
          const todoId = proposal.receipt.resource_ids.todo_id;
          assert.ok(todoId && proposal.receipt.receipt_id);
          assert.deepEqual(recovered.ids, [...initial.ids, todoId].sort());
          assert.notEqual(recovered.provider_revision, initial.provider_revision);
        } else {
          assert.equal(proposal.receipt.receipt_id, lost.receipt_id);
          assert.equal(proposal.receipt.resource_ids.todo_id, lost.todo_id);
          assert.deepEqual(recovered, interrupted);
        }
        assert.deepEqual(attempted, [path + "/apply", path + "/apply"]);
        if (lostRequest) {
          const [code, replay] = await post(path + "/apply");
          assert.equal(code, 200);
          assert.deepEqual(replay.proposal.receipt, proposal.receipt);
          assert.deepEqual(await snapshot(), recovered);
        }
        await drawer.getByRole("button", {name: /^(View updated Goal|查看更新后的 Goal)$/}).waitFor();
        await page.screenshot({path: resolve(output, capturePrefix + "-" + width + "-result.png")});
        console.log(provider + " " + width + "px: " + interruptedStep + "; original proposal and one Todo write across reload");
        if (!beforeWrite) await verifyPendingDisplay({page, post, origin, width, provider, snapshot});
      } catch (error) {
        await page.screenshot({path: resolve(output, capturePrefix + "-" + width + "-failure.png")});
        error.message += "\n" + (await page.locator("body").innerText()).slice(0, 1000);
        throw error;
      } finally { await page.close(); }
    }
    const intent = {action_kind: "todo.create", context: {kind: "goal", goal_id: "goal-a"},
      normalized_parameters: {goal_id: "goal-a", text: "Refresh a preview that was never confirmed"},
      summary: "Refresh a preview", idempotency_key: "unsubmitted-preview"};
    const preview = (await post("/api/actions/preview", intent))[1].proposal;
    // The fixture advances the real revision through a separate Todo.
    await post("/__confirmation_fixture/advance");
    const path = "/api/actions/" + preview.proposal_id;
    const [code, rejected] = await post(path + "/apply");
    assert.equal(code, 409);
    assert.equal(rejected.proposal.status, "stale");
    assert.equal(rejected.proposal.checkpoint, null);
    const [created, successor] = await post(path + "/regenerate");
    assert.equal(created, 201);
    assert.equal(successor.proposal.regenerated_from, preview.proposal_id);
    assert.notEqual(successor.proposal.proposal_id, preview.proposal_id);
    if (interruptedStep === "todo_created") {
      const display = "/__confirmation_fixture/display";
      const [previewCode, monitor] = await post("/api/actions/preview", {
        action_kind: "monitor.create", context: {kind: "goal", goal_id: "goal-a"},
        normalized_parameters: {goal_id: "goal-a", agent_id: "agent-a", target: "Review release readiness",
          target_key: "release-readiness", cadence: "2h", timezone: "UTC", stop_condition: "watch_only"},
        summary: "Review release readiness", idempotency_key: "monitor-display-recovery",
      });
      assert.equal(previewCode, 201);
      const monitorPath = "/api/actions/" + monitor.proposal.proposal_id;
      const original = await snapshot();
      await post(display, {hold: true});
      const [, pending] = await post(monitorPath + "/apply");
      const saved = pending.proposal.checkpoint.steps.todo_created;
      assert.equal(pending.proposal.failure.error_code, "canonical_create_projection_pending");
      const committed = await snapshot();
      assert.deepEqual(committed.ids, [...original.ids, saved.todo_id].sort());
      assert.equal((await post(monitorPath + "/cancel"))[0], 409);
      assert.deepEqual(await snapshot(), committed);
      await post(display, {hold: false});
      const [applied, recovered] = await post(monitorPath + "/apply");
      assert.equal(applied, 200);
      assert.equal(recovered.proposal.receipt.resource_ids.todo_id, saved.todo_id);
      assert.equal(recovered.proposal.receipt.projection_verified, true);
      assert.deepEqual(await snapshot(), committed);
      console.log(provider + ": the Monitor cancel returns 409 and the original confirmation recovers its display");
    }
  } finally {
    child.kill("SIGINT");
    await exited;
  }
}

try {
  await mkdir(output, {recursive: true});
  for (const interruptedStep of ["applying", "todo_create_prepared", "todo_created"]) {
    for (const provider of ["file", "sqlite"]) await verify(provider, interruptedStep);
  }
} finally {
  await browser.close();
}
