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

async function verify(provider) {
  const child = spawn(resolveTestPython({repoRoot: root}),
    ["-u", fileURLToPath(new URL("todo-confirmation-http-fixture.py", import.meta.url)), provider, assetDir],
    {cwd: root, stdio: ["ignore", "pipe", "inherit"]});
  const exited = once(child, "exit");
  const lines = createInterface({input: child.stdout})[Symbol.asyncIterator]();
  let timer;
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
          const chat = page.getByRole("navigation", {name: /Goal view|Goal 视图/})
            .getByRole("button", {name: /^(Chat|对话)$/});
          const goal = page.locator(".personal-goal-link:visible").first();
          const nav = page.locator(".personal-mobile-menu:visible").first();
          await chat.or(goal).or(nav).first().waitFor();
          if (!await chat.isVisible()) {
            if (await nav.isVisible()) await nav.click();
            await goal.click();
          }
          await chat.click();
          const card = page.locator(".personal-proposal-row").filter({hasText: text});
          await card.first().click();
        }
        await openConfirmation();
        const drawer = page.getByRole("dialog");
        const initial = await snapshot();
        const injected = page.waitForResponse(response => response.url().endsWith(path + "/apply"));
        await drawer.getByRole("button", {name: /^(Confirm and apply|确认并应用)$/}).click();
        assert.equal((await injected).status(), 424);
        const committed = await snapshot();
        const lost = committed.lost[id];
        assert.ok(lost?.receipt_id && committed.ids.includes(lost.todo_id));
        assert.equal(committed.ids.length, initial.ids.length + 1);
        assert.notEqual(committed.provider_revision, initial.provider_revision);
        const retry = drawer.getByRole("button", {name: /^(Retry original operation|重试原操作)$/});
        await retry.waitFor();
        assert.deepEqual(await post(path + "/regenerate").then(([code, value]) => [code, value.error_code]), [409, "action_conflict"]);
        assert.deepEqual(await post(path + "/cancel").then(([code, value]) => [code, value.error_code]), [409, "action_conflict"]);
        await page.reload();
        await openConfirmation();
        await retry.waitFor();
        assert.equal(await drawer.getByRole("button", {name: /regenerate|重新生成/i}).count(), 0);
        await page.screenshot({path: resolve(output, provider + "-" + width + "-recovery.png")});
        const recoveredResponse = page.waitForResponse(response => response.url().endsWith(path + "/apply"));
        await retry.click();
        const response = await recoveredResponse;
        assert.equal(response.status(), 200);
        const {proposal} = await response.json();
        assert.equal(proposal.proposal_id, id);
        assert.equal(proposal.receipt.receipt_id, lost.receipt_id);
        assert.equal(proposal.receipt.resource_ids.todo_id, lost.todo_id);
        assert.equal(proposal.receipt.outcome, "todo_added");
        assert.deepEqual(await snapshot(), committed);
        assert.deepEqual(attempted, [path + "/apply", path + "/apply"]);
        await drawer.getByRole("button", {name: /^(View updated Goal|查看更新后的 Goal)$/}).waitFor();
        await page.screenshot({path: resolve(output, provider + "-" + width + "-result.png")});
        console.log(provider + " " + width + "px: original proposal, Todo and receipt; one write across reload");
      } catch (error) {
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
  } finally {
    child.kill("SIGINT");
    await exited;
  }
}

try {
  await mkdir(output, {recursive: true});
  for (const provider of ["file", "sqlite"]) await verify(provider);
} finally {
  await browser.close();
}
