import {resolve} from "node:path";
import {outputDir} from "./fixture.mjs";
import {openWorkspacePage} from "./scenario-context.mjs";

export const stewardGroupTriggerScenario = {
  id: "steward-group-trigger",
  async run({browser, collectCoverage, url}) {
    const context = await openWorkspacePage(browser, url, {collectCoverage});
    const {page, api} = context;
    try {
      await page.getByRole("button", {name: "设置", exact: true}).click();
      await page.locator(".personal-settings-tabs").getByRole("button", {name: "Lark", exact: true}).click();
      await page.getByRole("button", {name: "连接 Lark App", exact: true}).click();
      const dialog = page.getByRole("dialog", {name: "连接 Lark App"});
      await dialog.getByRole("option", {name: "Product group"}).waitFor({state: "attached"});
      await dialog.getByLabel("群聊").selectOption({label: "Product group"});
      const trigger = dialog.getByLabel("何时回应");
      if (await trigger.inputValue() !== "addressed") throw new Error("Legacy mention default changed");
      await trigger.selectOption("human_messages");
      await page.screenshot({path: resolve(outputDir, "steward-group-trigger.png"), animations: "disabled"});
      await dialog.getByRole("button", {name: "连接", exact: true}).click();
      await dialog.waitFor({state: "hidden"});
      const row = page.locator(".personal-lark-table-row", {hasText: "Product group"});
      await row.getByText("群成员直接发消息，无需 @", {exact: true}).waitFor();
      await row.getByText("监听已连接，尚无新消息验证这条连接。请在群里直接发一条任务，无需 @；若没有收到事件，再检查 im.message.receive_v1 和接收群内所有消息的权限。", {exact: true}).waitFor();
      if (api.larkWrites.length !== 1 || api.larkWrites[0].turn_trigger !== "human_messages") throw new Error("Trigger not saved");
      const connection = api.larkConnections[0];
      for (const [reason, detail] of [
        ["not_addressed", "上条消息没有启动任务。免 @ 已开启，可直接发送新任务；改设置不会自动补跑旧消息。"],
        ["historical_context_only", "历史消息用于上下文，不会启动任务。请直接发送一条新任务。"],
        ["bot_message", "机器人消息仅用于上下文，不会互相触发任务。"],
        ["human_identity_unverified", "消息已保存，但无法验证发送者身份，没有启动任务。请检查 Lark 消息事件的发送者信息。"],
      ]) {
        Object.assign(connection, {event_count: 3, last_event_status: "context_only_captured", last_event_reason: reason});
        await page.reload();
        await page.getByRole("button", {name: "设置", exact: true}).click();
        await page.locator(".personal-settings-tabs").getByRole("button", {name: "Lark", exact: true}).click();
        const feedback = row.getByText(detail, {exact: true});
        await feedback.waitFor();
        if (await feedback.evaluate((element) => getComputedStyle(element).whiteSpace) === "nowrap") throw new Error("Recovery feedback is visually truncated");
      }
      await page.screenshot({path: resolve(outputDir, "steward-group-trigger-feedback.png"), animations: "disabled"});
      await row.getByRole("button", {name: /配置/}).click();
      const editor = page.getByRole("dialog");
      if (await editor.getByLabel("何时回应").inputValue() !== "human_messages") throw new Error("Trigger not read back");
      await page.setViewportSize({width: 390, height: 844});
      await editor.getByLabel("何时回应").scrollIntoViewIfNeeded();
      await page.screenshot({path: resolve(outputDir, "steward-group-trigger-mobile.png"), animations: "disabled"});
      await editor.getByLabel("何时回应").selectOption("addressed");
      await editor.getByRole("button", {name: "保存连接", exact: true}).click();
      await editor.waitFor({state: "hidden"});
      await row.getByText("仅 @ 或回复管家时", {exact: true}).waitFor();
      if (api.larkWrites[1]?.turn_trigger !== "addressed") throw new Error("Trigger not restored");
      if (context.errors.length) throw new Error(context.errors.join(" | "));
      return {coverageEntries: await context.close(), note: "Steward group trigger opt-in/readback/revocation and typed non-execution feedback; desktop and narrow screen; scripted provider state only"};
    } catch (error) { await context.close(); throw error; }
  },
};
