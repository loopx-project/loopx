import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

// Needs-you desktop notifications are a per-browser opt-in, surfaced as a bell
// in the sidebar footer: a labelled button whose pressed state, class, title
// and dot are the whole surface. Enabling asks the browser once and seeds what
// already waits; from then on the page re-reads the workspace in the background
// and notifies only for a new item that waits on the owner while they are not
// looking. Turning it off stops both. The browser API is stubbed so the
// scenario observes exactly what the page sends.

const gateText = "Approve the restated FY2023 operating cash flow";
// The bell is found by its accessible name, the same way a person reads it.
const bellName = "桌面通知"; // t("settings.attentionNotifications")
const bellToggleTitle = "有事项需要我决定时提醒我"; // t("settings.attentionNotificationsToggle")
const bellHintTitle = "仅在本页面在此浏览器中保持打开时生效。进展和运行不会提醒，只提醒新出现的、等你决定的事项。开启后页面每 30 秒重新读取一次工作区。"; // t("settings.attentionNotificationsHint")
const bellSelector = `button[aria-label="${bellName}"]`;

function installNotificationStub(page, { permission = "default" } = {}) {
  return page.addInitScript((initial) => {
    window.__loopxNotifications = [];
    window.__loopxPermissionRequests = 0;
    class FakeNotification {
      static permission = initial;
      static async requestPermission() { window.__loopxPermissionRequests += 1; FakeNotification.permission = "granted"; return "granted"; }
      constructor(title, options) { this.title = title; this.body = options?.body ?? ""; this.tag = options?.tag ?? ""; this.closed = false; window.__loopxNotifications.push(this); }
      close() { this.closed = true; }
    }
    window.Notification = FakeNotification;
    // The owner is elsewhere: the page is visible in a window that lost focus.
    document.hasFocus = () => false;
  }, permission);
}

function addGate(fixture, todoId, text) {
  const first = fixture.attention_queue.items[0];
  first.waiting_on = "user_or_controller";
  first.user_todos ??= { items: [], open_count: 0, source_section: "User Todo", total_count: 0 };
  first.user_todos.items.push({ done: false, status: "open", goal_id: first.goal_id, index: first.user_todos.items.length, role: "user", task_class: "user_gate", todo_id: todoId, text, updated_at: new Date().toISOString() });
  first.user_todos.open_count = first.user_todos.items.filter((todo) => !todo.done).length;
  first.user_todos.total_count = first.user_todos.items.length;
}

// The bell is the owner's whole notifications surface: aria-pressed says on or
// off, the is-* class says which square it is in (off/on/blocked), title carries
// the sentence the settings block used to print, disabled means this browser
// blocks it, and the dot marks on. Read it the way a person would see it.
function notificationBell(page) {
  return page.getByRole("button", { name: bellName, exact: true });
}

async function expectBell(page, { pressed, state, disabled, title, dot }) {
  const expected = { pressed, state, disabled, title, dot };
  try {
    await page.waitForFunction(
      ({ selector, pressed, state, disabled, title }) => {
        const node = document.querySelector(selector);
        return Boolean(node)
          && node.getAttribute("aria-pressed") === String(pressed)
          && node.classList.contains(`is-${state}`)
          && node.disabled === disabled
          && node.getAttribute("title") === title;
      },
      { selector: bellSelector, pressed, state, disabled, title },
    );
  } catch (error) {
    const actual = await page.evaluate(({ selector }) => {
      const node = document.querySelector(selector);
      return node ? { label: node.getAttribute("aria-label"), pressed: node.getAttribute("aria-pressed"), className: node.getAttribute("class"), title: node.getAttribute("title"), disabled: node.disabled, dots: node.querySelectorAll(".personal-notification-dot").length } : null;
    }, { selector: bellSelector }).catch(() => "unreadable");
    throw new Error(`The bell never reached ${JSON.stringify(expected)}; it read ${JSON.stringify(actual)}; ${error.message}`);
  }
  const bell = notificationBell(page);
  const className = await bell.getAttribute("class");
  assert.equal(await bell.getAttribute("aria-label"), bellName, "The bell names itself with the notifications label");
  assert.equal(await bell.getAttribute("aria-pressed"), String(pressed), `The bell reports ${pressed ? "on" : "off"}`);
  assert.match(className ?? "", new RegExp(`\\bis-${state}\\b`), `The bell's class reports ${state}`);
  assert.equal(await bell.getAttribute("title"), title, "The bell's title carries this state's sentence");
  assert.equal(await bell.isDisabled(), disabled, disabled ? "A browser that blocks notifications disables the bell" : "The bell is offered");
  assert.equal(await bell.locator(".personal-notification-dot").count(), dot ? 1 : 0, dot ? "An on bell shows its dot" : "An off bell shows no dot");
  return bell;
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

      const bell = notificationBell(page);
      await bell.waitFor({ state: "visible", timeout: 15_000 });
      const offBell = { pressed: false, state: "off", disabled: false, title: bellToggleTitle, dot: false };
      await expectBell(page, offBell);

      // Timers scheduled from now on are fake, so the 30 s re-read can be fired on demand.
      await page.clock.install();
      await bell.click();
      await expectBell(page, { pressed: true, state: "on", disabled: false, title: bellHintTitle, dot: true });
      assert.equal(await page.evaluate(() => window.__loopxPermissionRequests), 1, "The bell asks the browser exactly once");
      assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), "on");
      assert.equal((await notifications()).length, 0, "Enabling never replays items already waiting");
      await page.screenshot({ path: resolve(outputDir, "attention-notifications-bell.png"), animations: "disabled" });

      // A partial read that omits a gate already seen at enable time, then a
      // full read that lists it again, is not a new decision: identities seen
      // while on are kept, so nothing is replayed.
      const partialOmitsSeededGate = (fixture) => {
        const first = fixture.attention_queue.items[0];
        for (const group of [first.user_todos, first.project_asset?.user_todos]) {
          if (!group?.items) continue;
          const before = group.items.filter((todo) => !todo.done).length;
          group.items = group.items.filter((todo) => todo.todo_id !== "todo-browser-user-gate");
          if ("open_count" in group) group.open_count = before;
          if ("open" in group) group.open = before;
        }
      };
      api.transformStatus = partialOmitsSeededGate;
      await page.clock.runFor(31_000);
      await page.waitForTimeout(1_000);
      api.transformStatus = null;
      await page.clock.runFor(31_000);
      await page.waitForTimeout(1_000);
      assert.equal((await notifications()).length, 0, "A gate seen at enable time is not replayed after a partial read omitted it");

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
      // Close the item the way a person would, then reach the sidebar bell.
      await page.keyboard.press("Escape");
      await drawer.waitFor({ state: "hidden", timeout: 10_000 });

      // Turning it off stops the background re-read and any further notification.
      await bell.click();
      await expectBell(page, offBell);
      assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), null);
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
      await expectBell(page, { pressed: true, state: "on", disabled: false, title: bellHintTitle, dot: true });
      assert.equal((await notifications()).length, 0, "A reload with items already waiting notifies nothing");
      const coverageEntries = await session.close();

      // A browser that blocked notifications can be allowed again in its site
      // settings without reloading: returning to the page reads the answer
      // back, the bell becomes usable, and turning it on asks nothing more. A
      // revocation while on reads back the same way and stops the re-read.
      const denied = await openWorkspacePage(browser, url, { collectCoverage, viewport: { width: 1440, height: 900 }, beforeGoto: (_api, page) => installNotificationStub(page, { permission: "denied" }) });
      try {
        const { api: deniedApi, page } = denied;
        const bell = notificationBell(page);
        await bell.waitFor({ state: "visible", timeout: 15_000 });
        // Blocked: the same bell is offered but disabled and in its blocked square.
        await expectBell(page, { pressed: false, state: "blocked", disabled: true, title: bellToggleTitle, dot: false });
        await page.evaluate(() => { window.Notification.permission = "granted"; window.dispatchEvent(new Event("focus")); });
        await expectBell(page, { pressed: false, state: "off", disabled: false, title: bellToggleTitle, dot: false });
        await page.clock.install();
        await bell.click();
        await expectBell(page, { pressed: true, state: "on", disabled: false, title: bellHintTitle, dot: true });
        assert.equal(await page.evaluate(() => window.__loopxPermissionRequests), 0, "An already granted browser is not asked again");
        const readsBefore = deniedApi.statusRequestCount;
        await page.clock.runFor(31_000);
        await page.waitForTimeout(1_000);
        assert.ok(deniedApi.statusRequestCount > readsBefore, "Enabled after recovery, the page re-reads in the background");
        // Revoked in the site settings while on: read back on return, stop quietly.
        await page.evaluate(() => { window.Notification.permission = "denied"; window.dispatchEvent(new Event("focus")); });
        // The preference is not rewritten behind the owner: the bell stays on
        // (pressed, dot) but is blocked, which is how a revocation shows here.
        await expectBell(page, { pressed: true, state: "on", disabled: true, title: bellHintTitle, dot: true });
        const readsAfterRevoke = deniedApi.statusRequestCount;
        await page.clock.runFor(70_000);
        await page.waitForTimeout(1_000);
        assert.equal(deniedApi.statusRequestCount, readsAfterRevoke, "A revoked permission stops the background re-read");
        assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), "on", "A revocation does not clear the stored preference");
        // Allowed again, the bell is usable and the owner can turn it off.
        await page.evaluate(() => { window.Notification.permission = "granted"; window.dispatchEvent(new Event("focus")); });
        await expectBell(page, { pressed: true, state: "on", disabled: false, title: bellHintTitle, dot: true });
        await bell.click();
        await expectBell(page, { pressed: false, state: "off", disabled: false, title: bellToggleTitle, dot: false });
        assert.equal(await page.evaluate(() => localStorage.getItem("loopx-pw-attention-notifications")), null, "Turning it off clears the stored preference");
        return { coverageEntries: [...coverageEntries, ...await denied.close()] };
      } catch (error) {
        await denied.close().catch(() => {});
        throw error;
      }
    } catch (error) {
      await session.close().catch(() => {});
      throw error;
    }
  },
};
