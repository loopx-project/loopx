import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Needs-you desktop notifications are a per-browser opt-in. Enabling asks the
// browser once and seeds what already waits; from then on the page re-reads
// the workspace in the background and notifies only for a new item that waits
// on the owner while they are not looking. Turning it off stops both. The
// browser API is stubbed so the scenario observes exactly what the page sends.

const gateText = "Approve the restated FY2023 operating cash flow";

function installNotificationStub(page) {
  return page.addInitScript(() => {
    window.__loopxNotifications = [];
    window.__loopxPermissionRequests = 0;
    class FakeNotification {
      static permission = "default";
      static async requestPermission() { window.__loopxPermissionRequests += 1; FakeNotification.permission = "granted"; return "granted"; }
      constructor(title, options) { this.title = title; this.body = options?.body ?? ""; this.tag = options?.tag ?? ""; this.closed = false; window.__loopxNotifications.push(this); }
      close() { this.closed = true; }
    }
    window.Notification = FakeNotification;
    // The owner is elsewhere: the page is visible in a window that lost focus.
    document.hasFocus = () => false;
  });
}

function addGate(fixture, todoId, text) {
  const first = fixture.attention_queue.items[0];
  first.waiting_on = "user_or_controller";
  first.user_todos ??= { items: [], open_count: 0, source_section: "User Todo", total_count: 0 };
  first.user_todos.items.push({ done: false, status: "open", goal_id: first.goal_id, index: first.user_todos.items.length, role: "user", task_class: "user_gate", todo_id: todoId, text, updated_at: new Date().toISOString() });
  first.user_todos.open_count = first.user_todos.items.filter((todo) => !todo.done).length;
  first.user_todos.total_count = first.user_todos.items.length;
}

export const attentionNotificationsScenario = {
  id: "attention-notifications",
  async run({ browser, collectCoverage, url }) {
    const session = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: (_api, page) => installNotificationStub(page) });
    const { api, page } = session;
    const notifications = () => page.evaluate(() => window.__loopxNotifications.map((row) => ({ title: row.title, body: row.body, tag: row.tag })));
    try {
      // Off by default: no permission request, no notification, nothing stored.
      assert.equal(await page.evaluate(() => window.__loopxPermissionRequests), 0);
      assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), null);

      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "外观", exact: true }).click();
      const settings = page.getByTestId("attention-notification-settings");
      const toggle = settings.getByRole("checkbox");
      const permission = page.getByTestId("attention-notification-permission");
      await settings.waitFor({ state: "visible" });
      assert.equal(await toggle.isChecked(), false);
      assert.match(await permission.innerText(), /开启时浏览器会请求通知权限/);

      // Timers scheduled from now on are fake, so the 30 s re-read can be fired on demand.
      await page.clock.install();
      await toggle.click();
      await page.waitForFunction(() => document.querySelector('[data-testid="attention-notification-permission"]')?.textContent?.includes("已允许"));
      assert.equal(await toggle.isChecked(), true);
      assert.equal(await page.evaluate(() => window.__loopxPermissionRequests), 1, "The toggle asks the browser exactly once");
      assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), "on");
      assert.equal((await notifications()).length, 0, "Enabling never replays items already waiting");
      await page.screenshot({ path: resolve(outputDir, "attention-notifications-settings.png"), animations: "disabled" });
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();

      // A decision raised elsewhere arrives through the background re-read.
      const readsBefore = api.statusRequestCount;
      api.transformStatus = (fixture) => addGate(fixture, "gate-restatement", gateText);
      await page.clock.runFor(31_000);
      await page.waitForFunction(() => window.__loopxNotifications.length > 0, null, { timeout: 15_000 });
      assert.ok(api.statusRequestCount > readsBefore, "The page re-read the workspace without a user action");
      const [first] = await notifications();
      assert.equal(first.body, gateText, "The notification carries the item in owner language");
      assert.ok(first.title.length > 0);
      assert.equal((await notifications()).length, 1);

      // The same item does not notify again on the next re-read.
      await page.clock.runFor(31_000);
      await page.waitForTimeout(1_500);
      assert.equal((await notifications()).length, 1, "An item already seen is not re-notified");

      // Clicking the notification returns to the item in place.
      await page.evaluate(() => window.__loopxNotifications[0].onclick());
      const drawer = page.locator(".personal-context-drawer");
      await drawer.waitFor({ state: "visible", timeout: 10_000 });
      assert.match(await drawer.innerText(), new RegExp(gateText.slice(0, 24)));
      assert.equal(await page.evaluate(() => window.__loopxNotifications[0].closed), true);
      await page.screenshot({ path: resolve(outputDir, "attention-notifications-opened.png"), animations: "disabled" });

      // Turning it off stops the background re-read and any further notification.
      await page.getByRole("button", { name: "设置", exact: true }).click();
      await page.locator(".personal-settings-tabs").getByRole("button", { name: "外观", exact: true }).click();
      await toggle.click();
      assert.equal(await toggle.isChecked(), false);
      assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), null);
      await page.getByRole("button", { name: "返回工作区", exact: true }).click();
      const readsAfterOff = api.statusRequestCount;
      api.transformStatus = (fixture) => { addGate(fixture, "gate-restatement", gateText); addGate(fixture, "gate-second", "Choose the segment disclosure source"); };
      await page.clock.runFor(70_000);
      await page.waitForTimeout(1_500);
      assert.equal(api.statusRequestCount, readsAfterOff, "No background re-read while notifications are off");
      assert.equal((await notifications()).length, 1, "Nothing notifies while off");

      // The preference survives a reload and re-seeds instead of replaying.
      await page.evaluate(() => localStorage.setItem("loopx-pw-attention-notifications", "on"));
      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("personal-goal-home").waitFor({ state: "visible", timeout: 15_000 });
      await page.waitForTimeout(1_000);
      assert.equal((await notifications()).length, 0, "A reload with items already waiting notifies nothing");
      return { coverageEntries: await session.close() };
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};
