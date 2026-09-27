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
      if (api.larkWrites.length !== 1 || api.larkWrites[0].turn_trigger !== "human_messages") throw new Error("Trigger not saved");
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
      return {coverageEntries: await context.close(), note: "Steward group trigger opt-in, persisted readback and revocation; desktop and narrow screen"};
    } catch (error) { await context.close(); throw error; }
  },
};
