import assert from "node:assert/strict";
import { openWorkspacePage } from "./scenario-context.mjs";

const actionText = "在桌面 App 中手动创建剩余的 3 个角色会话";
const idlessText = "核对本机备份目录是否可写";

// A User action in "needs you" is handled in place through the existing typed
// Todo actions: done / defer / no longer needed, each previewed and confirmed once.
export const attentionUserActionScenario = {
  id: "attention-user-action",
  async run({ browser, collectCoverage, url }) {
    const { api, page, close, errors } = await openWorkspacePage(browser, url, { apiOptions: { userActionAttention: true }, collectCoverage });
    const drawer = page.locator(".personal-context-drawer");
    async function openRequest(text) {
      await page.getByRole("button", { name: /LoopX 管家/ }).first().click();
      await page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
      await page.getByRole("button", { name: /^任务$/ }).first().click();
      await page.getByText(text, { exact: true }).first().click();
      await drawer.getByText(text, { exact: true }).first().waitFor({ state: "visible" });
    }
    try {
      await openRequest(actionText);
      for (const forbidden of ["批准", "拒绝", "解释此决定"]) {
        assert.equal(await drawer.getByRole("button", { name: forbidden, exact: true }).count(), 0, `A User action must not offer "${forbidden}"`);
      }
      const done = drawer.getByRole("button", { name: "我已完成", exact: true });
      await drawer.getByRole("button", { name: "在对话中回复", exact: true }).waitFor({ state: "visible" });

      // Failure: nothing is written, the error is visible, and the same action recovers.
      const writesBefore = api.durableWriteCount;
      api.failNextActionPreview = true;
      await done.click();
      await drawer.getByRole("alert").getByText(/未能准备这项变更.*没有保存任何内容/u).waitFor({ state: "visible" });
      assert.equal(api.durableWriteCount, writesBefore, "A failed preview must not write");
      await done.click();
      await page.locator('[data-context-kind="proposal"]').waitFor({ state: "visible" });
      const completion = api.actionPreviews.at(-1);
      assert.equal(completion.action_kind, "todo.update");
      assert.equal(completion.normalized_parameters.operation, "complete");
      assert.equal(completion.normalized_parameters.todo_id, "todo-browser-user-action");
      assert.equal(api.durableWriteCount, writesBefore, "Preview waits for owner confirmation");
      await page.locator('[data-context-kind="proposal"]').getByRole("button", { name: "确认并应用" }).click();
      await page.getByRole("button", { name: "查看更新后的 Goal" }).click();
      assert.equal(api.durableWriteCount, writesBefore + 1, "Confirmation writes exactly once");
      await page.getByRole("button", { name: /^任务$/ }).first().click();
      await page.getByText(idlessText, { exact: true }).first().waitFor({ state: "visible" });
      assert.equal(await page.getByText(actionText, { exact: true }).count(), 0, "The completed request leaves needs-you on readback");

      // Without a stable todo_id the drawer explains why and offers the conversation instead.
      await openRequest(idlessText);
      await drawer.getByText(/缺少稳定的 Todo 标识/u).waitFor({ state: "visible" });
      assert.equal(await drawer.getByRole("button", { name: "我已完成", exact: true }).count(), 0, "No write without a stable Todo identity");
      const previews = api.actionPreviews.length;
      await drawer.getByRole("button", { name: "在对话中回复", exact: true }).click();
      await page.locator('[data-goal-panel="chat"]').waitFor({ state: "visible" });
      await page.waitForFunction((expected) => document.querySelector("textarea")?.value === expected, `关于「${idlessText}」：`);
      assert.equal(api.actionPreviews.length, previews, "Replying drafts a message; it previews no write");
      assert.equal(api.turnRequests.length, 0, "Replying never sends without the owner");

      // Defer and "no longer needed" reuse the same owners on a fresh request.
      const fresh = await openWorkspacePage(browser, url, { apiOptions: { userActionAttention: true } });
      try {
        const freshDrawer = fresh.page.locator(".personal-context-drawer");
        await fresh.page.getByRole("button", { name: /LoopX 管家/ }).first().click();
        await fresh.page.getByTestId("personal-home-lane-needs_you").locator(".personal-home-goal-card").first().click();
        await fresh.page.getByRole("button", { name: /^任务$/ }).first().click();
        for (const [label, check] of [
          ["暂缓到明天 9:00", (preview) => {
            assert.equal(preview.action_kind, "todo.update");
            assert.equal(preview.normalized_parameters.operation, "defer");
            assert.match(preview.normalized_parameters.resume_when, /^resume_at:\d{4}-\d{2}-\d{2}T09:00:00[+-]\d{2}:\d{2}$/u);
            assert.ok(Date.parse(preview.normalized_parameters.resume_when.slice(10)) > Date.now());
          }],
          ["不再需要", (preview) => {
            assert.equal(preview.action_kind, "gate.resolve");
            assert.equal(preview.normalized_parameters.decision, "cancel");
            assert.equal(preview.normalized_parameters.todo_id, "todo-browser-user-action");
          }],
        ]) {
          await fresh.page.getByText(actionText, { exact: true }).first().click();
          await freshDrawer.locator("summary", { hasText: "其他处理" }).click();
          await freshDrawer.getByRole("button", { name: label, exact: true }).click();
          await fresh.page.locator('[data-context-kind="proposal"]').waitFor({ state: "visible" });
          check(fresh.api.actionPreviews.at(-1));
          await fresh.page.locator(".personal-drawer-close").click();
        }
        assert.equal(fresh.api.durableWriteCount, 0, "Opening previews never writes");
      } finally {
        await fresh.close();
      }

      // A User gate keeps its approve/reject decision.
      await openRequest("确认本轮独立审查范围");
      await drawer.getByRole("button", { name: "批准", exact: true }).waitFor({ state: "visible" });
      assert.equal(await drawer.getByRole("button", { name: "我已完成", exact: true }).count(), 0);
      assert.deepEqual(errors.filter((message) => !/Failed to load resource/u.test(message)), [], "Only the injected preview failure may log");
      return { coverageEntries: await close(), note: "User action done/defer/cancel/reply with failure recovery and readback" };
    } catch (error) {
      await close();
      throw error;
    }
  },
};
